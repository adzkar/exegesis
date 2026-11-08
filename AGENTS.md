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
- Eval: `python3 scripts/eval_epub.py <input.pdf> <output.epub> [--lang en|de|fr|es]` —
  severity-ranked quality report; exits 1 on High findings (CI gate),
  0 clean, 2 on operational failure or an unsupported language.
  Language-bound patterns ride the `LangProfile` from `--lang`,
  else `dc:language`, else `en`
- Release: `scripts/bump.sh X.Y.Z` then `scripts/release.sh X.Y.Z`
  (prep + tag/publish; see `RELEASE.md`)
- Tests: `python3 -m pytest -q` (self-contained tests run without local PDFs;
  corpus goldens skip when absent). Lint: `python3 -m ruff check .`.
- Layout gates: `python3 -m pytest -q tests/test_layout_baselines.py tests/test_layout_furniture.py -rx`.
  Extraction contracts and conversion/lint tests must pass independently;
  only `SemanticMismatch` may satisfy a strict semantic xfail. Use
  `helpers.chapter_body_text` for ordering checks: the older `chapter_text`
  includes XHTML head/title text, which can duplicate a heading before
  actual preceding body content. Required content loss/duplication raises
  an ordinary assertion, never an expected ordering mismatch.
- Layout survey: `python3 dev/diagnose_layouts.py [name-substring ...]`
  reads the optional gitignored `journals/` corpus recursively; use
  `--journals-dir DIR` for a corpus outside the checkout. Tests never import
  the script; `tests/test_diagnose_layouts.py` smoke-tests its CLI with
  generated PDFs. Its temporary profiling hook follows real render claims
  and `_reading_columns`'s evidence locals; update the instrumentation
  contract when those locals change. The script
  runs on `requirements.txt` alone — test helpers it borrows (e.g.
  `tests/helpers.py`) keep test-only imports like `pytest` call-time, not
  module-level.

## Architecture (`exegesis` package)

`pdf_to_epub.py` is a thin CLI shim; the pipeline lives in the package:
`analyze` (font/column/footnote detection) → `order` (reading order) →
`render` (block classification to HTML fragments) → `emit` (chapter
chunking + ebooklib packaging), with `state` (`PipelineState`), `text`,
`const`, and `cli` alongside.

Single-pass pipeline: per page, positioned text/image blocks are extracted,
reordered into reading order, rendered to HTML fragments, then chunked into
chapters. `_extract_pages` strips every character outside the XML 1.0
Char production (C0 minus `\t\n\r`, lone surrogates, U+FFFE/U+FFFF) from
every span in the same pass — a newsstand magazine's BEL layout marks
otherwise crash `ebooklib`/`lxml` at item-title setting (#56) — and
`convert` rebinds `title`/`author` through the same sanitize before the
item boundary, so the seeded nav `Link`, the `dc:` metadata, and the
stats payload all read the clean values (#58). The same pass drops
spans painted invisibly — near-white fill (per-channel floor, so
saturated colors survive) on a light rendered ground (`_light_ground`
medians the page pixmap under the span bbox, so white on a dark banner
stays) — publishers' invisible placeholders (PLOS's `a111…` runs)
otherwise emit as prose (#63); emptied lines and blocks go with them.
`eval_epub.py` runs the same helper in `_page_ctx` so the dropped words
excuse as expected loss, not genuine word loss.

- **Column order** — column edges are x0 clusters found on ≥50% of pages
  (doc-wide voting kills phantom edges from bylines/headings; furniture-
  shaped blocks don't vote). Wrap strips beside an in-column image don't
  vote either: `analyze._wrap_fragments` claims eligible blocks that sit
  on both sides of a non-trivial image inside a band some eligible block
  encloses horizontally *and* adjoins vertically — the resume/opening
  prose that proves one logical column (a distant full-width span like a
  title or abstract horizontally encloses any strip pair, so the cover
  must sit within `WRAP_COVER_GAP` of the band before it proves
  anything) — and only the side away from the enclosing x0 is
  withheld, so the strip's second x0 can't seed a phantom column (#38;
  an edge-flush float with text on one side is deliberately not claimed).
  A strip whose x0 is shared by blocks flowing past the band keeps its
  vote either way: that column continues above or below the float, the
  genuine second-column case the cover test alone can't rule out once a
  spanning head sits close to the band.
  Blocks crossing a gutter are spans; each
  column emits its own sequence of non-spans + covering spans in y order,
  columns left to right. Works for 1/2/3-column layouts.
  The committed doc grid is only the *prior*: `analyze._page_grids`
  reruns the same clustering per page on the same eligible items and
  a page orders on its own grid when the candidate clears the
  `_strong_grid` gate — every proposed column needs ≥2 non-spanning
  text blocks whose y-spread exceeds `ZONE_COL_MIN_SPREAD`×line_h,
  whose support's y-union covers ≥`ZONE_COL_MIN_COVER` of the page's
  text extent (a real column flows down the page; an author grid or
  formula fragment covers a few bands), median x-extent
  ≥`ZONE_COL_MIN_WIDTH`×body_size, and ≤`ZONE_GUTTER_GRAZE` of claimed
  blocks grazing within `ZONE_GUTTER_TOL` of the next edge (table cells
  and indent clusters ride through the proposed gutter; graze counts
  spans — the 12pt span window strips edge-reachers from support, so a
  support-only measure could never see one). A single-edge
  candidate always wins its page — one column cannot zipper, and its
  edge still beats the prior for geometry — while a weak candidate
  falls back to the doc grid (`grid_sources`: `page` / `doc` /
  `doc-weak` / `doc-none`; mirrored-margin journals like ebiomedicine_lancet split
  real edges by parity so no single grid reaches the doc quorum, and
  per-page grids recover them). Pages sharing an agreeing grid
  (`_grids_agree`, `ZONE_EDGE_TOL` on sorted edges) group into zones —
  non-contiguous, since parities alternate — and `_zone_reading_maps`
  runs `_reading_columns` once per zone so an outdent edge's evidence
  pools only over pages sharing that geometry. The page's active
  `page_edges[pi]`/`page_col_map[pi]` pair drives `_render_page`
  entirely — ordering, claims, notes, captions, heading followers,
  blockquote isolation — while `state.edges`/`state.col_map` keep the
  doc prior's identity (diagnostics report both, plus the page's
  source). A second `_detect_margins` pass reads each page's active
  bands after grids settle (bands derived from the committed grid can
  hide a strip the page's own columns expose); the pass-2 group counter
  continues from the page's existing `("margin", pi, gi)` marks so a
  late-lifted region can't collide with a pass-1 key, and the
  margin-detector's doc-grid re-vote stays reserved for the first pass.
  Physical edges stay the geometry grid; reading identity flows through
  `state.col_map` (`_reading_columns`): an edge whose non-spanning
  evidence on pages ≥1 is ≥80% (`OUTDENT_HEADING_SHARE`) outdented-strip
  fragments — `analyze._outdent_like`: ≤2 short unpunctuated lines whose
  x-extent stops inside `OUTDENT_BAND_FILL` of the band the edge shares
  with its right column — is an outdent edge and folds into the nearest
  non-outdent column on its right rather than ordering as its own
  column (MDPI's outdented section heads). A column of wrapped text
  lines fails the band-fill clause; a strip needs ≥3 evidence blocks;
  the last edge can never fold. Non-spanning assignments and every
  span's covered columns pass through the map in `_ordered_blocks`, and
  the heading follower scan, caption column matching, and blockquote
  same-column checks all compare the mapped reading identity. Footnote
  zones stay physical: a zone is a horizontal band at a column's foot,
  not column identity — merging tops by min across a folded edge lets a
  note-shaped strip block widen the swept region and misfile body text
  as notes (a strip's own top still files its notes; it just can't
  claim the body column's blocks). Geometric distances/indents still
  read `edges[physical col]`. `stats["columns"]` reports the widest
  page's distinct reading columns (the doc prior's map alone would
  under-report a mixed-grid document); `dev/diagnose_layouts.py` prints
  each physical edge's mapped column (`edge=… -> rcolN (outdent)`)
  plus the deciding share, and each page line carries its active
  `grid=`/`src=` alongside the committed `doc_edges`.
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
  edge zones and bounded by `BOILERPLATE_MAX_LINES`/`BOILERPLATE_MAX_CHARS`
  (6 lines / 300 normalized letters — medRxiv's five-line license head
  ≈265 letters fits inside; long continued-table headers and multi-
  paragraph blocks stay body text), and pattern furniture (`VOL`, `www.`,
  `ISSN`, `doi`, `©`, dates) in the outer 7% edge (works on 1-page docs).
  A pre-pass before ordering (`_split_signature_prefixes`, run once per
  page right after the boilerplate set is built) rescues heads fused to
  the block below them — a whole-block signature can never match a fused
  block, so when a block's first line lies in an edge zone its leading
  lines join incrementally (≤`SIGNATURE_PREFIX_MAX_LINES`, the
  boilerplate set's own 6-line bound — a matched join is already a
  verified signature) and the *longest* joined signature found in the
  boilerplate set splits off into its own signature-furniture block —
  alternating heads nest as prefixes of each other, so first-match
  would leave the real head's tail fused to the body. A block whose
  whole signature is already boilerplate is the signature arm's own
  case and never splits — splitting would leak a non-boilerplate
  remainder into body flow. The remainder continues as a normal block
  with a recomputed bbox and re-enters the scan, so stacked heads each
  independently recurring split one at a time; a remainder that is
  itself signature furniture then drops downstream like any other
  head. The matched prefix's union must stay inside the zone or nothing
  splits: a head straddling the boundary would leak as a paragraph —
  the same failure the split fixes. Column-edge voting, the strip clip,
  and render all see the post-split blocks, so a recurring fused head
  can't vote a phantom column edge either.
  Rotated margin strips (tall+narrow blocks in the outer 10% margins,
  measured in `page.mediabox` space so /Rotate pages compare correctly)
  qualify by *line direction*, not width: every line with reliable
  direction data must differ from the page's upright direction — derived
  from `page.rotation` via the inverse rotation matrix — and one
  reliably upright line vetoes the whole block. The legacy <25pt width
  test survives only as a fallback when no line carries usable direction
  data, and it never overrides an upright veto. A strip candidate that
  intersects the `_strip_clip` region — a rotated-table band with a
  member reaching the body band — is exempted; its text is rasterized
  with the table, not dropped.
  `--keep-headers` keeps
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
- **Figures** — an artwork area (a ≥`MIN_IMAGE_BYTES` raster block, or
  connected drawing-path rects from `page.get_drawings()` padded
  `DRAWING_PAD` each and ignoring near-page-size frames, merged across
  ≤`FIGURE_AREA_MERGE` gaps transitively — page decoration keeps a
  wider berth) carrying ≥`FIGURE_MIN_LABELS` short
  (≤`FIGURE_LABEL_CHARS`-char) text members is a figure candidate when
  an adjoining `figure_caption` block sits within 120pt sharing a
  column band. Members are line-level: a block enclosed by the member
  union or touching it with label-like (non-sentence) lines joins —
  panel letters in the `FIGURE_LABEL_PAD` ring qualify only for
  already-short blocks — while a block mixing a member line with
  foreign lines (a straddler) rejects the whole candidate. A caption
  counts as that straddler only when it touches the artwork itself —
  a short caption grazing only the pad ring is the claim's own
  anchor or a crop barrier, never a label member. Validation runs
  inside `_page_claims`: note zones
  are computed once unmasked and once with the provisional label set
  masked out of seeds and gap neighbors (`_footnote_tops` records
  per-column seed provenance; `_seeds_consistent` allows a dismissed
  seed only when some evidence block is a member — masking alone never
  convicts a note), furniture/asides keep their bboxes as crop
  barriers, and rotated-table regions keep precedence on overlap.
  `_figure_crop` shrinks the claim's `FIGURE_CLIP_PAD` clip around
  barriers it can exclude and rejects when one crosses the union; any
  failed gate drops the whole page's figure claims back to ordinary
  text flow. A committed region emits one
  `images/fig_<sha>.png` rasterized from the exact validated clip
  (rotated-table rasters keep their padded clip), claims the caption
  block as its `alt` (the caption still renders in flow), excludes
  its members from every other claim through `raster_of`/`raster_ids`
  and `nonbody_ids`, and counts in `stats["raster_figures"]`.
  `dev/diagnose_layouts.py` reports per-page `figure raster claims`
  (regions/member-blocks) beside the surviving short-fragment counts;
  `scripts/eval_epub.py` replays the same `_page_claims` so claimed
  label words leave the expected vocabulary only when an emitted
  `images/fig_` raster exists — same excuse rule as `images/table_`.
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
  must not double as `alt`). A figure region instead takes the
  caption that anchored its claim — `_figure_caption_near` prefers
  the legend below, then above, and `Table N` can never anchor — so
  its raster always carries a figure-flavored `alt`. Images
  never take `Table N.` (table legends sit above the body, so a
  following one belongs to a text-flowed table); the rotated-table call
  tries the block *above* the region first against the full label set
  and rescues a later column's `Table N.` legend that column-major
  order emits after the cells.
- **Asides** — detector-claimed regions ride a `block["aside"]` region
  key, checked before the furniture tests in `_ordered_blocks` (a
  claimed block survives a furniture-matching line, and never seeds
  the strip clip, a span/cut, or a column edge — the same exclusion
  keeps marked blocks out of `_doc_column_edges` votes and
  `_reading_columns` outdent evidence). Lifted members never enter
  `ordered`, so no body classifier (headings/TOC, bylines, abstract,
  notes diversion, blockquote isolation, paragraph merges, caption
  donation) can see them. At page end each region emits one
  `<aside class="sidebar">` fragment through `_aside_frag`: an
  aside-local pass over the region's own blocks that reuses the grid
  table, rotated-table, and caption-alt machinery with the region's
  x0/x1 as its edges/page_width — `_table_regions`'s 8pt right-band
  inset is compensated so a tight bbox can't lose the rightmost cell —
  and anything unstructureable degrades to aside-local paragraphs,
  never dropped. The grid pass gets the region's full member list, not
  only texts, so `_region_from_run`'s image-overlap rejection still
  vetoes a false table wrapped around an aside figure (non-text
  members never seed bands or claims). Superscripts stay plain `<sup>`
  everywhere inside — `link_fns=False` threads through `_table_html`
  into cells and captions, so no aside path ever links notes.xhtml —
  a region title flag renders
  `<p class="aside-title">`, and no `<hN>`/heading id can appear
  inside, so an aside can't split a chapter or seed the TOC. When a
  paragraph is still open the page's asides queue in
  `state.pending_asides` and `_flush_paragraph` — the one paragraph
  flush, shared by render's closure and emit's document tail — drains
  them into `par_page`'s frags right behind the closing paragraph, so
  a document ending mid-paragraph can't drop them; with no open
  paragraph they append to their own page's frags after body flow
  (aside order follows member y). `stats["asides"]` counts emitted
  regions; `dev/diagnose_layouts.py` reports the lifted count per page.
- **Front-matter info boxes** — the machinery's first detector
  (`analyze._detect_asides`, called in `convert()` after the
  signature-prefix split, before `_doc_column_edges`, so a claimed
  field column can't vote a phantom edge). Pages 0–1 only, and only
  grid-independent evidence: a field block opens with a profile
  `info_labels` label — `Label:` any casing (multi-word labels and
  `*` markers included) or an all-caps label with no colon
  (`EDITED BY`, `RECEIVED  #35 April 2024`) — a mid-sentence
  `Received:` never reaches the anchor. Aligned wrap continuations
  (same left edge ±8pt, ≤2× line-height gap, ≤8-line run cap) extend
  a field — an MDPI `Citation:` wraps narrow lines while a body
  paragraph, living on a different left edge, can't follow. Runs
  chain while adjacent (≤2× line height, x-overlap) — ordinary body
  text or unclaimed signature furniture intersecting the open gap
  inside the pair's x-union is a barrier — and a box needs ≥2
  distinct labels, so a single `Received:`, a repeated label, or a
  top-edge `TYPE / PUBLISHED / DOI` stack with one known label stays
  in flow. The frozen core is the run-members' union: inside it
  admits only media or short non-sentence text (Frontiers' email
  line); a straddler stays whole in body flow; outside, a bounded
  media-anchored group within a hop of a field edge may attach
  (natcomm's badge image + `Check for updates` text) — a nearby
  text-only paragraph never can. Every member gets a shared
  `block["aside"]` region key; `lang` profiles without
  `info_labels` (hand-built profiles) detect nothing.
- **Margin marginalia** — the machinery's second detector
  (`analyze._detect_margins`, called in `convert()` right after the
  `_doc_column_edges` vote, before the reading-column map; a marked
  *voted* margin edge triggers exactly one grid re-vote). Each page's
  body bands (`_margin_bands`) derive from the grid's occupied
  x-ranges: per column edge, the right bound is the deepest x-extent
  among blocks starting in the left 75% (`MARGIN_RIGHT_START`) of the
  nominal band — a margin strip can't stretch the band that would
  exclude it — capped by the next edge, widened by `MARGIN_BAND_TOL`
  for slight bleed. Text or media wholly outside every band is a
  *candidate* (`_margin_candidates`): furniture, signatures,
  already-claimed asides, rotated strips, and `Figure N` legends are
  excluded up front (figure-domain content belongs to the figure
  machinery). Candidates group by margin zone (left of the first
  band, right of the last, inside a gutter) and split at
  `table_caption` openers (`_margin_groups`) so a legend binds its
  own trailing notes. A group lifts only with sidebar evidence
  (`_margin_group_evidence`): a `Table N`/`Label:` opener member, an
  `info_labels` field inside a multi-member group, or a
  punctuation-structured fragment recurring across doc pages or
  within the group; serial-numbered fragments (`Aim 01`, digit
  strips) carry no evidence, and margin images ride an evidenced
  group but never justify one — figure artwork spilling past the
  band is not marginalia. A margin strip dense enough to vote its
  own edge is handled by the voted-edge arm (`_margin_voted_edge`):
  the edge must be outermost, narrow against the widest band
  (`MARGIN_EDGE_MAX_FILL`), and ≥`MARGIN_EDGE_SHARE` of its
  non-spanning non-furniture members must carry evidence (counted
  on pages ≥1 like the outdent read; then every page's members
  mark, since a margin column's page-0 content is marginalia too).
  Region keys are `("margin", page, group)` /
  `("margin-edge", page, edge)` — emission rides the shared
  `<aside>` path unchanged. Table-legend anchoring: when a page
  holds exactly one table region and exactly one legend aside
  group, the legend's `Table N` member binds into the table's
  `<caption>` (or becomes a raster region's alt donor) and the
  remaining legend material emits as an aside immediately after
  `</table>` rather than at page end; an ambiguous pairing (two
  tables, two legends) degrades to the whole page-end aside, and a
  legend whose table never emits falls back to the plain aside
  rather than dropping. `scripts/eval_epub.py` replays the same
  pass (and re-vote) before partitioning body vs furniture so the
  evaluator sees the same claims conversion saw.
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
  metadata, the `info_labels` front-matter box vocabulary, the
  `section_fallback` untitled-chapter name, the
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
  labels, indented list items/quotes, centered figures, bordered
  `aside.sidebar` boxes — no floats or fixed widths).

## Layout baseline integration audit (#33)

Implementation base, checked 2026-11-06 after `git fetch origin master`:
`651a2521ced50e3834cbfabaf34252ca672b74a8`. The starting checkout and
`origin/master` were identical; this work does not assume branch divergence.
The unmodified, corpus-free suite passed (143 passed, 8 skipped, 2 slow
cases deselected). Converter heuristics and golden snapshots are unchanged.

- **#34 furniture:** `scripts/eval_epub.py`'s `boilerplate_signatures`,
  `_dropped`, and `furniture_words` match today's `analyze`/`order` paths,
  including the 80-letter signature ceiling and 25pt margin-strip width.
  Their literal thresholds and tuple-to-text normalization must move in
  parity with future furniture changes. `tests/test_eval_epub.py` covers
  clean/High/operational exits and language resolution, but does not yet
  directly pin these furniture exclusions or the rasterized-table excuse.
- **#41 preserved asides:** no aside path exists at this base. Evaluator
  coverage currently reads chapter and notes documents; preserved aside
  text must remain counted, never excused as stripped furniture. Audit
  `_BLOCK_OPEN`/`_LEGEND_AT` if new aside/figure markup carries legends,
  and the test helper's `TOP_TAGS` if fragment snapshots must include it.
- **#35 figure claims:** `rasterized_blocks` mirrors only rotated-table
  regions and `render`'s intersecting `tbl_ids`. Coverage/legend masking
  and raster evidence must also mirror any new intentional figure claims;
  `epub_imgs` currently recognizes only `images/img_*`, and vector reporting
  uses page-level drawing counts rather than per-fragment locations.
  Owners of #35/#41 must add evaluator subprocess regressions alongside
  their production changes; this baseline records the integration points,
  not speculative fixes to them.

## Preprint-stamp thresholds audit (#34)

- `_is_margin_strip` is direction-driven: `_line_dir` accepts only
  near-axis dirs (one component `>0.9` dominating the other), `_upright_dir`
  maps displayed +x through `~Matrix(page.rotation)` so comparisons stay
  in mediabox space on /Rotate pages, and the outer-margin test uses
  `page.mediabox.width` — never displayed `page.rect` (they swap W/H on
  rotated pages). `_is_furniture` takes the `page` object (not a rect) so
  both spaces are available; its `clip` argument is consulted only by the
  strip arm — edge/content arms always apply regardless.
- `order._ordered_blocks` builds the clip via
  `analyze._strip_clip(page, blocks, boilerplate, lang)` *before*
  flagging and passes it to `_is_furniture`. The clip is the
  `_rotated_table_region` over only the blocks that can still reach the
  body flow — blocks the signature or pattern arm removes (and
  empty-text blocks) can no more seed it than they can seed render's
  region — and it forms only when a *band member* (a tall block
  covering a shared ≥3-overlap y-point in any band, not just the first
  trigger, `_rotated_table_seeds`) reaches the body band (`x0 <= 0.9W
  and x1 >= 0.1W` on the member, not the union). Testing the union
  rect's span instead let left+right margin strips satisfy it jointly
  and self-exempt into a fake rasterized table that swallowed
  intersecting body text; sampling membership only at the first ≥3
  point refused legit tables whose margin cells open the band earliest.
- `scripts/eval_epub.py` mirrors via a per-page `_page_ctx` (displayed
  rect, mediabox width, upright dir, dict blocks keyed by bbox) plus the
  same `_strip_clip`, seeded by the eval's own boilerplate set and
  resolved profile so the signature/pattern exclusions match order's.
  Block tuples carry no line dirs/counts, so the dict pass is required;
  the shared helpers `_line_dir`/`_upright_dir`/`_strip_clip` are
  imported lazily inside functions — pulling `exegesis.analyze` pulls
  fitz, and module-top imports would escape the exit-2 net. Tuple
  unpacking in `_dropped` must respect the `(x0, y0, x1, y1, text, …)`
  shape — `y0, _, _, y1` binds `y0` to x0 and silently disables every
  bottom-edge test.
- Corpus check: `journals/preprints/arxiv_revtex.pdf` converts with zero
  `arXiv:`/`cs.LG` leakage in body text; `medrxiv_preprint.pdf` converts
  with no license-head wording (remaining `license` hits are legitimate
  disclosure prose). Eval's residual findings on arxiv_revtex (equation
  fragments, TOC headings) are pre-existing issues unrelated to #34.

## Conventions

- Every function carries a docstring explaining its heuristic (the *why*,
  since thresholds are tuned, not principled).
- Heuristics are tuned against scanned-journal PDFs with invisible OCR text
  layers (jittery font sizes, no bold flags, stray artifacts). Prefer
  position/repetition signals over font-style signals.
- Keep it dependency-light: stdlib + pymupdf + ebooklib only. No OCR/ML —
  scanned-image PDFs are a documented limitation.
- Release scripts (`scripts/*.sh`) hold every precondition in the guard
  block ahead of any mutation — e.g. `bump.sh` pre-flights the gitignored
  `*.pdf` corpus and the python env (either can be absent in a
  worktree/fresh clone) so a missing dependency can't strand a dirty
  tree mid-prep.
- Eval/tooling that reimplements a pipeline signal must mirror the
  pipeline's constants and floors, not approximate them —
  `scripts/eval_epub.py`'s furniture vocabulary copies
  `analyze._boilerplate_signatures` (edge-zone restriction, letters-only
  sig, `max(2, int(n * 0.4))` page floor, line/char bounds) and
  `analyze._is_furniture` (outer-7% content/digit gate, direction-driven
  margin strip with upright veto, <25pt fallback, table-clip exemption —
  importing the pipeline's own direction/region helpers where lazily
  possible). A looser
  re-implementation (page-wide signatures, no floor, unconditional edge
  zone) produces false "clean" verdicts on 1-page inputs, repeated body
  text, and real content near the edge — the same drift applies to any
  check that borrows a pipeline heuristic. Mirror every path that drops
  text *by design*, not just furniture: rotated tables leave the flow as
  `images/table_*.png` and figure regions as `images/fig_*.png`, so both
  raster mirrors replay the converter's own preparation through
  `_claim_pages` (signature splits, asides, margin lifts, document
  edges) and call `analyze._page_claims` — a replay, not a mirror, so
  aside marks, the provisional-label note mask, table precedence, and
  crop validation can't drift — and a figure page stops flagging "not
  capturable" on vector geometry alone. `rasterized_blocks` additionally
  replays `_bind_legend`'s raster arm (one table region + one
  legend-carrying aside on the page): a margin legend bound into a
  raster's `alt` never emits as text, so its words, its `Table N`, and
  its window position leave the expected side and the disposition
  resolves 'rasterized', not 'missing' — an unmodeled path grades
  intended behavior as loss, the report contradicts itself, and the
  High gate fails a correct conversion.
- A repeated regex group must match its text exactly one way — no
  empty-able repeat nested inside it (`(?:<t>x</t>|[^<]*)*`,
  `(?:\.?\s*)*`) and no two alternatives that can consume the same
  text (a generic `<[^>]+>` beside a `<sup>…</sup>` arm): py39 `re` has
  no atomic groups or possessive quantifiers, so a failing match
  backtracks exponentially — a 107-char `<sup>` hung `eval_epub.py`.
  Make each loop step consume one unambiguous unit
  (`(?:[^<]|<(?!/sup>)[^>]*>)*`, `<(?!sup\b)[^>]+>`,
  `(?:[.\s]|</b><b>)*`).

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
