"""Pure helpers over PyMuPDF ``get_text("dict")`` structures, plus the
shared text patterns (font-name suffixes, furniture, note openers, list
items, named section labels)."""
import re
from statistics import median

BOLD_FONT_RE = re.compile(r"(?i)(bold|\.b\d*$|-b\d*$|/b\d*$)")
ITAL_FONT_RE = re.compile(r"(?i)(italic|oblique|\.i\d*$|-i\d*$|/i\d*$)")
FURNITURE_RE = re.compile(
    r"(?i)(www\.|downloaded from|vol\.?\s*\d|issn|doi[:\s]|©|copyright|"
    r"\b\d{2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec))"
)
NOTE_START_RE = re.compile(
    r"(?i)^[*\s]*(preparation of (?:this|the)|requests? for reprints|"
    r"correspondence (?:concerning|should be|addressed)|"
    r"(?:this (?:paper|article|work) was )?supported by|"
    r"the author[s]? (?:thank|acknowledge|wish)|to whom correspondence)"
)
LIST_ITEM_RE = re.compile(r"^\d{1,2}[.)]\s+\S")
FN_LINK_RE = re.compile(
    r'<sup><a href="notes\.xhtml#fn(\d+)">(\d+)</a></sup>'
)
SECTION_LABEL_RE = re.compile(
    r"^(References and Notes|Notes and References|References|Bibliography|"
    r"Supporting Online Material|Supplementary Materials?|Acknowledg?ements|"
    r"Acknowledgments|Materials and Methods|Appendix|Funding|"
    r"Author Contributions|Data Availability)\b"
)


def _block_lines(block):
    """Return the list of line dicts for a text block (empty for images)."""
    return block.get("lines", [])


def _line_text(line):
    """Concatenate the span texts of one line."""
    return "".join(s["text"] for s in line["spans"])


def _block_text(block):
    """Join all lines of a text block, undoing soft hyphenation."""
    parts = [
        t
        for line in _block_lines(block)
        if (t := _line_text(line).strip())
    ]
    return _join_lines(parts)


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


def _join_lines(parts):
    """Join line fragments, merging words split by a soft hyphen."""
    out = []
    for t in parts:
        t = t.strip()
        if not t:
            continue
        if (
            out
            and len(out[-1]) == 1
            and out[-1].isupper()
            and out[-1] not in "IA"
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
