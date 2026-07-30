---
name: pdf-to-markdown
description: Converts visually rich, multi-column PDFs (manuals, sourcebooks, structured reference documents, and similar layouts) into a single clean Markdown file, using ONLY a deterministic Python script plus local OCR for scanned pages -- no model reasoning, no reading pages as images, no token cost beyond running the command. IMPORTANT: use this proactively any time you are about to read a PDF file with the Read tool for a document that plausibly matches this style (multi-column layout with headings and reference tables, or a scanned manual), even if the user only asked a question about the PDF's contents rather than asking for a conversion by name -- reading a PDF directly renders every page as an image and burns far more tokens than running this script once and reading the resulting text file. Check first whether a same-named .md file already sits next to the PDF from a previous run before reconverting. Text only: images in the source PDF are never embedded or referenced in the output -- pages with no real text layer (scanned pages) are OCR'd locally via Tesseract instead. If the user instead wants images preserved, or wants the best possible fidelity on a document too irregular for a heuristic script (very unusual layouts, sidebars, non-standard templates), fall back to reading the PDF's pages directly and transcribing it by hand instead of relying on this skill's script.
---

# PDF to Markdown (script-only, zero token cost)

## The point of this skill

Run one script, get one Markdown file. No page-by-page reading, no visual
judgment calls, no per-document prompting -- the entire conversion happens
in `scripts/pdf_to_markdown.py`, which only costs whatever it costs to run
Python. That's the deal the user asked for, and it's worth understanding
what it trades away:

- **Text only.** The script never embeds or references images. A page's own
  illustrations are simply skipped; only text comes out. The one exception
  is pages that have no real text layer at all (scanned/rasterized pages) --
  for those, see the OCR section below.
- **Heuristics, not understanding.** Reading order, heading levels, and
  table detection all come from font size and text patterns, not from
  actually comprehending the page. They're tuned against real multi-column
  reference documents with numbered lists and label-style fields, and hold
  up well there, but an unusual template can still fool them. Skim the
  output against the source once before trusting it fully -- this is a
  "verify, don't just trust" tool, the same as any script you didn't write
  from scratch yourself.

## Before reading any matching PDF directly

Reading a PDF with the Read tool renders every page as an image -- for a
short 2-page document that alone runs tens of thousands of tokens once
you factor in visually checking the layout, and it costs that again on every
future turn that needs the same PDF. This skill's whole purpose is to pay
that cost once, in code, so it never has to happen again: run
`pdf_to_markdown.py` on it (a fraction of a second, no model tokens), then
read the resulting `.md` instead of the PDF.

Do this even when the user didn't explicitly ask for a "conversion" -- if
they ask a question about a PDF's contents and it looks like this kind of
document, converting first and answering from the Markdown is cheaper than
answering from the rendered pages, for this question and every one after it.

You don't need to check by hand whether a conversion already exists and is
current -- just run the script every time. It has its own memory for that
(see below) and does the right thing on its own: instant no-op if nothing
changed, real reconversion if the PDF did, and if you skip straight to the
`.md` file yourself, its Contents block up top tells you it's already there
without needing to run anything.

## Usage

**Single file:**
```bash
python3 scripts/pdf_to_markdown.py <input.pdf> [output.md] [--force] [--jobs N] [--no-ocr]
```
If `output.md` is omitted, the script writes it next to the input PDF with
the same base name (`manual.pdf` -> `manual.md`).

**A whole folder of PDFs:**
```bash
python3 scripts/pdf_to_markdown.py <folder> [output_folder] [--recursive] [--force] [--jobs N] [--no-ocr]
```
Converts every `.pdf` directly inside `<folder>`. Without `output_folder`,
each `.md` is written next to its source PDF (same behavior as single-file
mode, just for every PDF found). With `output_folder`, all outputs go there
instead, mirroring the subfolder structure. Add `--recursive` to also
descend into subfolders -- without it, only PDFs directly in `<folder>` are
converted. One bad PDF doesn't stop the batch: failures are reported per
file to stderr and the rest still run.

Either way, that's the right default unless the user asks for the output
somewhere else -- just run the command and report the path(s), there's
nothing else to do.

## The skill's memory: front matter + CRC32

Every `.md` this script writes opens with a small front-matter block:

```
---
source_pdf: /Users/you/Downloads/manual.pdf
source_crc32: 8758ed3e
source_pages: 3
converted_at: 2026-07-29T21:33:03
---
```

That CRC32 is a checksum of the source PDF's actual bytes -- this is the
skill's memory of "have I already converted this file, and is it still the
same file." Every run recomputes the current PDF's CRC32 and compares it
against what's recorded:

- **Same CRC32** -> the PDF hasn't changed since the last conversion. The
  script skips the rework entirely and just reports the existing path.
  Nothing about the PDF's *name*, *location*, or *modification time* matters
  here -- only its content. Renaming the PDF or touching its mtime doesn't
  trigger a reconversion; editing even one byte of it does.
- **Different CRC32 (or no front matter found)** -> something changed, or
  this `.md` predates this feature, or it's not one of this script's
  outputs at all. Either way, reconvert and overwrite.

Pass `--force` to reconvert regardless -- useful right after you've changed
`pdf_to_markdown.py` itself and want existing outputs regenerated with the
new logic even though the source PDFs haven't changed.

If the user asks what's already been converted, or wants a status check
before running a large batch, read that memory back instead of converting
anything:

```bash
python3 scripts/pdf_to_markdown.py --list-cache <folder> [--recursive]
```

Reports every `.md` in `<folder>` with this front matter -- page count,
last-read (conversion) date, and whether it's still `up to date`, `stale
(PDF changed)`, or has its `source PDF missing` -- pure lookup, zero
conversion work. Also exposed as the `/pdf-to-markdown:list-cache` command.

## Progress output

Both conversion modes print as they run -- which page is being extracted,
which is being converted, then `Wrote: ...` per file -- rather than staying
silent until the whole run finishes. On a large batch this is the signal
that the run is actually progressing, not hung; read it back to the user
if the conversion is taking a while, instead of just waiting on the tool
call with no commentary.

## Speeding up long documents: --jobs N

By default (`--jobs 1`, unchanged) every page is processed one after another
in a single process. Pass `--jobs N` to split a document's pages across N
worker processes instead -- real OS processes, not threads, since Python's
GIL means threads wouldn't actually speed up this CPU-bound text/regex work.
Output is byte-for-byte identical either way (aside from the front matter's
timestamp); only wall-clock time changes. It mainly pays off on longer
documents -- a 2-page sheet isn't worth the process-startup overhead, an
80-page manual is. Leave it at the default unless the user asks for speed or
you're converting something long enough that it matters.

## The Contents block: navigating without reading everything

Right after the front matter, every output with at least one heading gets a
`## Contents` block listing every heading and the exact line number it
starts on in that same file:

```
## Contents
- Model X200 (line 12)
  - Specifications (line 24)
  - Maintenance Schedule (line 28)
```

The point is to avoid reading a long manual end-to-end just to answer a
question about one section. Read the Contents block first (it's tiny), find
the heading you need, and jump straight to it with the Read tool's `offset`
parameter (e.g. `offset: 24` to land right on "Specifications") instead of
reading the whole file from the top.

## Scanned pages: automatic OCR fallback

Some PDFs -- especially scans of physical books, or exports that flattened
every page to an image -- have no real text layer at all: `extract_lines`
comes back empty even though the page clearly has content. For those pages
only, the script automatically falls back to local OCR (Tesseract, via
`pytesseract`), rendering the page to an image and recognizing text from it.
Pages that already have a real text layer never pay for this -- OCR only
kicks in when the fast path genuinely comes up empty.

This still costs zero model tokens -- Tesseract is a different piece of
local software, not a model call. It is slower than reading real text
(seconds per page instead of milliseconds), and its output feeds into the
exact same column/heading/table logic as real text, using each recognized
line's glyph height as a stand-in for font size. It's a proxy, not real font
metadata, so heading detection on OCR'd pages is a little less reliable than
on pages with a real text layer -- skim OCR'd sections a bit more carefully.

This needs Tesseract installed on the machine (`brew install tesseract` on
macOS, `apt-get install tesseract-ocr` on Linux, or the installer at
https://github.com/UB-Mannheim/tesseract/wiki on Windows) plus `pip install
pytesseract pillow`. If Tesseract isn't found, scanned pages just come out
empty and the script prints a one-line hint to stderr rather than failing --
it doesn't block conversion of the rest of a document (or a folder) that
does have real text. Pass `--no-ocr` to skip even trying, e.g. if you know a
document is scanned and want the fast native-only pages processed without
waiting on OCR for the rest.

## How it works, and where it can be wrong

Understanding the mechanics matters here specifically because there's no
model in the loop to catch a bad call -- if the heuristic misreads the
document, the output will be wrong and nothing will flag it. Skim the
generated Markdown against the source PDF before handing it over, especially
on a document you haven't run this on before.

**Reading order**: text lines are grouped into left-to-right columns by
their x-position, then read top-to-bottom within each column. This matches
the common 1-2 column reference-document layout well. A document with an
unusual layout (a full-width callout box interrupting two columns, a
3-column spread, a sidebar) can come out in the wrong order -- the script
has no way to notice this happened, so check a page like that yourself if
the source has one.

**Heading levels**: the script tallies every font size used in the document
that's at least 20% bigger than body text, then ranks candidates by how
*often* each one recurs -- not by raw size -- before assigning the most
common few (H1, H2, ... capped at H4) by size. Frequency comes first
deliberately: a real heading style repeats throughout a document, while a
single freak line (a stray oversized glyph, or -- on OCR'd pages -- one
line whose recognized glyph height came out unusually large) does not, and
picking "biggest raw sizes" instead of "most common qualifying sizes" means
that kind of noise wins over real structure. Detection works per *line*,
not per PDF-internal "block" -- PyMuPDF's own block grouping sometimes
fuses a heading line together with the paragraph text right next to it, and
classifying block-by-block would have inherited the wrong size for one of
them.

Size isn't the only signal, either: on a real text layer (not OCR), a line
that's bold, no smaller than body text, and heading-length also counts as a
heading -- one level deeper than any size-based tier. This catches the
common case of a feature/section name styled bold *without* being enlarged,
which pure size-based detection would otherwise fold into the surrounding
paragraph. It only fires on genuine PDF font metadata (the bold flag PyMuPDF
reports per span, with a font-name check as a fallback for PDFs that don't
set the flag correctly), never on OCR'd pages -- Tesseract doesn't give a
reliable font-weight signal, so OCR'd lines are never treated as bold.

**Label lines** (`Category: Electronics, Appliances`) become
`**Label:** value`. This works generally for anything with a literal colon.
Some templates render certain fields (things like `Manufacturer`,
`Warranty`, `Origin`, `Certification`) in bold *without* a colon in the
underlying text at all -- for those, the script matches against a small
built-in list of common field names (see `KNOWN_NOCOLON_LABELS` in the
script). A custom or unusual field name outside that list just stays plain
paragraph text -- correct content, missing emphasis, never silently wrong.

**Numbered reference tables** (a short header line like `d8 Common Causes`
followed by `1 ...`, `2 ...`, etc.) become a two-column Markdown table.

**Level-indexed reference tables** (`Membership Level Perks` followed by
`1st free shipping, priority support`, `3rd dedicated account manager,
early access`, etc.) become a table the same way. This shape is easy to
confuse with a genuinely multi-column progression table (`Employee Level
Base Salary Bonus Percent Vacation Days`), which this script deliberately
does *not* try to tabulate -- forcing four numeric columns into two would
misrepresent the data. The two are told apart by looking at the row
content, not the header: a real level-indexed list is prose
(comma-separated entries), a progression table's rows are just a few short
numbers, so the header only commits to building a table once the first row
confirms it looks like prose (see `row_looks_like_prose` in the script).
When it doesn't pan out, the header line is left as an ordinary paragraph
instead of guessed at.

Both table kinds handle rows wrapping across more than one extra source
line, and know to stop rather than absorbing whatever comes next: a second
non-row line starting with an all-caps word or run (`WARRANTY TERMS
Coverage begins on the date of purchase...`) is treated as the start of new
content, not a continuation -- exactly the "bold/small-caps section name"
pattern this kind of document constantly reuses for subsection titles,
which is also useful on OCR'd pages where that subheading's recognized size
sometimes doesn't clear the heading threshold either. If you add support
for a new document family with different table or heading conventions,
this is the piece most likely to need a new pattern.

## When this isn't the right call

If the user wants the actual artwork extracted too, or the source PDF has a
layout irregular enough that the heuristics above are likely to misfire
(and getting it right matters more than getting it free), don't force this
script -- read the PDF's pages yourself and transcribe it with judgment
instead. That costs tokens, but it's the honest tradeoff on the other side
of "no model in the loop."
