# pdf-to-markdown

Convert visually rich, multi-column PDFs — manuals, sourcebooks, reference
documents, and similar layouts — into a single clean Markdown file.

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
recognizes common structured patterns (numbered reference tables,
label/value lines), and writes it all out as one Markdown file — for the
cost of running Python, not tokens.

## Performance

Measured on two real-world test documents — a couple of data points, not a
rigorous multi-trial benchmark, so treat this as illustrative rather than a
guaranteed ratio:

| Document | Reads the PDF directly (no skill) | Uses this skill |
|---|---|---|
| 2-page document | ~52,000 tokens / ~193s | ~35,000 tokens / ~30s |
| 20-page document (sample) | ~88,800 tokens / ~436s (~7.3 min) | ~49,200 tokens / ~82s |

A few things worth knowing about these numbers:

- **The gap widens on longer documents**, as expected: ~1.4x more tokens
  without the skill on the 2-page document, ~1.8x more on the 20-page one.
  The script's own cost stays close to zero regardless of page count;
  reading a PDF directly scales with it, since every additional page is
  another image to render and read.
- **Most of the "with skill" token count isn't PDF analysis at all** — it's
  the ordinary overhead of running any agent (reading `SKILL.md`, reporting
  the result back). The conversion itself runs in Python and costs 0
  tokens; the numbers above are largely fixed overhead, not something that
  scales with the document.
- **These numbers came from a fresh, isolated test agent** spun up
  specifically to measure this. Used within an ongoing session instead
  (the normal way), the "with skill" side would be lower still, since that
  fixed overhead is already paid for by the session itself.
- **The 20-page run is a sample from a larger document**, not necessarily
  representative of every document's density of images, columns, or tables
  — actual results will vary by document.

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

To confirm it's there, just ask Claude directly — *"what skills are
available?"* — or use the `/skills` command to browse everything installed.

Once installed, Claude uses it automatically when it looks relevant (per the
`description` in `SKILL.md`'s frontmatter) — you don't need to invoke it by
name, though asking to "convert this PDF to Markdown" works too.

**Note for Cowork / cloud sessions:** these don't read `~/.claude/skills/`
from your machine — only project skills committed to the repo (as above)
or skills enabled for your claude.ai account carry over. If you only need
this for your local CLI, the personal install above is all you need.

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
- Model X200 (line 12)
  - Specifications (line 24)
  - Maintenance Schedule (line 28)

# Model X200

This is the reference manual for the Model X200 unit...

**Manufacturer:** Acme Corp, Beta Industries
...

| d8 | Common Causes |
|---|---|
| 1 | Worn drive belt or misaligned pulley. |
...
```

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
