# exegesis

PDF → EPUB converter tuned for two-column academic/journal papers. Rule-based
layout analysis over PyMuPDF blocks — no OCR, no ML dependencies.

## Commands

- Setup: `pip3 install -r requirements.txt` (pymupdf, ebooklib)
- Convert: `python3 pdf_to_epub.py <input.pdf> [-o output.epub] [--title T] [--author A] [--keep-headers]`
- Verify output: `unzip -l out.epub` (chapters, notes.xhtml, images, nav present),
  then spot-check a chapter's XHTML for reading order

## Architecture (`pdf_to_epub.py`)

Single-pass pipeline: per page, positioned text/image blocks are extracted,
reordered into reading order, rendered to HTML fragments, then chunked into
chapters.

- **Column order** — blocks spanning the midline are full-width; the rest form
  left/right segments read left-then-right.
- **Paragraphs** — blocks merge into the open paragraph unless the first line
  is indented (>4pt) or the previous line ends with terminal punctuation.
  Open paragraphs carry across columns and page breaks.
- **Headings/TOC** — large fonts → h1; standalone single-line blocks ≥1.08×
  body → h2 (rejects sentence-like text with mid-string periods). Headings
  drive chapter splits and the TOC.
- **Footnotes** — superscript-digit starts in the lower half, OR an unnumbered
  smaller-than-body block below a >2×line-height gap in the bottom 40%
  (author notes under a rule). Removed from the body flow into a `Notes`
  chapter; in-body superscripts link to `#fnN` (dead links are stripped).
- **Headers/footers** — short texts in page edge zones recurring on ≥40% of
  pages are dropped (normalized, digit-insensitive signatures).
- **Bylines** — blocks ≥1.15× body size that aren't headings emit as own
  `<p class="byline">`, never merged.
- **Abstract** — first small-font inset block on pages 0–1 injects an
  `Abstract` h2.

## Conventions

- Every function carries a docstring explaining its heuristic (the *why*,
  since thresholds are tuned, not principled).
- Heuristics are tuned against scanned-journal PDFs with invisible OCR text
  layers (jittery font sizes, no bold flags, stray artifacts). Prefer
  position/repetition signals over font-style signals.
- Keep it dependency-light: stdlib + pymupdf + ebooklib only. No OCR/ML —
  scanned-image PDFs are a documented limitation.

## Git hygiene

- `*.pdf`, `*.epub`, `__pycache__/` are covered by a **local** `.gitignore`.
- Do NOT commit `.gitignore`, PDFs, or EPUB outputs — stage files explicitly.
