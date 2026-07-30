# pdf-to-markdown

Convert visually rich, multi-column PDFs — D&D/TTRPG manuals, homebrew
sourcebooks, Homebrewery-style character sheets, and similar layouts — into
a single clean Markdown file.

It's a Claude Code **skill**: a `SKILL.md` file that tells Claude when and
how to use the bundled script, plus the script itself
(`scripts/pdf_to_markdown.py`). The script is a **deterministic, standalone
Python tool** — no model/LLM involved in the conversion itself. Point it at
a PDF, get a `.md` file back.

## Why

Reading a PDF directly (e.g. with an LLM's built-in file tools) renders
every page as an image, which is slow and expensive if you just want the
text. This script extracts real text from the PDF's own text layer,
reconstructs reading order across columns, detects headings from font size,
recognizes common TTRPG patterns (roll tables, stat-block label lines), and
writes it all out as one Markdown file — for the cost of running Python,
not tokens.

## Features

- **Column-aware reading order** — text lines are clustered into
  left-to-right columns by position, then read top-to-bottom within each,
  instead of the scrambled order you'd get from naively reading a
  two-column PDF top-to-bottom.
- **Heading detection from font size** — no manual tagging; the script
  infers heading tiers (H1/H2/...) from which font sizes are used and how
  often, relative to the document's own body text size.
- **"Label: value" lines** (`Skill Proficiencies: Persuasion, Insight`)
  become `**Label:** value`, including for a handful of common D&D 5e field
  names that templates render without a colon at all (`Languages`,
  `Equipment`, etc.).
- **Roll/random tables** (`d8 Personality Trait` + numbered entries) and
  **class/level spell tables** (`Cleric Level Spells` + `1st sleep, cause
  fear`, `3rd ...`) both become real 2-column Markdown tables. Rows wrapping
  across multiple source lines get rejoined correctly, and a table knows to
  stop rather than absorb whatever comes after it (a new all-caps
  subsection title ends it, not just a heading or a new table). Multi-column
  numeric progression tables are deliberately left as prose rather than
  guessed at, since getting that wrong would misrepresent the data.
- **Contents block** — every output starts with a table of contents listing
  every heading and the exact line number it starts on, so a long document
  can be navigated without reading it end to end.
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
- Baker (line 12)
  - Feature: Baking insight (line 24)
  - Suggested Characteristics (line 28)

# Baker

You have spent your life practicing and perfecting the art of baking...

**Skill Proficiencies:** Persuasion, Insight
...

| d8 | Personality Trait |
|---|---|
| 1 | I am very meticulous about my work and pay attention to detail. |
...
```

## Limitations

- **Text only.** Images are never embedded in the output. A cover page
  whose title/credits are baked into an image rather than real text will
  lose that text (unless OCR recovers it, if the whole page has no text
  layer at all).
- **Heuristics, not comprehension.** Reading order, heading levels, and
  table detection come from font size, position, and text patterns — not
  from understanding the page. They're tuned against real Homebrewery/5e
  homebrew layouts; unusual templates (sidebars, 3+ column spreads,
  non-standard random tables) may not come out right. Skim the output
  against the source before trusting it fully, especially the first time
  you run this on a new document family.
- **OCR is a proxy, not a substitute for real text.** Font-size-based
  heading detection is less reliable on OCR'd pages, since it relies on
  recognized glyph height rather than actual font metadata.

See `SKILL.md` for the full design rationale and how Claude Code uses this
skill.
