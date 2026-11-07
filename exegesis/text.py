"""Pure helpers over PyMuPDF ``get_text("dict")`` structures, plus the
language-agnostic text patterns (font-name suffixes, list items). The
language-bound patterns — furniture, note openers, section labels,
caption labels — live in ``lang.LangProfile``, as does the
``single_letter_words`` drop-cap set ``_join_lines`` consults."""
import re
from statistics import median

from .lang import EN, LangProfile

BOLD_FONT_RE = re.compile(r"(?i)(bold|\.b\d*$|-b\d*$|/b\d*$)")
ITAL_FONT_RE = re.compile(r"(?i)(italic|oblique|\.i\d*$|-i\d*$|/i\d*$)")
LIST_ITEM_RE = re.compile(r"^\d{1,2}[.)]\s+\S")
FN_LINK_RE = re.compile(
    r'<sup><a href="notes\.xhtml#fn(\d+)">(\d+)</a></sup>'
)
# the XML 1.0 Char production excludes C0 other than \t \n \r, lone
# surrogates, and U+FFFE/U+FFFF; born-digital files still carry them as
# layout artifacts (New Scientist's BELs, #56) or broken ToUnicode maps
_XML_INVALID = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


def _sanitize_text(text):
    """Drop characters the XML 1.0 Char production excludes.

    Marks like ``\x07`` are invisible layout artifacts, not content;
    leaving them in crashes ``ebooklib``/``lxml`` ("no NULL bytes or
    control characters") when a string reaches an ``epub`` item title
    and silently corrupts signatures and pattern matches elsewhere.
    """
    return _XML_INVALID.sub("", text)


def _sanitize_spans(pdict):
    """Strip XML-invalid characters from every span's text in a page dict.

    Runs once at extraction (``emit._extract_pages``) so every
    downstream consumer — fragment HTML, headings, ``dc:title``,
    signatures, pattern matching — reads clean text; sanitizing on
    each read instead would still leak the raw chars through the
    span-level ``html.escape`` path in render.
    """
    for block in pdict["blocks"]:
        for line in block.get("lines", []):
            for span in line["spans"]:
                span["text"] = _sanitize_text(span["text"])


def _block_lines(block):
    """Return the list of line dicts for a text block (empty for images)."""
    return block.get("lines", [])


def _line_text(line):
    """Concatenate the span texts of one line."""
    return "".join(s["text"] for s in line["spans"])


def _block_text(block, lang: LangProfile = EN):
    """Join all lines of a text block, undoing soft hyphenation."""
    parts = [
        t
        for line in _block_lines(block)
        if (t := _line_text(line).strip())
    ]
    return _join_lines(parts, lang)


def _line_max_size(line):
    """Return the largest font size among the line's spans."""
    return max((s["size"] for s in line["spans"]), default=0)


def _span_is_bold(span):
    """Bold via flags or font-name suffix (.B/Bold) — flags are often absent."""
    return bool(
        span["flags"] & 16 or BOLD_FONT_RE.search(span.get("font", ""))
    )


def _span_is_italic(span):
    """Italic via font-name suffix (.I/Italic/Oblique)."""
    return bool(ITAL_FONT_RE.search(span.get("font", "")))


def _line_is_bold(line):
    """True when every span in the line is bold."""
    return bool(line["spans"]) and all(_span_is_bold(s) for s in line["spans"])


def _block_median_size(block):
    """Median span size of a text block."""
    sizes = [
        s["size"]
        for l in _block_lines(block)
        for s in l["spans"]
        if s["text"].strip()
    ]
    return median(sizes) if sizes else 0


def _join_lines(parts, lang: LangProfile = EN):
    """Join line fragments, merging words split by a soft hyphen.

    A lone uppercase fragment fuses into a lowercase continuation — a
    drop cap — unless the letter is a real single-letter word in the
    document's language: ``lang.single_letter_words`` holds the set
    ('I'/'A' in English; German has none, so every lone letter fuses).
    """
    out = []
    for t in parts:
        t = t.strip()
        if not t:
            continue
        if (
            out
            and len(out[-1]) == 1
            and out[-1].isupper()
            and out[-1] not in lang.single_letter_words
            and t[:1].islower()
        ):
            out[-1] += t
        elif out and re.search(r"[a-z]-$", out[-1]):
            out[-1] = out[-1][:-1] + t
        else:
            out.append(t)
    return " ".join(out)


def _first_line_indent(block):
    """Indent of a block's first line relative to the block's left edge."""
    lines = _block_lines(block)
    if not lines:
        return 0
    return lines[0]["bbox"][0] - block["bbox"][0]
