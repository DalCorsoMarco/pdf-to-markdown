---
description: List PDFs in a folder that already have a converted Markdown file, with page count, last-read date, and whether each one is still up to date with its source PDF. Use when the user asks what's already been converted, or wants a status/cache report before running a big batch.
argument-hint: [folder-path] [--recursive]
arguments: folder
allowed-tools: Bash(python3 scripts/pdf_to_markdown.py --list-cache *)
---

Run this skill's cache-listing mode, without converting anything:

```
python3 scripts/pdf_to_markdown.py --list-cache "$folder"
```

Add `--recursive` to the command above if the user's request implies
subfolders too (e.g. "$folder" mentions a top-level library folder, or the
user says "everything in and under").

Report the result as a short table or list: source file, page count, last
read (conversion) date, and status (`up to date`, `stale (PDF changed)`, or
`source PDF missing`). If nothing is cached yet, say so plainly instead of
inventing rows.
