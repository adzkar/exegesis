"""Shared pipeline constants — tuned thresholds and the assumed language."""

EDGE_ZONE = 0.20
BOILERPLATE_COVERAGE = 0.4
MIN_IMAGE_BYTES = 2048
MIN_HEADING_CHARS = 4
MAX_HEADING_CHARS = 120
PAGES_PER_FALLBACK_CHAPTER = 10

# Language is assumed English throughout: NOTE_START_RE, SECTION_LABEL_RE
# and FURNITURE_RE all match English-only patterns.
BOOK_LANG = "en"
