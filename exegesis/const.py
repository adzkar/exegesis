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

# Language profiles live in exegesis/lang.py — `convert(lang=...)`/`--lang`
# select the pattern set stamped into the EPUB; defaults to English.
