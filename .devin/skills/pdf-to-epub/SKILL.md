---
name: pdf-to-epub
description: Convert a PDF file (including two-column journal/academic papers) to a reflowable EPUB ebook with a table of contents and embedded images
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

- Text is extracted as positioned blocks via PyMuPDF. Blocks spanning the page
  midline are full-width; the rest are segmented into left/right columns and
  emitted left-column-then-right, so two-column journals read correctly.
- Paragraphs are reconstructed: blocks starting flush-left continue the open
  paragraph (merging mid-sentence splits across blocks/columns/pages);
  first-line-indented blocks or blocks after sentence-terminal punctuation
  start a new one.
- Headings are detected by font size relative to the modal body size (large
  fonts => h1; standalone single-line blocks modestly larger => h2) and
  become the EPUB TOC and chapter splits.
- Footnotes (blocks opening with a superscript digit in the bottom of a
  column) are moved out of the body flow into a "Notes" chapter; superscript
  markers in the body link to them.
- Embedded raster images are deduplicated and included; tiny artifacts
  (<2 KB) are dropped.

## Verify

After converting, sanity-check the output before reporting done:

```bash
unzip -l output.epub          # chapters, images, nav present
```

Spot-check the first chapter's XHTML for correct reading order (especially if
the source is a two-column paper).

## Limitations

- **No OCR.** If the output EPUB has almost no text, the PDF is scanned images.
  Tell the user — an OCR pass (e.g. tesseract) would be needed as a separate
  step.
- Complex layouts (multi-figure pages, equations, tables) may read
  imperfectly; mention this if the source is heavy on them.
- Degraded PDF copies (jittery font sizes, no embedded bold/italic) yield
  imperfect heading detection; stray OCR artifacts in the source text layer
  are reproduced faithfully.
