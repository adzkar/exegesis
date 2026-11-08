"""Shared pipeline constants — tuned thresholds."""

EDGE_ZONE = 0.20
BOILERPLATE_COVERAGE = 0.4
# Real running heads are short multi-line blocks: the medRxiv license head
# needs five lines and a 265-letter normalized signature, far past the old
# 80-char ceiling. Bounding by line count as well as signature length keeps
# a page-spanning repeated block (a continued-table header, a multi-
# paragraph license footer) from excusing itself.
BOILERPLATE_MAX_LINES = 6
BOILERPLATE_MAX_CHARS = 300
# A running head fused to the block below it escapes the whole-block
# signature test. The prefix split tries leading-line joins up to the
# boilerplate set's own line bound — a matched join is already a
# verified recurring signature, so a longer cap can't promote a title
# that merely resembles the head, and real running heads reach 4-6
# lines (the medRxiv license head above).
SIGNATURE_PREFIX_MAX_LINES = BOILERPLATE_MAX_LINES
MIN_IMAGE_BYTES = 2048
MIN_HEADING_CHARS = 4
MAX_HEADING_CHARS = 120
# invisible text (#63): a span is painted invisibly when its fill is
# near-white on a light ground — publishers leave white placeholder
# lines (PLOS's `a111…` runs) that extract as ordinary text and emit as
# prose. The per-channel floor keeps saturated colors (yellow, cyan)
# reading as text; the ground floor is the median rendered brightness
# under the span's bbox — the span's own pale glyphs cover a minority
# of the region, so the median reads the painted background and white
# on a dark banner stays.
INVISIBLE_TEXT_MIN = 0xF0
INVISIBLE_BG_MIN = 200
# prose evidence for the reading-column fold (#62): a member counts as
# prose when it holds ≥this many alphabetic words of ≥3 letters — a
# column is a run of prose. Table rows, equation fragments, heads, and
# caption labels recur at fixed indents on math/table papers and seed
# phantom indent-cluster edges whose supporters stay under this bound.
VOTE_MIN_WORDS = 4
# An outdent edge is a physical column edge whose non-spanning blocks are
# almost all short unpunctuated fragments floating in a narrow strip —
# a real edge (full-width elements start there) that must not order as
# its own reading column. The share is deliberately high: a genuine
# column carries prose, so 0.8 fires only when a column is *dominated*
# by strip-shaped blocks, not merely contains some. The floor keeps a
# couple of stray blocks from rebranding an edge, and the band-fill
# bound separates strip fragments from wrapped column lines: wrapped
# text hugs the band's far edge, an outdented fragment stops early.
OUTDENT_HEADING_SHARE = 0.8
OUTDENT_MIN_BLOCKS = 3
OUTDENT_BAND_FILL = 0.85
# a fragment-dominated edge — almost no prose-carrying members — folds
# into the nearest real column: the bound is the complement of the
# outdent share, so an edge needs ~1/5 prose evidence to read as a
# column (the arxiv indent clusters hold ~0%)
FRAG_PROSE_SHARE = 0.2
PAGES_PER_FALLBACK_CHAPTER = 10

# info-box detection bounds (#41): run adjacency and media attachment
# are in body line-height units; continuation evidence is a left-edge
# x tolerance; the run cap stops a wrapped MDPI citation (~6 narrow
# lines) before a following paragraph can sneak in; inside-core text
# members cap at 3 lines and never carry a sentence end; outside-core
# badge groups cap at 4 media+label members with a 1.5-line hop.
INFO_ADJ_GAP = 2.0
INFO_CONT_X_TOL = 8.0
INFO_RUN_MAX_LINES = 8
INFO_MEMBER_MAX_LINES = 3
INFO_ATTACH_MAX = 4
INFO_ATTACH_GAP = 1.5

# per-page/zone column grids (#37): the doc-wide edge vote is a prior,
# not a prison — a page whose own cluster evidence is *strong* gets its
# own grid, and pages sharing an equal grid form a zone (adjacency not
# required: mirrored-margin layouts alternate parity). A candidate is
# strong only when every proposed column carries multiple non-spanning
# text blocks spread over more than one y-band (a lone heading/byline
# cluster is not a column), with a median block width wide enough to
# hold prose (table cells and margin strips run narrower), and x-ranges
# that respect the gutter to the next column (indent clusters and
# hanging-reference starts cross it). Every clause binds every proposed
# column; one weak column demotes the whole candidate to the doc grid.
ZONE_EDGE_TOL = 15
ZONE_COL_MIN_BLOCKS = 2
ZONE_COL_MIN_SPREAD = 2.5  # x line_h
ZONE_COL_MIN_WIDTH = 8.0  # x body_size — a prose column holds ~8em
# a real column's support flows down the page — an author grid,
# formula fragment, or caption strip covers under a tenth of the
# text extent and reads as a column-shaped cluster without being one
ZONE_COL_MIN_COVER = 0.15
ZONE_GUTTER_TOL = 4.0
ZONE_GUTTER_GRAZE = 0.2

# a page-wide whitespace strip — no column's items intersect it — marks
# a band seam: a section that occupies a horizontal band across the
# columns (declarations over references, front matter over an abstract)
# reads band-major instead of splitting around the other column (#61).
# The bound is in line heights: paragraph spacing stays under it while
# a real band break (Frontiers' ~1.7x, Bundesgesundheitsblatt's ~4x)
# clears it.
BAND_MIN_GAP = 1.5  # x line_h
# the regions on both sides of a candidate strip must each hold real
# content — a page-top running-head strip or a page-bottom colophon is
# the same whitespace but not a band boundary
BAND_MIN_DEPTH = 3.0  # x line_h

# marginalia detection (#36): a block fully outside the doc grid's body
# bands is only a *candidate* — lifting it to an aside needs spatial
# coherence plus sidebar evidence (a table legend, a ``Label:`` opener,
# a grouped info-label field, or a punctuation-structured fragment
# recurring across pages); serial-numbered strips are manuscript line
# numbering and never qualify, and margin images ride an evidenced
# group but never justify one — a figure fragment spilling past the
# band is not marginalia on its own. The band tolerance keeps a block
# bleeding a few points past an edge in-band. Occupancy for a column's
# right bound counts only blocks starting inside the band's own left
# fraction — a margin strip can't stretch the band that would exclude
# it. A strip that reached the column-edge quorum is a *voted* margin
# column — an outermost edge is one only when its band is narrow
# against the widest band and nearly all its non-spanning members
# carry sidebar evidence.
MARGIN_BAND_TOL = 6.0
MARGIN_RIGHT_START = 0.75
MARGIN_EDGE_MAX_FILL = 0.5
MARGIN_EDGE_SHARE = 0.8

# figure-region detection (#35): a raster image block or a merged
# vector-drawing area earns a raster claim only with short label
# density over it and an adjoining figure caption — all three signals.
# The label pad covers panel letters sitting just outside the artwork;
# the char cap is the survey's <=12-char fragment measure; the clip pad
# is the margin validated against foreign blocks before rasterizing.
FIGURE_LABEL_PAD = 14.0
FIGURE_LABEL_CHARS = 12
FIGURE_MIN_LABELS = 2
FIGURE_CLIP_PAD = 4.0
# area merge gap: panels inside one figure sit a few points apart
# (eLife's grids ~5pt); page decoration keeps a wider berth (~9pt), so
# merging beyond this pulls headers/logos into the figure union
FIGURE_AREA_MERGE = 7.0
# vector figure areas: drawing-path rects padded so thin or touching
# strokes connect, ignoring near-page-size frames
DRAWING_PAD = 1.0
PAGE_FRAME_RATIO = 0.9

# wrapped floats (#38): a strip of text narrowed beside an in-column image
# shares a second x0 inside one logical column — that x0 variance alone
# must never seed a column edge, or the far strip emits after the resumed
# body. A strip counts only beside a non-trivial image (an icon or a
# bullet can't host a wrap), with most of its own height inside the
# image's y-band (a real column's blocks flow past the float, not around
# it), and only when an eligible text block encloses image + strips
# horizontally *and* adjoins the band vertically — the resume/opening
# block that proves one column. The vertical bound is what makes the
# proof local: a distant full-width span (title, abstract, spanning
# section head) horizontally encloses any strip pair on the page, so an
# unbounded cover test lets it brand the neighboring column's blocks as
# wrap fragments and the real edge dies. The gap is generous — the
# resume/opening block can sit several lines off the float — while a
# page-distant span cannot reach it. Even with a near cover, a strip
# whose x0 is shared by blocks flowing past the band keeps its vote:
# that column continues above or below the float, the real second-column
# case. The edge tolerance matches ``_column_edges``'s own cluster window.
WRAP_MIN_IMG = 24.0
WRAP_SIDE_TOL = 2.0
WRAP_BAND_SHARE = 0.5
WRAP_EDGE_TOL = 15.0
WRAP_COVER_GAP = 96.0

# Language profiles live in exegesis/lang.py — `convert(lang=...)`/`--lang`
# select the pattern set stamped into the EPUB; defaults to English.
