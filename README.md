<p align="center">
  <img src="assets/logo.svg" width="140" alt="pdf-to-markdown logo">
</p>

<h1 align="center">pdf-to-markdown</h1>

<p align="center">
  <em>Turn dense, multi-column PDFs into clean Markdown — without ever
  asking a model to look at a single page.</em>
</p>

<p align="center">
  <img alt="License" src="https://img.shields.io/badge/license-Apache--2.0-blue.svg">
  <img alt="Python" src="https://img.shields.io/badge/python-3.8%2B-blue.svg">
  <img alt="Model calls in conversion path" src="https://img.shields.io/badge/model%20calls%20in%20conversion-0-brightgreen.svg">
  <img alt="Claude Code skill" src="https://img.shields.io/badge/Claude%20Code-skill-5A45FF.svg">
</p>

<p align="center">
  <a href="#why-this-exists">Why</a> ·
  <a href="#what-you-get">What you get</a> ·
  <a href="#performance">Performance</a> ·
  <a href="#features">Features</a> ·
  <a href="#installing-this-skill-in-claude-code">Install</a> ·
  <a href="#usage">Usage</a> ·
  <a href="#limitations">Limitations</a>
</p>

---

It's a Claude Code **skill**: a `SKILL.md` file that tells Claude when and
how to use the bundled script, plus the script itself
(`scripts/pdf_to_markdown.py`). The script is a **deterministic, standalone
Python tool** — no model/LLM involved in the conversion itself. Point it at
a PDF, get real Markdown back: headings, tables, and reading order intact,
for the cost of running Python, not tokens.

## Why this exists

Hand an LLM a PDF and, under the hood, every page usually gets rendered as
an image and read back in as pixels — even when the PDF is nothing but
ordinary text. That's slow, it burns tokens on content that was text all
along, and on a real multi-column layout it still doesn't reliably get the
reading order right, because "read this image top to bottom" and "this
document actually has two columns" are different problems.

This skill started out doing exactly that — an LLM reading each page and
using judgment to structure it — and was rebuilt from the ground up as a
fully deterministic Python script with zero model calls anywhere in the
conversion path. The point was to stop paying image-reading prices for
what is, underneath the formatting, plain text: real extraction from the
PDF's own text layer, column-aware reading order, font-size-based heading
detection, and table reconstruction, all inferred by code instead of read
by a model.

## What you get

- **It's free and instant, not just "cheaper."** The conversion itself
  costs 0 tokens, always — see [Performance](#performance) below for real,
  measured numbers rather than a marketing claim.
- **Every run is reproducible.** Same PDF in, same Markdown out, byte for
  byte (aside from the timestamp) — nothing to second-guess between runs.
- **It remembers what it's already done.** Re-running on an unchanged PDF
  is an instant no-op, so pointing it at a growing folder over and over
  only ever pays for the files that actually changed.
- **Everything stays traceable to its source.** Every heading in the
  Contents block carries both a line number and the PDF page it starts
  on, and the body itself is threaded with page markers throughout — any
  paragraph or table can be traced back to exactly where it came from in
  the original document.
- **It scales to a whole library, not just one document.** Point it at a
  folder — even a deeply nested one — and every PDF converts through one
  shared worker pool at once, instead of waiting on files one at a time.
- **It knows what it doesn't know.** Scanned pages fall back to local OCR
  automatically, and content the heuristics can't confidently structure
  (an irregular table shape, for instance) is left as plain prose instead
  of being guessed at and silently wrong.

## Performance

Measured on two real-world test documents — a couple of data points, not a
rigorous multi-trial benchmark, so treat this as illustrative rather than a
guaranteed ratio. Both columns below are the actual cost of reading the PDF
and producing the Markdown, measured the same way for each: "reads the PDF
directly" is a fresh, isolated agent transcribing the PDF's pages as
images; "uses this skill" is the script itself, timed with `time` on an
**Apple M4, 10 cores** (single-process, `--jobs 1`, the default), averaged
over 3 runs. Wall-clock times are naturally tied to this hardware — slower
or faster on different CPUs — while the token counts and the "0 tokens"
result aren't, since they don't depend on the machine running the script.

| Document | Reads the PDF directly (no skill) | Uses this skill |
|---|---|---|
| 2-page document | ~52,000 tokens / ~193s | 0 tokens / ~1.5s* |
| 20-page document (sample) | ~88,800 tokens / ~436s (~7.3 min) | 0 tokens / ~0.6s |

\* This particular 2-page document's own cover page has no real text layer
(it's essentially a full-page image), so this run includes a real local
Tesseract OCR pass on that one page, not just text extraction — that OCR
pass is most of the ~1.5s. A same-size document with a real text layer on
every page converts faster than this.

> [!IMPORTANT]
> **Honest numbers, not marketing ones.**
> - **0 tokens is exact, not rounded** — the conversion runs entirely in
>   Python (plus local OCR when needed), with no model call anywhere in
>   that path, regardless of document size.
> - **The gap widens on longer documents**, as expected: the script's own
>   cost stays roughly flat (well under a second here) regardless of page
>   count, while reading a PDF directly scales with it, since every
>   additional page is another image to render and read.
> - **The 20-page row is a fresh 20-page sample** (pages 1–20) of the same
>   source document used for the original "no skill" measurement above,
>   not necessarily the exact same slice byte-for-byte — a representative
>   proxy, not a controlled A/B on identical input.
> - **A folder of many PDFs converts even faster per document**, since one
>   shared worker pool serves every file's pages at once (`--jobs N` in
>   folder mode) — see [Usage](#usage) below.

## Features

- **Column-aware reading order** — text lines are clustered into
  left-to-right columns by position, then read top-to-bottom within each,
  instead of the scrambled order you'd get from naively reading a
  two-column PDF top-to-bottom.
- **Heading detection from font size and boldness** — no manual tagging;
  the script infers heading tiers (H1/H2/...) from which font sizes recur
  most often relative to the document's own body text size, plus a
  same-size bold line (e.g. a bold feature name that was never enlarged) —
  common in real PDFs, and something size alone would miss.
- **"Label: value" lines** (`Category: Electronics, Appliances`) become
  `**Label:** value`, including for a handful of common field names that
  some templates render without a colon at all (`Manufacturer`,
  `Warranty`, etc.) — see `KNOWN_NOCOLON_LABELS` in the script if you want
  to extend this list for a different document family.
- **Numbered/lettered reference tables** (`d8 Common Causes` + numbered
  entries) and **level-indexed reference tables** (`Membership Level
  Perks` + `1st free shipping, priority support`, `3rd ...`) both become
  real 2-column Markdown tables. Rows wrapping across multiple source
  lines get rejoined correctly, and a table knows to stop rather than
  absorb whatever comes after it (a new all-caps subsection title ends it,
  not just a heading or a new table). Multi-column numeric progression
  tables are deliberately left as prose rather than guessed at, since
  getting that wrong would misrepresent the data.
- **Contents block with page tracking** — every output starts with a table
  of contents listing every heading, the exact line number it starts on,
  and the source PDF page it starts on, so a long document can be
  navigated without reading it end to end. The body itself carries the
  same page information throughout, via an invisible `<!-- page N -->`
  marker before each page's content — any paragraph or table, not just
  headings, can be traced back to its source page.
- **Built-in memory** — every output's front matter records the source
  PDF's CRC32 checksum. Re-running the script on an unchanged PDF is an
  instant no-op instead of redoing the work; `--force` overrides this.
- **Single file or whole folders**, optionally recursive, with per-file
  failure isolation (one bad PDF doesn't stop a batch).
- **Multi-process speedup** (`--jobs N`) for long documents — splits a
  PDF's pages across N worker processes. Real processes, not threads
  (Python's GIL makes threads useless for this CPU-bound work). Off by
  default; output is byte-identical either way.
- **OCR fallback for scanned pages** — pages with no real text layer at all
  are run through local OCR (Tesseract) automatically, only when needed.
  Pages with real text never pay for this.

## Installing this skill in Claude Code

This repo *is* the skill — Claude Code discovers a skill by finding a
`SKILL.md` at the top of a folder in one of a few specific locations. Put
this whole `pdf-to-markdown/` folder in one of them:

| Scope | Location | Available in |
|---|---|---|
| Personal | `~/.claude/skills/pdf-to-markdown/` | Every project, on this machine only |
| Project | `<your-project>/.claude/skills/pdf-to-markdown/` | Just that project, but shareable via git |

```bash
# Personal (works in any project on this machine)
git clone <this-repo-url> ~/.claude/skills/pdf-to-markdown

# OR project-specific (commit .claude/skills/ so teammates get it too)
git clone <this-repo-url> /path/to/your-project/.claude/skills/pdf-to-markdown
```

Claude Code watches these folders while a session is running, so a skill
added to an *existing* `~/.claude/skills/` or `.claude/skills/` directory is
picked up immediately — no restart needed. The one exception: if
`~/.claude/skills/` doesn't exist yet on your machine and this is the first
skill you're adding, restart Claude Code once after creating it so it
starts watching the new folder.

> [!TIP]
> To confirm it's there, just ask Claude directly — *"what skills are
> available?"* — or use the `/skills` command to browse everything
> installed. Once installed, Claude uses it automatically when it looks
> relevant (per the `description` in `SKILL.md`'s frontmatter) — you don't
> need to invoke it by name, though asking to "convert this PDF to
> Markdown" works too.

> [!NOTE]
> **Cowork / cloud sessions** don't read `~/.claude/skills/` from your
> machine — only project skills committed to the repo (as above) or skills
> enabled for your claude.ai account carry over. If you only need this for
> your local CLI, the personal install above is all you need.

## Requirements

| Purpose | Package | Install |
|---|---|---|
| Core conversion (always required) | [PyMuPDF](https://pypi.org/project/PyMuPDF/) (`fitz`) | `pip install pymupdf` |
| OCR fallback for scanned PDFs (optional) | [pytesseract](https://pypi.org/project/pytesseract/) | `pip install pytesseract` |
| OCR fallback for scanned PDFs (optional) | [Pillow](https://pypi.org/project/Pillow/) | `pip install pillow` |
| OCR fallback for scanned PDFs (optional) | [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) (system binary, not a pip package) | see below |

Python 3.8+ is otherwise all standard library (`re`, `zlib`,
`concurrent.futures`, etc.) — nothing else to install for the core
text-extraction path.

### Installing Tesseract (only needed for scanned/image-only PDFs)

```bash
# macOS (via Homebrew)
brew install tesseract

# Debian/Ubuntu
sudo apt-get install tesseract-ocr

# Windows
# Download the installer from:
# https://github.com/UB-Mannheim/tesseract/wiki
```

Without Tesseract installed, scanned pages simply come out empty and the
script prints a one-line warning to stderr — it doesn't fail the rest of
the conversion.

### One-shot install (everything, including OCR)

```bash
pip install pymupdf pytesseract pillow
brew install tesseract   # or apt-get / the Windows installer above
```

## Uninstalling

Removing the skill itself is just deleting its folder — there's no
installer, registry entry, or system state beyond that:

```bash
rm -rf /path/to/pdf-to-markdown
```

The Python packages and Tesseract are regular system-wide installs, though,
so only remove them if nothing else on the machine depends on them —
`pymupdf`, `pytesseract`, and `pillow` in particular are common general-
purpose libraries other tools may also use.

```bash
# Python packages
pip uninstall pymupdf pytesseract pillow

# Tesseract itself (only needed if you installed it for this skill's OCR
# fallback and nothing else uses it)
brew uninstall tesseract          # macOS
sudo apt-get remove tesseract-ocr # Debian/Ubuntu
# Windows: remove it from "Add or Remove Programs", same as any installer
```

If you're unsure whether something else depends on a package, just leave it
— an unused pip package or Tesseract install sitting on disk doesn't cause
harm, it just takes up some space.

## Usage

**Single file:**
```bash
python3 scripts/pdf_to_markdown.py input.pdf [output.md] [--force] [--jobs N] [--no-ocr]
```
If `output.md` is omitted, it's written next to the input PDF with the same
base name (`manual.pdf` → `manual.md`).

**A whole folder of PDFs:**
```bash
python3 scripts/pdf_to_markdown.py folder/ [output_folder] [--recursive] [--force] [--jobs N] [--no-ocr]
```
Converts every `.pdf` directly inside `folder/`. Without `output_folder`,
each `.md` is written next to its source PDF. With it, all outputs go there
instead, mirroring the subfolder structure. Add `--recursive` to also
descend into subfolders.

### Flags

| Flag | Effect |
|---|---|
| `--force` | Reconvert even if the PDF's CRC32 already matches the existing `.md` |
| `--jobs N` | Split a PDF's pages across N worker processes (default: 1, sequential) |
| `--no-ocr` | Skip the OCR fallback entirely, even for pages with no text layer |

## Output format

```markdown
---
source_pdf: /Users/you/Downloads/manual.pdf
source_crc32: 8758ed3e
converted_at: 2026-07-29T21:33:03
---

## Contents
- Model X200 (line 14, page 1)
  - Specifications (line 26, page 2)
  - Maintenance Schedule (line 30, page 3)

<!-- page 1 -->

# Model X200

This is the reference manual for the Model X200 unit...

<!-- page 2 -->

**Manufacturer:** Acme Corp, Beta Industries
...

| d8 | Common Causes |
|---|---|
| 1 | Worn drive belt or misaligned pulley. |
...
```

The `<!-- page N -->` markers are HTML comments: invisible in a rendered
preview, but there in the raw text so anything reading the file — a person
or a model — can trace any piece of content back to its source PDF page.

## Limitations

- **Text only.** Images are never embedded in the output. A cover page
  whose title/credits are baked into an image rather than real text will
  lose that text (unless OCR recovers it, if the whole page has no text
  layer at all).
- **Heuristics, not comprehension.** Reading order, heading levels, and
  table detection come from font size, position, and text patterns — not
  from understanding the page. They're tuned against real multi-column
  reference documents with numbered lists and label-style fields; unusual
  templates (sidebars, 3+ column spreads, non-standard tables) may not come
  out right. Skim the output against the source before trusting it fully,
  especially the first time you run this on a new document family.
- **OCR is a proxy, not a substitute for real text.** Font-size-based
  heading detection is less reliable on OCR'd pages, since it relies on
  recognized glyph height rather than actual font metadata.
- **A "real" text layer can still come out garbled.** Some PDFs embed a
  decorative or custom font whose glyph-to-Unicode mapping is broken or
  non-standard in the file itself — PyMuPDF (and any other text-extraction
  library) then pulls out the wrong characters even though a text layer
  genuinely exists, no OCR involved. This is rare, affects only the
  specific text styled with that font, and isn't something this script can
  detect or work around; if a section of the output looks like garbage
  characters, check it against the source PDF directly.

See `SKILL.md` for the full design rationale and how Claude Code uses this
skill.
