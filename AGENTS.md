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

- **Column order** — column edges are x0 clusters found on ≥50% of pages
  (doc-wide voting kills phantom edges from bylines/headings). Blocks
  crossing a gutter are spans; each column emits its own sequence of
  non-spans + covering spans in y order, columns left to right. Works for
  1/2/3-column layouts.
- **Paragraphs** — blocks merge into the open paragraph unless the first line
  is indented (>4pt) or the previous line ends with terminal punctuation.
  Open paragraphs carry across columns and page breaks; a lone drop-cap
  letter fragment is never flushed alone.
- **Headings/TOC** — h1: single-line ≥1.6× body or multi-line block whose
  lines are all ≥1.6×; h2: standalone single-line ≥1.08×, rejecting
  sentence-like text with mid-string periods. Named labels (`References`,
  `Supporting Online Material`, `Methods`…) at block start also emit h2.
  All-caps short labels in the top 12% emit `<p class="label">` instead.
  Chapters split at heading fragments — each begins exactly at its heading.
- **Bylines** — blocks ≥1.15× body, or the block right after an h1 when it
  looks like a name list; merge into one `<p class="byline">` with short
  single-line followers. A trailing drop-cap letter moves into the body
  paragraph. The merged text feeds the author metadata fallback.
- **Headers/footers** — two layers: signatures repeated on ≥40% of pages in
  edge zones, and pattern furniture (`VOL`, `www.`, `ISSN`, `doi`, `©`,
  dates) in the outer 7% edge (works on 1-page docs). Narrow+tall blocks in
  outer 10% margins (rotated sidebars) are dropped.
- **Footnotes** — superscript-digit starts in the lower half, OR an unnumbered
  smaller-than-body block below a >2×line-height gap in the bottom 40%
  (author notes under a rule). Removed from the body flow into a `Notes`
  chapter; in-body superscripts link to `#fnN` (dead links are stripped).
- **Abstract** — first small-font inset block in the top half of pages 0–1
  injects an `Abstract` h2.
- **Styles** — bold via flags or `.B`/`Bold` font-name suffix, italic via
  `.I`/`Italic`/`Oblique` suffix → `<b>`/`<i>` spans.
- **Metadata** — title/author from PDF metadata are validated (must contain
  letters, not pagination junk like `932 932..932`); invalid/missing falls
  back to the first h1 heading and the byline text.

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
