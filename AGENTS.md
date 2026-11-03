# exegesis

PDF → EPUB converter tuned for multi-column academic/journal papers. Rule-based
layout analysis over PyMuPDF blocks — no OCR, no ML dependencies.

## Commands

- Setup: `pip3 install -r requirements.txt` (pymupdf, ebooklib)
- Convert: `python3 pdf_to_epub.py <input.pdf> [-o output.epub] [--title T] [--author A] [--lang en|de|fr|es] [--keep-headers]`
- Verify output: `unzip -l out.epub` (chapters, notes.xhtml, images, nav present),
  then spot-check a chapter's XHTML for reading order
- Report: `python3 scripts/build_report.py` (converts root `*.pdf` corpus
  → `docs/report/` for GitHub Pages)
- Release: `scripts/release.sh X.Y.Z` (gates on ruff + non-slow pytest; see
  `RELEASE.md`)

## Architecture (`exegesis` package)

`pdf_to_epub.py` is a thin CLI shim; the pipeline lives in the package:
`analyze` (font/column/footnote detection) → `order` (reading order) →
`render` (block classification to HTML fragments) → `emit` (chapter
chunking + ebooklib packaging), with `state` (`PipelineState`), `text`,
`const`, and `cli` alongside.

Single-pass pipeline: per page, positioned text/image blocks are extracted,
reordered into reading order, rendered to HTML fragments, then chunked into
chapters.

- **Column order** — column edges are x0 clusters found on ≥50% of pages
  (doc-wide voting kills phantom edges from bylines/headings; furniture-
  shaped blocks don't vote). Blocks crossing a gutter are spans; each
  column emits its own sequence of non-spans + covering spans in y order,
  columns left to right. Works for 1/2/3-column layouts.
- **Paragraphs** — blocks merge into the open paragraph unless the first line
  is indented (>4pt) or the previous line ends with terminal punctuation.
  Open paragraphs carry across columns and page breaks; a lone drop-cap
  letter fragment is never flushed alone.
- **Headings/TOC** — h1: single-line ≥1.6× body, multi-line block all-lines
  ≥1.6×, or the first page-0 heading ≥1.3× (promoted so title → byline
  position detection fires); h2: standalone ≥1.08× OR all-bold line/block
  ≥0.9× outside edge zones (papers whose headings share the body font) that
  also precedes body-flow text in its column — footnote-zone, rasterized
  table, and kept-furniture blocks don't count (a trailing bold line is
  usually a note, not a heading); a block that is itself a heading
  terminates the scan — a tail before a new section precedes no body.
  The same edge+follower check gates a bold line inside a mixed-weight
  block, and the follower scan is column- and page-local — a bold line
  ending a column or page can't see its next-column/next-page follower
  and stays demoted (a demoted block's size-signaled ≥1.3× lines may
  still emit; only its bold-band lines are silenced). Lines opening
  with footnote markers (`*†‡§`) are never headings.
  Sentence-like text with periods is rejected. Named labels (`References`,
  `Supporting Online Material`…) at block start emit h2, but respect the
  demotion — a trailing `Funding NIH grant…` is a plausible note tail,
  not a section head. All-caps short labels in the top 12% emit
  `<p class="label">` instead. Chapters split at heading fragments; frags
  before the first heading fold into chapter 1 rather than forming a
  degenerate leading chapter. The TOC nests: each h1 opens a section that
  absorbs following h2s (an h2 before the first h1 stays top-level); a
  childless h1 degrades to a bare nav Link — ebooklib emits an invalid
  empty `<ol/>` for childless section tuples — and a headingless, noteless
  document seeds one entry at chapter one so nav/NCX are never empty.
- **Bylines** — blocks ≥1.15× body, or the block right after an h1 when it
  looks like a name list; merge into one `<p class="byline">` with short
  single-line followers. A trailing drop-cap letter (one not in the
  profile's `single_letter_words`) moves into the body paragraph. The merged text feeds the author metadata fallback.
- **Headers/footers** — two layers: signatures repeated on ≥40% of pages in
  edge zones, and pattern furniture (`VOL`, `www.`, `ISSN`, `doi`, `©`,
  dates) in the outer 7% edge (works on 1-page docs). Narrow+tall blocks in
  outer 10% margins (rotated sidebars) are dropped. `--keep-headers` keeps
  all of it flagged `is_furniture` — the signature set is built on any
  doc length so repeated running heads flag the same way: such blocks
  skip the spans/cut/column machinery entirely (emitted at the page's
  top/bottom edge), render verbatim as their own paragraphs, and never
  feed any classifier — headings, abstract, bylines, footnotes, followers,
  blockquote isolation, or table regions (a kept block inside a table's
  bbox still emits verbatim rather than being rasterized away).
- **Footnotes** — superscript-digit starts in the lower half, OR an unnumbered
  smaller-than-body block below a >2×line-height gap in the bottom 40%,
  OR a bottom-55% block opening with an acknowledgment/correspondence
  pattern (`Preparation of`, `Requests for reprints`, `supported by`…).
  Removed from the body flow into a `Notes` chapter; superscripts link to
  `#fnN` and dead links are stripped in body and notes alike (a note's own
  leading marker is always unlinked). Pure-digit blocks in the outer 7%
  edge (page numbers) are dropped.
- **Lists & quotes** — lines starting `N.`/`N)` become `p.listitem`
  paragraphs (absorbing continuation lines); a block indented >12pt from
  its column edge AND vertically isolated (>0.7× line-height gaps on both
  sides) becomes `<blockquote>` (quotes, verse).
- **Tables** — two paths. Rotated/landscape tables (≥3 tall-narrow blocks
  sharing a y-band) are rasterized and embedded as PNGs. `is_furniture`-flagged
  blocks never seed a region, so sidebar furniture kept under
  `--keep-headers` can't be rasterized into a false table. Block bboxes arrive in
  mediabox (unrotated) space; the padded clip is mapped into displayed
  space via `page.rotation_matrix`, because `get_pixmap` clips in rotated
  coordinates — the render comes out upright and uncropped. Upright grid
  tables emit real `<table>` markup: page text lines (rotated ones
  excluded via their reported `dir`, not aspect ratio — a lone '7' cell
  is taller than wide) cluster into y-bands,
  bands split into cells at ≥0.45×line-height x-gaps, and runs of
  multi-cell bands that keep a permanent interior whitespace gutter
  (crossed by no row) become regions. Columns fall out of the surviving
  gutters — only multi-cell bands feed that union, so a lone-cell band
  can never reshape or kill the layout; cells spanning several columns
  emit `colspan`. A narrow lone-cell band joins the run only as a
  wrapped-cell continuation (occupied columns a strict subset of the row
  above's, ≤ max(1, ncols−2), column 0 empty — the same test `_assemble_rows`
  folds on), so a `Note.`/`n.s.` line or prose fragment below the grid
  stays prose instead of surfacing as a fake sparse row. A wide
  lone-cell band is held pending as a colspan candidate — only a
  resuming multi-cell band commits it, a second consecutive wide band
  closes the run (prose filling the gap is not a run of divider rows),
  and a wide band covering no recovered column is never held at all
  (it could claim a block for a cell that can never emit).
  Footnote-region blocks are excluded from grid candidates like
  furniture and rotated cells are, so a note band-joining the grid
  can't be swallowed into cells instead of notes.xhtml. Guards are
  precision-first: ≥3 rows (≥2 when captioned), ≥60% grid-dense rows,
  band/cell fill under prose levels (0.88/0.9), no list-marker first
  column, no image overlap;
  a region spanning a document column edge additionally needs a `Table N`
  caption and gutters that don't just mirror the page's own columns (the
  layout itself is not a table). A band whose text opens with the
  `Table N` label never seeds or joins a run — it is a hard boundary
  reserved for the caption machinery, so a legend can't be mangled into
  cells or swallowed as a wrapped-cell continuation. A `Table N` band
  adjacent to the region becomes `<caption>` — an above-run band wins
  over a below-run one, and a below-run band within the caption window
  of a following run belongs to that table instead (legends sit above
  the grid they label), so a legend between stacked grids can't be
  stolen upward; regions contending for the same caption block resolve
  by ownership — the loser keeps its grid with the caption stripped
  rather than the whole region being discarded — bands made purely of the
  caption block's other lines (a wrapped legend) join it; a legend band
  shared with a foreign block is ambiguous and degrades to prose. A
  block is claimed into a region only
  when every one of its lines lands in a member band — partial coverage
  leaves the whole block in body flow rather than silently dropping the
  un-emitted lines. An all-bold first row becomes `<thead>` `<th>`s.
  Misses degrade to ordinary text flow, never to mangled prose.
- **Caption alt text** — images and rasterized tables adopt the nearest
  text block beyond the region's facing edge as `alt` when it opens with
  a caption label from the language profile (EN:
  `Fig.`/`Figure`/`Table`/`Plate`/`Scheme`/`Chart` +
  number, incl. `S`-prefixed supplementary labels, subfigure letters,
  dash-joined ranges, and 2+-space layout-gap tails — the number core
  and terminator are shared across all three caption regexes so the
  patterns can't drift) within 120pt and shares a column band
  (`_overlap_cols`, so a few points of gutter bleed can't claim both
  columns). The first candidate ends the scan, image blocks break it,
  and a page-shared `claimed` set keeps one caption from alting two
  images — the adopted block still renders in the body flow. Blocks that
  leave the flow can't donate: rasterized cells, footnote-zone blocks,
  and kept furniture are skipped via the page's `nonbody_ids` set (a
  `Figure N.` bound for notes.xhtml or a caption-shaped running head
  must not double as `alt`). Images
  never take `Table N.` (table legends sit above the body, so a
  following one belongs to a text-flowed table); the rotated-table call
  tries the block *above* the region first against the full label set
  and rescues a later column's `Table N.` legend that column-major
  order emits after the cells.
- **Abstract** — first small-font inset block in the top half of pages 0–1
  injects an `Abstract` h2 (title from the language profile's
  `abstract_title`).
- **Language** — `convert(lang=)` / `--lang` selects a `LangProfile`
  (`exegesis/lang.py`: `en` default, `de`, `fr`, `es`) carried on
  `state.lang`; every language-bound scan reads it — furniture content
  patterns, note openers, section labels, the three caption patterns
  (`caption` union, `figure_caption` no-tables, `table_caption`
  tables-only), plus the localized `abstract_title`, `notes_title`,
  byline `name_joiner`, the `affiliation` tail-split for author
  metadata, the `section_fallback` untitled-chapter name, the
  fallback literals `table_alt`/`unknown_author`/`start_title`, and
  the `single_letter_words` drop-cap exclusion set (lone letters that
  are words when sentence-initial — DE's is empty so every lone
  letter fuses; FR a/à/y, ES a/y/o — pre-vocalic allomorphs like
  e/u never open a sentence, so they stay drop-caps) — every
  user-visible string rides the profile so nothing leaks English.
  Unknown codes raise; the API accepts a custom
  `LangProfile` for unsupported languages. Non-Latin scripts (CJK, RTL)
  are out of scope — word shaping, vertical text, and direction
  attributes are unhandled. `section_label` reads its label from capture
  group 1, falling back to the whole match — a custom group-less pattern
  (`r"^Bibliografia\b"`) can't IndexError mid-conversion. In
  `section_label` alternations always list
  longest-first (`Références bibliographiques|Références`, `Literaturverzeichnis|Literatur`) — a shorter arm that fully matches
  leaves a lowercase rest the caller rejects, silently dead-labeling the
  long form. Caption label alternations carry the abbreviated forms
  beside the spelled-out word (`Abb.`, `Fig.`, `Tab.`) — keep the fig
  and table arms in parity: a journal that abbreviates `Abbildung`
  sets `Tabelle` as `Tab.` just as often.
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

## Branches

- Feature/fix work branches from `origin/master` as `feat/<slug>` or
  `fix/<slug>`; deliver through a PR.
- Small doc/tooling tweaks may go straight to `master`.
- `git worktree` works for parallel checkouts, but the gitignored corpus
  (`*.pdf`, `*.epub`) does not travel — copy or symlink it into the
  worktree or golden tests skip and `build_report.py` finds no PDFs.
