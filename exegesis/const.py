"""Shared pipeline constants — tuned thresholds."""

EDGE_ZONE = 0.20
BOILERPLATE_COVERAGE = 0.4
MIN_IMAGE_BYTES = 2048
MIN_HEADING_CHARS = 4
MAX_HEADING_CHARS = 120
PAGES_PER_FALLBACK_CHAPTER = 10

# Language profiles live in exegesis/lang.py — `convert(lang=...)`/`--lang`
# select the pattern set stamped into the EPUB; defaults to English.
