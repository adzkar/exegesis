# exegesis

PDF → EPUB converter tuned for multi-column academic/journal papers. Rule-based
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
- **Headings/TOC** — h1: single-line ≥1.6× body, multi-line block all-lines
  ≥1.6×, or the first page-0 heading ≥1.3× (promoted so title → byline
  position detection fires); h2: standalone ≥1.08× OR all-bold line/block
  ≥0.9× outside edge zones (papers whose headings share the body font) that
  also precedes body-flow text in its column — footnote-zone and rasterized
  table blocks don't count (a trailing bold line is usually a note, not a
  heading). Lines opening with footnote markers (`*†‡§`) are never headings.
  Sentence-like text with periods is rejected. Named labels (`References`,
  `Supporting Online Material`…) at block start also emit h2. All-caps
  short labels in the top 12% emit `<p class="label">` instead. Chapters
  split at heading fragments — each begins exactly at its heading.
- **Bylines** — blocks ≥1.15× body, or the block right after an h1 when it
  looks like a name list; merge into one `<p class="byline">` with short
  single-line followers. A trailing drop-cap letter moves into the body
  paragraph. The merged text feeds the author metadata fallback.
- **Headers/footers** — two layers: signatures repeated on ≥40% of pages in
  edge zones, and pattern furniture (`VOL`, `www.`, `ISSN`, `doi`, `©`,
  dates) in the outer 7% edge (works on 1-page docs). Narrow+tall blocks in
  outer 10% margins (rotated sidebars) are dropped.
- **Footnotes** — superscript-digit starts in the lower half, OR an unnumbered
  smaller-than-body block below a >2×line-height gap in the bottom 40%,
  OR a bottom-55% block opening with an acknowledgment/correspondence
  pattern (`Preparation of`, `Requests for reprints`, `supported by`…).
  Removed from the body flow into a `Notes` chapter; in-body superscripts
  link to `#fnN` (dead links are stripped). Pure-digit blocks in the outer
  7% edge (page numbers) are dropped.
- **Lists & quotes** — lines starting `N.`/`N)` become `p.listitem`
  paragraphs (absorbing continuation lines); a block indented >12pt from
  its column edge AND vertically isolated (>0.7× line-height gaps on both
  sides) becomes `<blockquote>` (quotes, verse).
- **Tables** — rotated/landscape tables (≥3 tall-narrow blocks sharing a
  y-band) are rasterized and embedded as PNGs. Margin-strip-shaped blocks
  never seed a region, so sidebar furniture kept under `--keep-headers`
  can't be rasterized into a false table. Block bboxes arrive in
  mediabox (unrotated) space; the padded clip is mapped into displayed
  space via `page.rotation_matrix`, because `get_pixmap` clips in rotated
  coordinates — the render comes out upright and uncropped. Horizontal
  tables are not yet detected — their cells currently flow as text.
- **Abstract** — first small-font inset block in the top half of pages 0–1
  injects an `Abstract` h2.
- **Styles** — bold via flags or `.B`/`Bold` font-name suffix, italic via
  `.I`/`Italic`/`Oblique` suffix → `<b>`/`<i>` spans.
- **Metadata** — title/author from PDF metadata are validated (must contain
  letters, not pagination junk like `932 932..932`); invalid/missing falls
  back to the first h1 heading and the byline text (affiliation tail and
  footnote markers stripped from the author).
- **Style** — a minimal `style.css` is embedded (italic bylines, small-caps
  labels, indented list items/quotes, centered figures).

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
