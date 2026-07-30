#!/usr/bin/env python3
"""
Deterministic PDF -> Markdown converter for visually rich, multi-column
documents (manuals, sourcebooks, structured reference documents, and
similar layouts).

This is a pure, self-contained script: no model/LLM is involved in reading
or composing the output. Everything -- column-aware reading order, heading
levels, "Label: value" lines, and dN random/roll tables -- is inferred from
per-line font size, position, and text patterns alone. That trade-off means:

  - It's fast, free, and fully repeatable (same PDF in -> same Markdown out).
  - It only extracts text; images are never embedded in the output. Pages
    with no real text layer at all (scanned pages) are handled via local OCR
    instead (see below) -- but a normal page's own illustrations are always
    just skipped, OCR or not.
  - Heading levels, tables, and label lines are detected with heuristics
    tuned against real-world multi-column reference documents. Unusual
    templates (unlabeled sidebars, 3+ column layouts, unconventional
    tables) may not be recognized correctly -- always skim the output
    against the source before trusting it fully.

Detection works line-by-line, not block-by-block: PyMuPDF's own block
grouping sometimes merges a heading line together with the body text right
before or after it (e.g. a paragraph's last line and the next section's
heading end up in the same "block"). Classifying every line independently
by its own font size avoids inheriting one line's size for its neighbors.

Every output file starts with a small YAML front-matter block recording the
source PDF's path and CRC32 checksum, followed by a "Contents" table of
contents that lists every heading with the line number it starts on. Two
things fall out of that:

  - Re-running the script on a PDF that hasn't changed is a no-op: it
    recomputes the CRC32 (cheap), sees it matches what's recorded in the
    existing .md, and skips the reconversion instead of redoing the work.
    Pass --force to regenerate anyway (e.g. after changing this script).
  - Anything reading the resulting Markdown -- a person or a model -- can
    jump straight to the section it needs via the Contents block instead of
    reading the whole file top to bottom.

Pages with no real text layer (scanned/rasterized pages) fall back to local
OCR via Tesseract (through pytesseract), when it's installed -- still no
model/LLM involved, just a different piece of local software. Pages that
already have real text never pay for this; OCR only kicks in per-page, only
when needed. Recognized words are grouped back into lines with the same
{bbox, text, font_size} shape as real text, so OCR'd pages flow through the
exact same column/heading/table logic. If Tesseract isn't installed, those
pages just come out empty and a one-line hint is printed -- pass --no-ocr to
skip even trying.

Usage:
    python3 pdf_to_markdown.py <input.pdf> [output.md] [--force] [--jobs N] [--no-ocr]
    python3 pdf_to_markdown.py <folder> [output_folder] [--recursive] [--force] [--jobs N] [--no-ocr]

Single-file mode: if output.md is omitted, it is written next to the input
PDF using the same base name (e.g. manual.pdf -> manual.md).

Folder mode (triggers automatically when the argument is a directory):
converts every .pdf found directly inside it. Without output_folder, each
.md is written next to its source PDF; with it, outputs go there instead,
mirroring the subfolder structure. Add --recursive to also descend into
subfolders.

--jobs N (default 1, i.e. single-process/sequential): splits work across N
worker processes instead of handling it one piece at a time. Real
processes, not threads -- Python's GIL means threads wouldn't actually
speed up this CPU-bound text/regex work, so processes are what deliver the
speedup --jobs is for. Output is byte-for-byte identical to --jobs 1
(aside from the front matter's timestamp); only wall-clock time changes.
In single-file mode this splits *that PDF's pages* across N workers, and
mainly pays off on longer documents where per-worker startup overhead is
small relative to the work. In folder mode with more than one PDF found,
every file's page-chunks -- from every PDF found, not just one at a time
-- are queued on the same shared pool of N workers, so a folder of many
PDFs converts concurrently instead of one after another, and one
page-heavy or OCR-heavy file among several small ones still gets most of
the available workers instead of being capped at a fixed per-file share.

OCR fallback: a page with zero extractable text (scanned/rasterized, no
real text layer at all) is rendered to an image and run through local
Tesseract OCR automatically -- pages that already have real text never pay
for this. Requires `pytesseract` + `Pillow` (pip) and the `tesseract`
binary itself (a system install: brew/apt/the Windows installer -- see
README.md). Without them, OCR is silently skipped and those pages come out
empty, same as before this feature existed. Pass --no-ocr to skip it even
when available. OCR text only has bounding boxes, not real font metadata,
so it's a serviceable but noisier proxy for heading detection than a real
text layer -- expect roll tables and paragraphs to still come out right,
but heading levels to occasionally need a manual look on OCR'd pages.
"""
import sys
import os
import re
import io
import math
import shutil
import zlib
import datetime
import functools
from concurrent.futures import ProcessPoolExecutor, as_completed

import fitz  # PyMuPDF

OCR_DPI = 200  # render resolution for OCR fallback; higher is slower but more accurate

TESSERACT_INSTALL_HINT = (
    "Tesseract OCR is not installed, so scanned/image-only pages can't be read.\n"
    "  macOS:   brew install tesseract\n"
    "  Linux:   apt-get install tesseract-ocr  (or your distro's equivalent)\n"
    "  Windows: https://github.com/UB-Mannheim/tesseract/wiki\n"
    "Then: pip install pytesseract pillow"
)

HEADING_SIZE_RATIO = 1.2      # a line's font size must be at least this much
                               # bigger than body text to count as a heading
MAX_HEADING_LEVELS = 4         # cap on how many distinct size tiers become
                               # H1..H4; anything smaller stays body text
COLUMN_GAP_RATIO = 0.08        # x-gap (relative to page width) that splits
                               # text into separate columns

TABLE_HEADER_RE = re.compile(r"^d(\d{1,3})\s+(.+)$")
TABLE_ROW_RE = re.compile(r"^(\d{1,3})\s+(.+)$")

# "Membership Level Perks", "Employee Level Benefits" -- the other common
# reference-table shape, pairing a level/tier column with a short
# one-word-ish category. Deliberately NOT used for multi-column progression
# tables (e.g. "Employee Level Base Salary Bonus Percent Vacation Days"),
# which happen to match this same header shape but need real multi-column
# parsing this script doesn't attempt -- see ROW_LOOKS_LIKE_PROSE_RE below
# for how those get told apart.
LEVEL_TABLE_HEADER_RE = re.compile(r"^([A-Za-z][A-Za-z ]{1,30}?)\s+(?:Level|LEVEL)\s+([A-Za-z][A-Za-z ]{1,30})$")
ORDINAL_ROW_RE = re.compile(r"^(\d{1,2}(?:st|nd|rd|th))\s+(.+)$", re.IGNORECASE)

# A genuine "Level | Spells" row's text is prose (spell names, usually
# comma-separated); a genuine multi-column numeric progression row's text is
# just a handful of short numbers/ordinals with no real words. Used only to
# decide, on the row right after a LEVEL_TABLE_HEADER_RE match, whether to
# commit to building a 2-column table at all -- if it doesn't look like
# prose, the header line is left as plain paragraph text instead, since
# forcing a numeric progression table into 2 columns would misrepresent it.
def row_looks_like_prose(text):
    return "," in text or len(re.findall(r"[A-Za-z]{3,}", text)) >= 2


# A run of 2+ consecutive all-caps words, or one all-caps word of 4+ letters,
# right at the start of a line strongly suggests a new named subsection
# (a bold/small-caps feature name like "EYES OF TWILIGHT" or "SOULSEEING")
# rather than a table row wrapping onto another line -- used to decide when a
# table is actually over, since a wrapped row's continuation is ordinary
# lowercase-led prose. This matters especially for OCR'd pages: this kind of
# subheading is sometimes rendered at the same recognized size as body text,
# so the font-size heading check upstream doesn't always catch it either.
NEW_CONTENT_RE = re.compile(r"^(?:[A-Z]{2,}(?:\s+[A-Z]{2,})+|[A-Z]{4,})(?:\s|$)")


def looks_like_new_content(text):
    return bool(NEW_CONTENT_RE.match(text))


HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$")
FRONT_MATTER_CRC_RE = re.compile(r"^source_crc32:\s*([0-9a-fA-F]+)\s*$", re.MULTILINE)

# Label ends in a colon, on its own line, short enough to be a field name
# rather than a full sentence that happens to contain a colon.
LABEL_COLON_RE = re.compile(r"^([A-Z][A-Za-z0-9 /'\-]{1,40}):\s*(.+)$")

# Common reference-document field names that templates often render
# WITHOUT a trailing colon (styled as bold labels visually, but with no
# colon character in the underlying text at all). This list is a pragmatic
# heuristic, extendable for other document families -- custom or unusual
# field names outside this list simply won't get bolded, which is a safe
# (if less polished) fallback, not a wrong answer.
KNOWN_NOCOLON_LABELS = [
    "Skill Proficiencies", "Tool Proficiencies", "Languages", "Equipment",
    "Saving Throws", "Skills", "Senses", "Damage Resistances",
    "Damage Immunities", "Damage Vulnerabilities", "Condition Immunities",
    "Challenge", "Proficiency Bonus", "Armor Class", "Hit Points", "Speed",
]
KNOWN_NOCOLON_LABELS.sort(key=len, reverse=True)  # match longest first


BOLD_FLAG = 1 << 4  # PyMuPDF span "flags" bit for bold, per its font-flags bitfield
BOLD_NAME_RE = re.compile(r"bold|black|heavy", re.IGNORECASE)


def _span_is_bold(span):
    return bool(span["flags"] & BOLD_FLAG) or bool(BOLD_NAME_RE.search(span.get("font", "")))


def extract_lines(page):
    """Flatten a page into individual text lines, each with its own bbox,
    font size, and whether it's (mostly) bold."""
    lines = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b["lines"]:
            text = "".join(span["text"] for span in line["spans"]).strip()
            if not text:
                continue
            size = max((span["size"] for span in line["spans"]), default=0.0)
            # A line counts as bold if most of its *characters* are bold, not
            # just any single span -- one bolded word inside an otherwise
            # plain sentence shouldn't flip the whole line.
            total_chars = sum(len(span["text"]) for span in line["spans"])
            bold_chars = sum(len(span["text"]) for span in line["spans"] if _span_is_bold(span))
            is_bold = total_chars > 0 and bold_chars / total_chars > 0.5
            lines.append({
                "bbox": line["bbox"],
                "text": text,
                "font_size": round(size, 1),
                "bold": is_bold,
            })
    return lines


@functools.lru_cache(maxsize=1)
def tesseract_available():
    return shutil.which("tesseract") is not None


def ocr_extract_lines(page, dpi=OCR_DPI):
    """Fallback for pages with no real text layer (scanned/rasterized pages):
    render the page to an image and run local OCR (Tesseract, via
    pytesseract), then group recognized words into lines with the same
    {bbox, text, font_size} shape extract_lines() produces -- in PDF point
    coordinates, so OCR'd pages flow through the exact same column/heading/
    table logic as pages with a real text layer. font_size here is a proxy
    (each line's average recognized glyph height), not real font metadata,
    but it plays the same role for heading-tier detection."""
    import pytesseract
    from PIL import Image

    pix = page.get_pixmap(dpi=dpi)
    image = Image.open(io.BytesIO(pix.tobytes("png")))
    scale = dpi / 72.0  # pixels per PDF point, to convert OCR's pixel boxes back

    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)

    groups = {}  # (block_num, par_num, line_num) -> list of word boxes
    for i in range(len(data["text"])):
        word = data["text"][i].strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        groups.setdefault(key, []).append((
            data["left"][i], data["top"][i], data["width"][i], data["height"][i], word,
        ))

    lines = []
    for words in groups.values():
        text = " ".join(w[4] for w in words)
        x0 = min(w[0] for w in words) / scale
        y0 = min(w[1] for w in words) / scale
        x1 = max(w[0] + w[2] for w in words) / scale
        y1 = max(w[1] + w[3] for w in words) / scale
        avg_height = sum(w[3] for w in words) / len(words) / scale
        # Rounded to the nearest whole point, much coarser than the real text
        # layer's round(size, 1) -- OCR's glyph-height proxy is noisy enough
        # that two lines in the exact same visual heading style rarely come
        # back with the same size to one decimal place, which breaks
        # compute_heading_levels' assumption of a few sizes that recur
        # exactly. Whole points collapse that jitter back into a small,
        # usable set of tiers.
        # OCR gives no reliable font-weight signal, so every OCR'd line is
        # "bold": False -- the bold-based heading boost in
        # classify_heading_level only ever applies to a real text layer.
        lines.append({"bbox": (x0, y0, x1, y1), "text": text, "font_size": round(avg_height), "bold": False})
    return lines


def extract_lines_or_ocr(page, use_ocr=True):
    """Try the real text layer first; only fall back to OCR for pages that
    genuinely have none (scanned pages), and only if Tesseract is actually
    available -- OCR is comparatively slow, so pages with real text never
    pay for it."""
    lines = extract_lines(page)
    if lines or not use_ocr or not tesseract_available():
        return lines
    try:
        return ocr_extract_lines(page)
    except Exception as exc:  # noqa: BLE001 - one page's OCR failure shouldn't kill the run
        print(f"OCR failed on a page: {exc}", file=sys.stderr)
        return []


def cluster_columns(lines, page_width, gap_ratio=COLUMN_GAP_RATIO):
    """Group lines into left-to-right columns based on their x0 gaps."""
    if not lines:
        return []
    xs = sorted(set(round(l["bbox"][0]) for l in lines))
    gap_threshold = page_width * gap_ratio
    groups = [[xs[0]]]
    for x in xs[1:]:
        if x - groups[-1][-1] > gap_threshold:
            groups.append([x])
        else:
            groups[-1].append(x)
    col_starts = [min(g) for g in groups]

    columns = [[] for _ in col_starts]
    for l in lines:
        x0 = l["bbox"][0]
        best = min(range(len(col_starts)), key=lambda i: abs(x0 - col_starts[i]))
        columns[best].append(l)

    order = sorted(range(len(col_starts)), key=lambda i: col_starts[i])
    return [columns[i] for i in order]


def compute_body_size(all_lines):
    """Body text size = font size with the most total characters across the doc."""
    char_count = {}
    for l in all_lines:
        char_count[l["font_size"]] = char_count.get(l["font_size"], 0) + len(l["text"])
    if not char_count:
        return 10.0
    return max(char_count, key=char_count.get)


def compute_heading_levels(all_lines, body_size):
    """Pick which font sizes above body size count as headings, and rank them
    into H1/H2/... A real heading style recurs throughout a document; a
    single freak line (OCR noise, a stray oversized glyph) does not -- so
    candidates are chosen by how often they occur, not by raw size, and only
    ranked by size afterward to decide which is H1 vs H2 vs ... This matters
    most for OCR'd pages, where a proxy "font size" (recognized glyph height)
    is a noisy float that rarely repeats exactly for a real text layer's
    handful of true font sizes, but matters just as much there: a document
    with only a couple of real heading sizes isn't hurt by this (there's
    nothing else competing for the top spots either way)."""
    threshold = body_size * HEADING_SIZE_RATIO
    counts = {}
    for l in all_lines:
        if l["font_size"] >= threshold:
            counts[l["font_size"]] = counts.get(l["font_size"], 0) + 1

    by_frequency = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    selected = sorted((size for size, _ in by_frequency[:MAX_HEADING_LEVELS]), reverse=True)
    return {size: i + 1 for i, size in enumerate(selected)}


BOLD_HEADING_MAX_CHARS = 80  # a bold *label* is heading-length; a bold
                              # *paragraph* someone emphasized for emphasis's
                              # sake is not -- length is what tells them apart


def classify_heading_level(line, size_to_level, body_size):
    """Decide a line's heading level from its font size primarily, falling
    back to boldness for lines that are styled as a heading (bold, no
    smaller than body text, and heading-length) without a distinct size of
    their own -- common in real PDFs where a feature name is bold but not
    enlarged. Returns None for body text. Bold-only headings always land one
    level deeper than any size-based tier, since a same-size-as-body bold
    label is the least visually distinct heading style a document is likely
    to use."""
    level = size_to_level.get(line["font_size"])
    if level:
        return level
    if (
        line["bold"]
        and line["font_size"] >= body_size
        and len(line["text"]) <= BOLD_HEADING_MAX_CHARS
    ):
        return max(size_to_level.values(), default=0) + 1
    return None


def match_label_line(line):
    """Return (label, value) if this line is a 'Label: value' or known no-colon field line."""
    m = LABEL_COLON_RE.match(line)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    for label in KNOWN_NOCOLON_LABELS:
        if line.startswith(label + " ") and len(line) > len(label) + 1:
            return label, line[len(label):].strip()
    return None


class TableBuilder:
    def __init__(self, col1_header, col2_header):
        self.col1_header = col1_header
        self.col2_header = col2_header
        self.rows = []  # list of [key, text]

    def start_row(self, key, text):
        self.rows.append([key, text])

    def continue_row(self, text):
        if self.rows:
            self.rows[-1][1] = (self.rows[-1][1] + " " + text).strip()
        else:
            # Continuation text with no row yet -- treat as part of the category name.
            self.col2_header = (self.col2_header + " " + text).strip()

    def to_markdown(self):
        out = [f"| {self.col1_header} | {self.col2_header} |", "|---|---|"]
        for key, text in self.rows:
            out.append(f"| {key} | {text} |")
        return "\n".join(out)


def flush_paragraph(buf, out):
    text = " ".join(buf).strip()
    if text:
        out.append(text)
    buf.clear()


def flush_table(table, out):
    if table is not None and table.rows:
        out.append(table.to_markdown())


def process_body_flow(texts, out):
    """Feed a run of body-text lines (spanning until the next heading in the
    same column) through the paragraph/label/table state machine."""
    para_buf = []
    table = None
    row_re = None
    row_cont_buffer = []  # not-yet-committed continuation line(s) for the
                            # table's current last row -- see below
    pending_label_idx = None  # index in `out` of the last label line emitted,
                                # as long as nothing else has been emitted since
                                # (lets a wrapped label value like "Equipment A
                                # set of ... / containing 10 gp" rejoin instead
                                # of becoming its own stray paragraph)
    pending_level_header = None  # (class_name, category, raw_line) waiting on
                                   # the next line to decide whether this is a
                                   # real "Level | Spells" table or a numeric
                                   # progression table this script shouldn't
                                   # try to tabulate -- see row_looks_like_prose

    def end_table(commit_pending=False):
        # Two different reasons a table ends need two different treatments
        # for whatever's sitting in row_cont_buffer:
        #  - commit_pending=True: we simply ran out of lines (the column/page
        #    ended) with no positive evidence either way -- the buffered
        #    line(s) are the last row's trailing wrap, so merge them in.
        #  - commit_pending=False (default): something positively signaled
        #    the table is over (a new table header, or looks_like_new_content)
        #    -- the buffered line(s) were never part of this table at all,
        #    so hand them back to the paragraph flow instead of losing them.
        nonlocal table, row_re
        if commit_pending and row_cont_buffer and table is not None:
            table.continue_row(" ".join(row_cont_buffer))
            row_cont_buffer.clear()
        flush_table(table, out)
        table = None
        row_re = None
        if row_cont_buffer:
            para_buf.extend(row_cont_buffer)
            row_cont_buffer.clear()

    for text in texts:
        if pending_level_header is not None:
            class_name, category, raw_line = pending_level_header
            pending_level_header = None
            ordinal_match = ORDINAL_ROW_RE.match(text)
            if ordinal_match and row_looks_like_prose(ordinal_match.group(2)):
                flush_paragraph(para_buf, out)
                end_table(commit_pending=True)
                table = TableBuilder(f"{class_name} Level", category)
                row_re = ORDINAL_ROW_RE
                table.start_row(ordinal_match.group(1), ordinal_match.group(2))
                continue
            # Didn't pan out -- the header line was just a normal sentence
            # (or the start of a table this script won't attempt); put it
            # back as plain text and fall through to process `text` normally.
            pending_label_idx = None
            para_buf.append(raw_line)

        header_match = TABLE_HEADER_RE.match(text)
        if header_match:
            flush_paragraph(para_buf, out)
            end_table(commit_pending=True)
            table = TableBuilder(f"d{header_match.group(1)}", header_match.group(2))
            row_re = TABLE_ROW_RE
            pending_label_idx = None
            continue

        level_header_match = LEVEL_TABLE_HEADER_RE.match(text)
        if level_header_match and table is None:
            pending_level_header = (
                level_header_match.group(1).strip(),
                level_header_match.group(2).strip(),
                text,
            )
            continue

        if table is not None:
            row_match = row_re.match(text)
            if row_match:
                if row_cont_buffer:
                    table.continue_row(" ".join(row_cont_buffer))
                    row_cont_buffer.clear()
                table.start_row(row_match.group(1), row_match.group(2))
                continue
            if looks_like_new_content(text):
                # Not a wrapped continuation -- a new subsection has started
                # right where the table left off, with nothing in between to
                # have already ended it (no heading-sized line, no new table
                # header). End the table now rather than absorbing this.
                end_table()
                para_buf.append(text)
                continue
            # Might be a genuine wrapped continuation of the last row -- hold
            # it rather than committing it yet. A row can wrap across more
            # than one extra line, so there's no fixed cap here; it keeps
            # buffering until either a new row confirms the table is still
            # going (committing everything buffered so far into that row) or
            # looks_like_new_content proves it wasn't a continuation at all.
            row_cont_buffer.append(text)
            continue

        label_match = match_label_line(text)
        if label_match:
            flush_paragraph(para_buf, out)
            label, value = label_match
            out.append(f"**{label}:** {value}")
            pending_label_idx = len(out) - 1
            continue

        if pending_label_idx is not None and not para_buf:
            out[pending_label_idx] += " " + text
            continue

        pending_label_idx = None
        para_buf.append(text)

    if pending_level_header is not None:
        para_buf.append(pending_level_header[2])

    end_table(commit_pending=True)
    flush_paragraph(para_buf, out)


def convert_lines_to_markdown(lines, page_width, size_to_level, body_size):
    """Turn one page's already-extracted lines into its share of markdown
    chunks. Pure data in, pure data out -- no fitz object involved -- so this
    can run in a worker process for --jobs > 1."""
    columns = cluster_columns(lines, page_width)

    out = []
    for col in columns:
        col_sorted = sorted(col, key=lambda l: l["bbox"][1])
        body_run = []
        for line in col_sorted:
            level = classify_heading_level(line, size_to_level, body_size)
            if level:
                process_body_flow(body_run, out)
                body_run = []
                out.append(f"{'#' * level} {line['text']}")
            else:
                body_run.append(line["text"])
        process_body_flow(body_run, out)
    return out


def _extract_page_range_worker(args):
    """Runs in a worker process: open the PDF independently (fitz page/doc
    objects can't cross process boundaries) and extract just this slice of
    pages, returning plain picklable data."""
    pdf_path, start, end, use_ocr = args
    doc = fitz.open(pdf_path)
    return [(extract_lines_or_ocr(doc[i], use_ocr), doc[i].rect.width) for i in range(start, end)]


def _extract_all_parallel(ex, pdf_path, page_count, jobs, use_ocr=True):
    chunk_size = math.ceil(page_count / jobs)
    ranges = [
        (pdf_path, start, min(start + chunk_size, page_count), use_ocr)
        for start in range(0, page_count, chunk_size)
    ]
    page_data = []
    for chunk_result in ex.map(_extract_page_range_worker, ranges):
        page_data.extend(chunk_result)
    return page_data


def _convert_page_worker(args):
    lines, width, size_to_level, body_size = args
    return convert_lines_to_markdown(lines, width, size_to_level, body_size)


def _convert_all_parallel(ex, page_data, size_to_level, body_size):
    args = [(lines, width, size_to_level, body_size) for lines, width in page_data]
    return list(ex.map(_convert_page_worker, args))


def _extract_all_parallel_multi(ex, jobs, specs):
    """Folder-mode counterpart of _extract_all_parallel: specs is a list of
    (pdf_path, page_count, use_ocr) for *several* files, and every file's
    page-range chunks are submitted to the same shared pool `ex` instead of
    each file getting its own separate pool. That's what lets one page-heavy
    or OCR-heavy file among several small ones still soak up most of the
    available workers -- the small files' handful of chunks finish almost
    immediately and free their workers for whichever file still has chunks
    queued, rather than every file being capped at (jobs / file_count)
    workers regardless of how much work it actually has.
    Returns {pdf_path: page_data}, each page_data in original page order."""
    futures = {}
    for pdf_path, page_count, use_ocr in specs:
        file_chunks = max(1, min(jobs, page_count))
        chunk_size = math.ceil(page_count / file_chunks)
        for start in range(0, page_count, chunk_size):
            end = min(start + chunk_size, page_count)
            fut = ex.submit(_extract_page_range_worker, (pdf_path, start, end, use_ocr))
            futures[fut] = (pdf_path, start)

    chunks_by_file = {pdf_path: [] for pdf_path, _, _ in specs}
    for fut in as_completed(futures):
        pdf_path, start = futures[fut]
        chunks_by_file[pdf_path].append((start, fut.result()))

    page_data_by_file = {}
    for pdf_path, chunks in chunks_by_file.items():
        chunks.sort(key=lambda sc: sc[0])  # completion order != page order
        page_data_by_file[pdf_path] = [line for _, chunk in chunks for line in chunk]
    return page_data_by_file


def _convert_all_parallel_multi(ex, page_data_by_file, stats_by_file):
    """Folder-mode counterpart of _convert_all_parallel: every file's
    per-page conversion tasks share the same pool `ex`, for the same reason
    extraction does above. stats_by_file is {pdf_path: (body_size,
    size_to_level)}. Returns {pdf_path: [markdown chunks per page, in order]}."""
    futures = {}
    for pdf_path, page_data in page_data_by_file.items():
        body_size, size_to_level = stats_by_file[pdf_path]
        for idx, (lines, width) in enumerate(page_data):
            fut = ex.submit(_convert_page_worker, (lines, width, size_to_level, body_size))
            futures[fut] = (pdf_path, idx)

    chunks_by_file = {pdf_path: [None] * len(page_data) for pdf_path, page_data in page_data_by_file.items()}
    for fut in as_completed(futures):
        pdf_path, idx = futures[fut]
        chunks_by_file[pdf_path][idx] = fut.result()
    return chunks_by_file


def compute_crc32(path):
    crc = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 16)
            if not chunk:
                break
            crc = zlib.crc32(chunk, crc)
    return format(crc & 0xFFFFFFFF, "08x")


def read_existing_crc32(md_path):
    """Peek at an existing output file's front matter for its recorded CRC32,
    without reading the whole (possibly large) file."""
    if not os.path.exists(md_path):
        return None
    try:
        with open(md_path, "r", errors="ignore") as f:
            head = f.read(2000)
    except OSError:
        return None
    if not head.startswith("---"):
        return None
    end = head.find("---", 3)
    if end == -1:
        return None
    m = FRONT_MATTER_CRC_RE.search(head[:end])
    return m.group(1) if m else None


def build_document(doc_out, pdf_path, crc32):
    """Assemble front matter + a line-numbered Contents block + the body."""
    front_matter = (
        "---\n"
        f"source_pdf: {os.path.abspath(pdf_path)}\n"
        f"source_crc32: {crc32}\n"
        f"converted_at: {datetime.datetime.now().isoformat(timespec='seconds')}\n"
        "---"
    )

    headings = []  # (level, text, line_within_body)
    line_no = 1
    for chunk in doc_out:
        m = HEADING_RE.match(chunk)
        if m:
            headings.append((len(m.group(1)), m.group(2), line_no))
        line_no += chunk.count("\n") + 1 + 1  # chunk's own lines + trailing blank separator

    body_text = "\n\n".join(doc_out)

    if not headings:
        return front_matter + "\n\n" + body_text + "\n"

    # The body's line numbers above assume the body starts at line 1; once we
    # know how many lines the front matter + Contents block occupy, shift
    # every heading's recorded line by that fixed amount.
    toc_entry_count = len(headings)
    toc_line_count = 1 + toc_entry_count  # "## Contents" + one bullet per heading
    front_matter_line_count = front_matter.count("\n") + 1
    # front matter, blank, TOC (toc_line_count lines), blank, then body starts.
    offset = front_matter_line_count + 1 + toc_line_count + 1

    toc_lines = ["## Contents"]
    for level, text, pos in headings:
        indent = "  " * (level - 1)
        toc_lines.append(f"{indent}- {text} (line {pos + offset})")
    toc_text = "\n".join(toc_lines)

    return front_matter + "\n\n" + toc_text + "\n\n" + body_text + "\n"


def _warn_if_ocr_unavailable(pdf_path, page_count, use_ocr, page_data):
    if use_ocr and page_count > 0 and not tesseract_available() and not any(lines for lines, _ in page_data):
        print(f"{pdf_path}: {TESSERACT_INSTALL_HINT}", file=sys.stderr)


def _compute_global_stats(page_data):
    all_lines = [l for lines, _ in page_data for l in lines]
    body_size = compute_body_size(all_lines)
    size_to_level = compute_heading_levels(all_lines, body_size)
    return body_size, size_to_level


def convert(pdf_path, out_path=None, force=False, jobs=1, use_ocr=True):
    if out_path is None:
        out_path = os.path.splitext(pdf_path)[0] + ".md"

    crc32 = compute_crc32(pdf_path)
    if not force and read_existing_crc32(out_path) == crc32:
        return out_path, False  # unchanged since last run -- nothing to do

    doc = fitz.open(pdf_path)
    page_count = len(doc)

    parallel = jobs > 1 and page_count > 1
    if parallel:
        # One pool serves both the extract and convert phases below, instead
        # of spinning up and tearing down a separate ProcessPoolExecutor for
        # each -- halves the worker-process fork/spawn overhead per run.
        # Re-opens the PDF once per worker process instead of reusing `doc` --
        # fitz page/document objects aren't picklable, so each worker gets its
        # own handle onto its slice of pages.
        jobs = max(1, min(jobs, page_count))
        with ProcessPoolExecutor(max_workers=jobs) as ex:
            page_data = _extract_all_parallel(ex, pdf_path, page_count, jobs, use_ocr=use_ocr)
            _warn_if_ocr_unavailable(pdf_path, page_count, use_ocr, page_data)
            body_size, size_to_level = _compute_global_stats(page_data)
            per_page_chunks = _convert_all_parallel(ex, page_data, size_to_level, body_size)
    else:
        page_data = [(extract_lines_or_ocr(page, use_ocr), page.rect.width) for page in doc]
        _warn_if_ocr_unavailable(pdf_path, page_count, use_ocr, page_data)
        body_size, size_to_level = _compute_global_stats(page_data)
        per_page_chunks = [
            convert_lines_to_markdown(lines, width, size_to_level, body_size)
            for lines, width in page_data
        ]

    doc_out = []
    for chunks in per_page_chunks:
        doc_out.extend(chunks)

    _write_markdown(doc_out, pdf_path, crc32, out_path)
    return out_path, True


def _write_markdown(doc_out, pdf_path, crc32, out_path):
    markdown = build_document(doc_out, pdf_path, crc32)
    with open(out_path, "w") as f:
        f.write(markdown)


def find_pdfs(folder, recursive=False):
    if recursive:
        matches = []
        for root, _dirs, files in os.walk(folder):
            for name in files:
                if name.lower().endswith(".pdf"):
                    matches.append(os.path.join(root, name))
        return sorted(matches)
    return sorted(
        os.path.join(folder, name)
        for name in os.listdir(folder)
        if name.lower().endswith(".pdf")
    )


def _convert_folder_shared_pool(tasks, force, jobs, use_ocr):
    """Converts several PDFs through one shared worker pool instead of each
    file getting its own (see _extract_all_parallel_multi for why): files
    already up to date are resolved first, without touching the pool at
    all, then every remaining file's page-chunks -- extraction and
    conversion alike -- are submitted to the same pool so a single
    page-heavy or OCR-heavy file among several small ones still gets most
    of the available workers instead of being capped at its own fixed
    share."""
    pending = []  # (pdf_path, out_path, crc32, page_count)
    for pdf_path, out_path in tasks:
        resolved_out = out_path or (os.path.splitext(pdf_path)[0] + ".md")
        crc32 = compute_crc32(pdf_path)
        if not force and read_existing_crc32(resolved_out) == crc32:
            print(f"Up to date, skipped: {resolved_out}")
            continue
        page_count = fitz.open(pdf_path).page_count
        pending.append((pdf_path, resolved_out, crc32, page_count))

    if not pending:
        return

    with ProcessPoolExecutor(max_workers=jobs) as ex:
        specs = [(pdf_path, page_count, use_ocr) for pdf_path, _, _, page_count in pending]
        page_data_by_file = _extract_all_parallel_multi(ex, jobs, specs)

        stats_by_file = {}
        for pdf_path, _, _, page_count in pending:
            page_data = page_data_by_file[pdf_path]
            _warn_if_ocr_unavailable(pdf_path, page_count, use_ocr, page_data)
            stats_by_file[pdf_path] = _compute_global_stats(page_data)

        chunks_by_file = _convert_all_parallel_multi(ex, page_data_by_file, stats_by_file)

    for pdf_path, out_path, crc32, _ in pending:
        doc_out = []
        for chunk in chunks_by_file[pdf_path]:
            doc_out.extend(chunk)
        try:
            _write_markdown(doc_out, pdf_path, crc32, out_path)
            print(f"Wrote: {out_path}")
        except OSError as exc:
            print(f"FAILED on {pdf_path}: {exc}", file=sys.stderr)


def convert_folder(folder, out_dir=None, recursive=False, force=False, jobs=1, use_ocr=True):
    pdfs = find_pdfs(folder, recursive=recursive)
    if not pdfs:
        print(f"No PDF files found in {folder}", file=sys.stderr)
        return

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)

    tasks = []
    for pdf_path in pdfs:
        if out_dir:
            rel = os.path.relpath(pdf_path, folder)
            out_path = os.path.join(out_dir, os.path.splitext(rel)[0] + ".md")
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
        else:
            out_path = None
        tasks.append((pdf_path, out_path))

    if jobs > 1 and len(tasks) > 1:
        _convert_folder_shared_pool(tasks, force, jobs, use_ocr)
    else:
        # A single PDF, or --jobs 1: no benefit from a shared multi-file
        # pool, so fall back to this one file's own page-level splitting.
        for pdf_path, out_path in tasks:
            try:
                result, written = convert(pdf_path, out_path, force=force, jobs=jobs, use_ocr=use_ocr)
                print(f"{'Wrote' if written else 'Up to date, skipped'}: {result}")
            except Exception as exc:  # noqa: BLE001 - one bad PDF shouldn't stop the batch
                print(f"FAILED on {pdf_path}: {exc}", file=sys.stderr)


def main():
    raw = sys.argv[1:]
    recursive = "--recursive" in raw
    force = "--force" in raw
    use_ocr = "--no-ocr" not in raw

    jobs = 1
    if "--jobs" in raw:
        idx = raw.index("--jobs")
        try:
            jobs = max(1, int(raw[idx + 1]))
        except (IndexError, ValueError):
            print("--jobs requires an integer, e.g. --jobs 4", file=sys.stderr)
            sys.exit(1)

    positional = []
    skip_next = False
    for a in raw:
        if skip_next:
            skip_next = False
            continue
        if a in ("--recursive", "--force", "--no-ocr"):
            continue
        if a == "--jobs":
            skip_next = True
            continue
        positional.append(a)

    if len(positional) not in (1, 2):
        print(
            "Usage:\n"
            "  Single file:  pdf_to_markdown.py <input.pdf> [output.md] [--force] [--jobs N] [--no-ocr]\n"
            "  Whole folder: pdf_to_markdown.py <folder> [output_folder] [--recursive] [--force] [--jobs N] [--no-ocr]\n"
            "                (writes each <name>.md next to its source PDF if\n"
            "                 output_folder is omitted; --recursive also descends\n"
            "                 into subfolders; --force reconverts even if the PDF's\n"
            "                 CRC32 already matches the existing .md; --jobs N splits\n"
            "                 a PDF's pages across N worker processes, default 1;\n"
            "                 --no-ocr disables the OCR fallback for scanned pages)",
            file=sys.stderr,
        )
        sys.exit(1)

    input_path = positional[0]
    second = positional[1] if len(positional) == 2 else None

    if os.path.isdir(input_path):
        convert_folder(input_path, out_dir=second, recursive=recursive, force=force, jobs=jobs, use_ocr=use_ocr)
    else:
        result, written = convert(input_path, second, force=force, jobs=jobs, use_ocr=use_ocr)
        print(f"{'Wrote' if written else 'Up to date, skipped'}: {result}")


if __name__ == "__main__":
    main()
