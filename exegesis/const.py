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
PAGES_PER_FALLBACK_CHAPTER = 10

# Language profiles live in exegesis/lang.py — `convert(lang=...)`/`--lang`
# select the pattern set stamped into the EPUB; defaults to English.
