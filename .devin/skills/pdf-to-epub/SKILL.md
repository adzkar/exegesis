---
name: pdf-to-epub
description: Convert a PDF file (including multi-column journal/academic papers) to a reflowable EPUB ebook with a table of contents and embedded images
argument-hint: "<path to PDF> [output path]"
allowed-tools:
  - exec
  - read
  - write
  - glob
permissions:
  allow:
    - Exec(python3 *)
    - Exec(pip3 *)
    - Exec(unzip *)
---

# PDF to EPUB

Convert a PDF to a reflowable EPUB using `pdf_to_epub.py` in this project
(root: the directory containing `.devin/`).

## Setup

Dependencies live in `requirements.txt`. If `python3 -c "import fitz, ebooklib"`
fails, install them first:

```bash
pip3 install -r requirements.txt
```

## Usage

```bash
python3 pdf_to_epub.py <input.pdf> [-o output.epub] [--title "..."] [--author "..."]
```

- Output defaults to the input name with a `.epub` suffix, next to the input.
- Title/author come from PDF metadata, falling back to the filename; override
  with `--title` / `--author`.
- `--keep-headers` disables stripping of page headers/footers (page numbers,
  running heads). Enabled by default for documents over 3 pages.

## How it works

- Text is extracted as positioned blocks via PyMuPDF. Column edges are
  detected document-wide (x0 clusters on ≥50% of pages); gutter-crossing
  blocks are spans, and each column emits its own sequence left to right —
  handles 1/2/3-column layouts.
- Paragraphs are reconstructed: blocks starting flush-left continue the open
  paragraph (merging mid-sentence splits across blocks/columns/pages);
  first-line-indented blocks or blocks after sentence-terminal punctuation
  start a new one.
- Headings: font-size heuristics (h1/h2), all-bold lines/blocks at body
  size, and named section labels (References, Supporting Online Material,
  ...) become the EPUB TOC and chapter splits; the first page-0 heading
  ≥1.3× promotes to h1; chapters begin exactly at their heading.
- Bylines (size-based or right-after-h1 name lists) merge into one group;
  a trailing drop-cap letter relocates into the first paragraph.
- Footnotes (superscript-digit starts, unnumbered gap-separated zones, and
  acknowledgment-opener blocks at column bottom) are moved into a "Notes"
  chapter; superscript markers in the body link to them.
- Furniture is stripped four ways: repeated edge signatures (≥40% of
  pages), pattern matches (VOL/www./ISSN/doi/dates) in the outer 7% edge,
  pure-digit page numbers in edge zones, and narrow+tall margin strips
  (rotated sidebars).
- Numbered lines (N./N)) become list-item paragraphs; vertically isolated
  indented blocks become <blockquote>; rotated tables (tall-narrow block
  clusters) are re-rendered upright and embedded as images.
- Bold/italic come from font-name suffixes (.B/Bold/.I/Italic) when flags
  are absent; embedded raster images are deduplicated and included; tiny
  artifacts (<2 KB) are dropped.
- Title/author metadata is validated (pagination junk rejected) with h1
  heading/byline fallbacks.

## Verify

After converting, sanity-check the output before reporting done:

```bash
unzip -l output.epub          # chapters, images, nav present
```

Spot-check the first chapter's XHTML for correct reading order (especially if
the source is a multi-column paper).

## Limitations

- **No OCR.** If the output EPUB has almost no text, the PDF is scanned images.
  Tell the user — an OCR pass (e.g. tesseract) would be needed as a separate
  step.
- Complex layouts (multi-figure pages, equations, tables) may read
  imperfectly; mention this if the source is heavy on them.
- Degraded PDF copies (jittery font sizes, no embedded bold/italic) yield
  imperfect heading detection; stray OCR artifacts in the source text layer
  are reproduced faithfully.
