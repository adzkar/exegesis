#!/usr/bin/env python3
"""Convert a PDF (including two-column journal papers) to a reflowable EPUB.

Text blocks are reordered with column awareness so two-column layouts read in
the correct order, embedded images are carried over, and headings detected by
font size become the EPUB table of contents.
"""

import argparse
import hashlib
import html
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median

import fitz
from ebooklib import epub

EDGE_ZONE = 0.20
BOILERPLATE_COVERAGE = 0.4
MIN_IMAGE_BYTES = 2048
MIN_HEADING_CHARS = 4
MAX_HEADING_CHARS = 120
PAGES_PER_FALLBACK_CHAPTER = 10

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
# Language is assumed English throughout: NOTE_START_RE, SECTION_LABEL_RE
# and FURNITURE_RE all match English-only patterns.
BOOK_LANG = "en"


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


def _body_font_size(pages):
    """Return (modal body span size, median line height) across the document.

    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs so
    the extraction cost is paid once in ``convert``.
    """
    sizes = Counter()
    heights = []
    for _page, pdict in pages:
        for block in pdict["blocks"]:
            if block["type"] != 0:
                continue
            for line in _block_lines(block):
                heights.append(line["bbox"][3] - line["bbox"][1])
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes[round(span["size"], 1)] += len(span["text"])
    return (
        sizes.most_common(1)[0][0] if sizes else 10.0,
        median(heights) if heights else 12.0,
    )


SECTION_LABEL_RE = re.compile(
    r"^(References and Notes|Notes and References|References|Bibliography|"
    r"Supporting Online Material|Supplementary Materials?|Acknowledg?ements|"
    r"Acknowledgments|Materials and Methods|Appendix|Funding|"
    r"Author Contributions|Data Availability)\b"
)


def _section_label(text):
    """Leading section label if a text starts with a known heading phrase.

    Labels like 'References and Notes' often share a block/line with the
    first entry, so font-size heading detection can't see them. The rest of
    the text is returned separately to continue as a paragraph.
    """
    m = SECTION_LABEL_RE.match(text)
    if not m:
        return None
    rest = text[m.end():].strip()
    if rest and not (rest[0].isdigit() or rest[0].isupper()):
        return None
    return m.group(1), rest


def _authorish(text):
    """True if a short block plausibly lists author names (not a sentence)."""
    t = text.strip()
    return (
        3 < len(t) < 170
        and (", " in t or " and " in t)
        and not t.endswith((".", ":", ";", "?", "!"))
    )


def _valid_title(t):
    """True if a metadata title is usable (has letters, not pagination junk)."""
    return bool(t) and bool(re.search(r"[A-Za-z]", t)) and not re.match(
        r"^[\d\s.\-_()]+$", t
    )


def _looks_like_heading(text):
    """True if the string is plausible heading text (no sentence periods,
    no footnote-marker opener — headings never start with *†‡§)."""
    return (
        MIN_HEADING_CHARS <= len(text) <= MAX_HEADING_CHARS
        and text[:1] not in "*†‡§"
        and not re.search(r"\.", text[:-1])
        and not text.rstrip().endswith(".")
    )


def _heading_level(line, body_size):
    """Classify a line as h1/h2/None from its font size and weight."""
    text = _line_text(line).strip()
    if not _looks_like_heading(text):
        return None
    size = _line_max_size(line)
    if size >= body_size * 1.6:
        return 1
    if size >= body_size * 1.3 or (size >= body_size * 1.05 and _line_is_bold(line)):
        return 2
    return None


def _standalone_heading_level(block, body_size):
    """Classify a single-line block as a heading (h1/h2) or None.

    Section headings typically sit in their own block at a modestly larger
    size than body text, where per-line thresholds tuned for inline spans
    would miss them.
    """
    lines = _block_lines(block)
    if len(lines) != 1:
        text = " ".join(_line_text(l).strip() for l in lines)
        if all(_line_max_size(l) >= body_size * 1.6 for l in lines):
            if _looks_like_heading(text):
                return 1
        elif all(_line_is_bold(l) for l in lines):
            if _looks_like_heading(text):
                return 2
        return None
    size = _line_max_size(lines[0])
    if size >= body_size * 1.6:
        level = 1
    elif size >= body_size * 1.08:
        level = 2
    elif size >= body_size * 0.9 and _line_is_bold(lines[0]):
        level = 2
    else:
        return None
    text = _line_text(lines[0]).strip()
    return level if _looks_like_heading(text) else None


def _rotated_table_region(blocks):
    """Bbox of a rotated (landscape) table: ≥3 tall-narrow text blocks sharing
    a common y-point, i.e. rotated text columns standing side by side."""
    tall = [
        b
        for b in blocks
        if b["type"] == 0
        and (b["bbox"][3] - b["bbox"][1]) > 4 * max(1, b["bbox"][2] - b["bbox"][0])
        and (b["bbox"][3] - b["bbox"][1]) > 60
    ]
    if len(tall) < 3:
        return None
    events = sorted(
        [(b["bbox"][1], 1) for b in tall] + [(b["bbox"][3], -1) for b in tall]
    )
    cov = 0
    shared = False
    for _, d in events:
        cov += d
        if cov >= 3:
            shared = True
            break
    if not shared:
        return None
    return fitz.Rect(
        min(b["bbox"][0] for b in tall),
        min(b["bbox"][1] for b in tall),
        max(b["bbox"][2] for b in tall),
        max(b["bbox"][3] for b in tall),
    )


def _first_line_indent(block):
    """Indent of a block's first line relative to the block's left edge."""
    lines = _block_lines(block)
    if not lines:
        return 0
    return lines[0]["bbox"][0] - block["bbox"][0]


def _sup_marker(block, body_size):
    """Footnote number if the block opens with a superscript digit, else None."""
    lines = _block_lines(block)
    if not lines:
        return None
    first = next((s for s in lines[0]["spans"] if s["text"].strip()), None)
    if first and first["size"] < body_size * 0.75:
        m = re.match(r"^(\d+)['’]?$", first["text"].strip())
        if m:
            return int(m.group(1))
    return None


def _footnote_tops(blocks, edges, body_size, page_height, line_h):
    """Map each column index to the y where its footnote region starts.

    Two detection paths: (a) blocks opening with a superscript digit in the
    lower half of the page, and (b) unnumbered footnote zones — a smaller-than
    -body block in the bottom 40% separated from the text above by a gap much
    larger than the line height (author notes sit under a rule or whitespace).
    """
    tops = {}
    cols = {}
    for b in blocks:
        if b["type"] != 0:
            continue
        col = _assign_column(b, edges)
        cols.setdefault(col, []).append(b)
        if b["bbox"][1] >= page_height * 0.45 and _sup_marker(b, body_size) is not None:
            tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
    for col, cblocks in cols.items():
        cblocks.sort(key=lambda b: b["bbox"][1])
        for prev, cur in zip(cblocks, cblocks[1:]):
            if (
                cur["bbox"][1] > page_height * 0.6
                and cur["bbox"][1] - prev["bbox"][3] > 2 * line_h
                and _block_median_size(cur) < body_size * 0.95
            ):
                tops[col] = min(tops.get(col, cur["bbox"][1]), cur["bbox"][1])
                break
    for col, cblocks in cols.items():
        for b in cblocks:
            if (
                b["bbox"][1] > page_height * 0.55
                and _block_median_size(b) < body_size * 0.95
                and NOTE_START_RE.match(_block_text(b))
            ):
                tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
    return tops


def _signature(text):
    """Normalize block text for boilerplate comparison (digits/case removed)."""
    return re.sub(r"[^a-z]+", "", text.lower())


def _boilerplate_signatures(pages):
    """Find normalized texts that recur near page edges (headers/footers).

    A short text block sitting in the top or bottom ``EDGE_ZONE`` of at least
    ``BOILERPLATE_COVERAGE`` of pages is treated as a running header/footer.
    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs.
    """
    counts = Counter()
    for page, pdict in pages:
        height = page.rect.height
        seen = set()
        for block in pdict["blocks"]:
            if block["type"] != 0:
                continue
            _, y0, _, y1 = block["bbox"]
            if y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE):
                sig = _signature(_block_text(block))
                if 3 <= len(sig) <= 80:
                    seen.add(sig)
        counts.update(seen)
    threshold = max(2, int(len(pages) * BOILERPLATE_COVERAGE))
    return {sig for sig, n in counts.items() if n >= threshold}


def _column_edges(items):
    """Detect column left edges by clustering block x0 positions.

    Clusters of ≥2 blocks within 15pt count as a column; stray x0s (page
    numbers, margin notes) don't form edges. Returns 1+ edges left to right.
    """
    xs = sorted(b["bbox"][0] for b in items if b["type"] == 0)
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < 15:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [median(c) for c in clusters if len(c) >= 2] or [0.0]


def _doc_column_edges(pages):
    """Column edges for the document: edges found on ≥half of all pages.

    Per-page clustering produces phantom edges from bylines, headings, and
    other spanning elements; real columns repeat on most pages, so only
    edges that recur survive. Prevents mid-page splits that corrupt both
    ordering and footnote-gap detection.
    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs.
    """
    votes = []
    for _page, pdict in pages:
        items = pdict["blocks"]
        for e in _column_edges(items):
            for v in votes:
                if abs(v[0] - e) < 15:
                    v[1] += 1
                    break
            else:
                votes.append([e, 1])
    votes.sort(key=lambda v: -v[1])
    needed = max(1, len(pages) // 2)
    edges = sorted(v[0] for v in votes if v[1] >= needed)
    return edges or [0.0]


def _assign_column(block, edges):
    """Index of the column edge nearest a block's left position."""
    x0 = block["bbox"][0]
    return min(range(len(edges)), key=lambda i: abs(x0 - edges[i]))


def _is_spanning(block, edges):
    """True if a block crosses a gutter between detected columns."""
    x0, _, x1, _ = block["bbox"]
    return any(x0 < e - 12 < x1 for e in edges[1:])


def _covered_cols(block, edges, page_width):
    """Column indices a block overlaps, based on column edge bands."""
    x0, _, x1, _ = block["bbox"]
    bounds = list(edges) + [page_width]
    return [
        i
        for i in range(len(edges))
        if x0 < bounds[i + 1] and x1 > bounds[i]
    ]


def _ordered_blocks(page, blocks, boilerplate, edges, drop_furniture=True):
    """Return page blocks in reading order for any column layout.

    ``blocks`` is the page's pre-extracted ``get_text("dict")["blocks"]``;
    ``page`` supplies only geometry (rect).

    Blocks above the first spanning element come first (page header zone).
    Each column then emits its own sequence — non-spanning blocks plus the
    spanning elements that cover it, in y order — columns left to right.
    A span covering several columns emits at its first encounter only.
    Text blocks whose signature is in ``boilerplate`` are dropped. When
    ``drop_furniture`` is false, the pattern/margin/digit drops are skipped
    so ``--keep-headers`` preserves running heads, page numbers, and
    margin strips verbatim.
    """
    rect = page.rect
    items = []
    for block in blocks:
        x0, y0, x1, y1 = block["bbox"]
        if block["type"] == 0:
            text = _block_text(block)
            if not text:
                continue
            near_edge = y1 < rect.height * EDGE_ZONE or y0 > rect.height * (
                1 - EDGE_ZONE
            )
            if near_edge and _signature(text) in boilerplate:
                continue
            if drop_furniture and (
                x1 - x0 < 25
                and y1 - y0 > 4 * (x1 - x0)
                and (x0 > rect.width * 0.9 or x1 < rect.width * 0.1)
            ):
                continue
            if (
                drop_furniture
                and (y0 > rect.height * 0.93 or y1 < rect.height * 0.07)
                and (
                    FURNITURE_RE.search(text)
                    or re.fullmatch(r"\d{1,4}", text)
                )
            ):
                continue
        items.append(block)

    key = lambda b: (b["bbox"][1], b["bbox"][0])
    spans = {id(b) for b in items if _is_spanning(b, edges)}
    nonspans = [b for b in items if id(b) not in spans]

    cut = min((b["bbox"][1] for b in items if id(b) in spans), default=None)
    top_zone = (
        sorted((b for b in nonspans if b["bbox"][3] < cut), key=key)
        if cut is not None
        else []
    )
    top_ids = {id(b) for b in top_zone}
    nonspans = (
        [b for b in nonspans if id(b) not in top_ids]
        if top_zone
        else nonspans
    )

    cols = {i: [] for i in range(len(edges))}
    for b in nonspans:
        cols[_assign_column(b, edges)].append(b)
    for b in items:
        if id(b) in spans:
            for c in _covered_cols(b, edges, rect.width):
                cols[c].append(b)
    for c in cols:
        cols[c].sort(key=key)

    ordered = list(top_zone)
    emitted = set()
    for c in sorted(cols):
        for b in cols[c]:
            if id(b) in emitted:
                continue
            emitted.add(id(b))
            ordered.append(b)
    return ordered


def _rotate_clip(rect, rot, mb):
    """Map a rect found in rotated (displayed) space back into mediabox
    coordinates for ``page.get_pixmap(clip=...)``.

    ``get_pixmap`` interprets the clip in the page's unrotated mediabox
    space, so a table region located on the displayed page must be pushed
    back through the page's own ``/Rotate``. For 90 the displayed x axis
    runs along the mediabox y axis while displayed y maps to ``width - x``;
    270 is the mirror image; 180 flips both axes. 0 returns the rect.
    """
    if rot == 90:
        return fitz.Rect(
            rect.y0, mb.width - rect.x1, rect.y1, mb.width - rect.x0
        )
    if rot == 270:
        return fitz.Rect(
            mb.height - rect.y1, rect.x0, mb.height - rect.y0, rect.x1
        )
    if rot == 180:
        return fitz.Rect(
            mb.width - rect.x1, mb.height - rect.y1,
            mb.width - rect.x0, mb.height - rect.y0,
        )
    return rect


def _line_html(line, body_size):
    """Render a line's spans to HTML: sup markers link to notes, font-name
    bold/italic suffixes become <b>/<i>."""
    parts = []
    for s in line["spans"]:
        text = html.escape(s["text"])
        if s["size"] < body_size * 0.75 and s["text"].strip():
            inner = html.escape(s["text"].strip())
            if s["text"].strip().isdigit() and len(inner) <= 4:
                parts.append(f'<sup><a href="notes.xhtml#fn{inner}">{inner}</a></sup>')
            else:
                parts.append(f"<sup>{inner}</sup>")
            continue
        if _span_is_bold(s):
            text = f"<b>{text}</b>"
        if _span_is_italic(s):
            text = f"<i>{text}</i>"
        parts.append(text)
    return "".join(parts)


def _footnote_text(block, body_size):
    """Render a footnote block's text; returns (marker_number, html)."""
    pieces = [_line_html(l, body_size) for l in _block_lines(block)]
    return _sup_marker(block, body_size), _join_lines(
        [p for p in pieces if p.strip()]
    )


def _strip_dead_fn_links(frag, fn_nums):
    """Unlink sup-markers whose ``#fnN`` target has no note entry — links to
    real notes survive; other raised digits (years, cross-refs) revert to
    plain superscripts. Applied to body fragments and, inside notes.xhtml
    itself, to everything past a note's leading marker."""
    return FN_LINK_RE.sub(
        lambda m: m.group(0)
        if int(m.group(1)) in fn_nums
        else f"<sup>{m.group(2)}</sup>",
        frag,
    )


def _render_page(page, page_index, state):
    """Render one page to HTML fragments; collect headings, images, footnotes.

    ``state`` carries ``page_frags`` (list of fragment lists, one per page),
    ``images`` (hash -> EpubItem), ``headings`` (as ``(level, text, anchor,
    page_index)`` tuples), ``footnotes`` (as ``(number|None, html)`` tuples),
    ``par`` / ``par_page`` (open paragraph fragments and the page they
    started on), ``heading_seq`` and ``boilerplate``.

    Blocks that start flush-left continue the open paragraph; blocks whose
    first line is indented start a new one. A paragraph that crosses a page
    boundary is emitted on the page where it began, so chapter splits never
    relocate text. Footnote-region blocks are pulled out of the flow into
    ``state['footnotes']``.
    """
    body_size = state["body_size"]
    edges = state["edges"]
    ordered = _ordered_blocks(
        page,
        state["page_items"][page_index],
        state["boilerplate"],
        edges,
        state["drop_edges"],
    )
    foot_tops = _footnote_tops(
        ordered, edges, body_size, page.rect.height, state["line_h"]
    )
    frags = state["page_frags"][page_index]

    tbl = _rotated_table_region(ordered)
    tbl_ids = (
        {id(b) for b in ordered if fitz.Rect(b["bbox"]).intersects(tbl)}
        if tbl is not None
        else set()
    )
    tbl_done = False

    def flush_par():
        if state["par"]:
            state["page_frags"][state["par_page"]].append(
                f"<p>{_join_lines(state['par'])}</p>"
            )
            state["par"] = []
        flush_item()

    def flush_item():
        if state["item"]:
            frags.append(
                f'<p class="listitem">{_join_lines(state["item"])}</p>'
            )
            state["item"] = []

    def emit_heading(level, text):
        flush_par()
        if level == 1:
            state["after_h1"] = True
        state["heading_seq"] += 1
        anchor = f"h{state['heading_seq']}"
        state["headings"].append((level, text, anchor, page_index))
        frags.append(f'<h{level} id="{anchor}">{html.escape(text)}</h{level}>')

    for b_i, block in enumerate(ordered):
        if block["type"] == 1:
            data = block.get("image")
            if data and len(data) >= MIN_IMAGE_BYTES:
                flush_par()
                digest = hashlib.sha1(data).hexdigest()
                if digest not in state["images"]:
                    ext = block.get("ext", "png")
                    name = f"images/img_{len(state['images'])}.{ext}"
                    state["images"][digest] = epub.EpubItem(
                        file_name=name,
                        media_type=f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}",
                        content=data,
                    )
                frags.append(
                    f'<p class="fig"><img src="{state["images"][digest].file_name}" alt=""/></p>'
                )
            continue

        if id(block) in tbl_ids:
            if not tbl_done:
                tbl_done = True
                flush_par()
                mb = page.mediabox
                tbl = fitz.Rect(
                    max(0, tbl.x0 - 8), max(0, tbl.y0 - 20),
                    min(mb.width, tbl.x1 + 8),
                    min(mb.height, tbl.y1 + 20),
                )
                clip = _rotate_clip(tbl, page.rotation, mb)
                pix = page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2))
                data = pix.tobytes("png")
                digest = hashlib.sha1(data).hexdigest()[:16]
                if digest not in state["images"]:
                    state["images"][digest] = epub.EpubItem(
                        file_name=f"images/table_{digest}.png",
                        media_type="image/png",
                        content=data,
                    )
                frags.append(
                    f'<p class="fig"><img src="{state["images"][digest].file_name}" alt="table"/></p>'
                )
            continue

        col = _assign_column(block, edges)
        if col in foot_tops and block["bbox"][1] >= foot_tops[col] - 5:
            state["footnotes"].append(_footnote_text(block, body_size))
            continue

        btext = _block_text(block)
        if (
            block["bbox"][1] < page.rect.height * 0.12
            and btext.isupper()
            and len(btext) < 30
        ):
            flush_par()
            frags.append(f'<p class="label">{html.escape(btext)}</p>')
            continue

        level = _standalone_heading_level(block, body_size)
        if (
            level == 2
            and _line_max_size(_block_lines(block)[0]) < body_size * 1.08
        ):
            # bold-only candidates carry no size signal: they must sit
            # outside the edge zones AND introduce following text in their
            # column — a trailing bold line at a section/page end is far
            # more often a note than a heading.
            in_edge = (
                block["bbox"][1] < page.rect.height * 0.12
                or block["bbox"][3] > page.rect.height * 0.88
            )
            follower = next(
                (
                    b
                    for b in ordered[b_i + 1 :]
                    if b["type"] == 0
                    and col in _covered_cols(b, edges, page.rect.width)
                ),
                None,
            )
            if in_edge or follower is None:
                level = None
        if level == 2 and page_index == 0 and not state["headings"]:
            if _line_max_size(_block_lines(block)[0]) >= body_size * 1.3:
                level = 1
        if level:
            emit_heading(
                level,
                " ".join(_line_text(l).strip() for l in _block_lines(block)),
            )
            continue

        med = _block_median_size(block)
        is_byline = med >= body_size * 1.15
        if is_byline or (state["after_h1"] and _authorish(btext)) or (
            frags
            and frags[-1].startswith('<p class="byline">')
            and len(_block_lines(block)) == 1
            and len(_line_text(block["lines"][0]).strip()) < 100
        ):
            flush_par()
            blines = [l for l in _block_lines(block) if _line_text(l).strip()]
            tail = _line_text(blines[-1]).strip() if blines else ""
            if len(tail) == 1 and tail.isupper() and tail not in "IA":
                blines = blines[:-1]
                state["par"] = [html.escape(tail)]
                state["par_page"] = page_index
            text = _join_lines(
                _line_html(l, body_size).strip() for l in blines
            )
            if frags and frags[-1].startswith('<p class="byline">'):
                frags[-1] = frags[-1][:-4] + " " + text + "</p>"
                state["byline_text"] = re.sub(r"<[^>]+>", "", frags[-1])
            else:
                frags.append(f'<p class="byline">{text}</p>')
                state["byline_text"] = re.sub(r"<[^>]+>", "", text)
            continue

        state["after_h1"] = False
        if (
            not state["abstract_done"]
            and page_index <= 1
            and 0.75 * body_size < med < 0.92 * body_size
            and block["bbox"][0] > page.rect.width * 0.15
            and block["bbox"][1] < page.rect.height * 0.5
        ):
            emit_heading(2, "Abstract")
            state["abstract_done"] = True

        if (
            block["bbox"][0] - edges[col] > 12
            and med <= body_size * 1.05
            and len(btext) > 60
        ):
            prev = ordered[b_i - 1] if b_i > 0 else None
            nxt = ordered[b_i + 1] if b_i + 1 < len(ordered) else None
            isolated = (
                prev is not None
                and prev["type"] == 0
                and _assign_column(prev, edges) == col
                and block["bbox"][1] - prev["bbox"][3] > state["line_h"] * 0.7
                and nxt is not None
                and nxt["type"] == 0
                and _assign_column(nxt, edges) == col
                and nxt["bbox"][1] - block["bbox"][3] > state["line_h"] * 0.7
            )
            if isolated:
                flush_par()
                qhtml = _join_lines(
                    _line_html(l, body_size).strip()
                    for l in _block_lines(block)
                    if _line_text(l).strip()
                )
                frags.append(f"<blockquote><p>{qhtml}</p></blockquote>")
                continue

        new_par = _first_line_indent(block) > 4
        if not new_par and state["par"]:
            tail = re.sub(r"<[^>]+>", "", state["par"][-1]).rstrip()
            new_par = bool(re.search(r"[.!?…][\"”’)\]]*$", tail))
        if new_par and not (
            len(state["par"]) == 1 and len(state["par"][0]) == 1
        ):
            flush_par()
        for li, line in enumerate(_block_lines(block)):
            if li == 0:
                m = _section_label(_line_text(line).strip())
                if m:
                    emit_heading(2, m[0])
                    if m[1]:
                        state["par"].append(html.escape(m[1]))
                    continue
            # blocks opening with a footnote marker are note material —
            # none of their lines may classify as headings
            line_level = (
                _heading_level(line, body_size)
                if btext[:1] not in "*†‡§"
                else None
            )
            if line_level:
                emit_heading(line_level, _line_text(line).strip())
                continue
            t = _line_html(line, body_size).strip()
            if not t:
                continue
            if LIST_ITEM_RE.match(_line_text(line).strip()):
                flush_par()
                state["item"] = [t]
                continue
            if state["item"]:
                state["item"].append(t)
                continue
            if not state["par"]:
                state["par_page"] = page_index
            state["par"].append(t)
        flush_item()


def convert(pdf_path, out_path, title=None, author=None, drop_edges=True):
    """Convert ``pdf_path`` to an EPUB at ``out_path``; returns a stats dict."""
    doc = fitz.open(pdf_path)
    meta = doc.metadata or {}
    # single extraction pass: every analysis/render step reads these dicts
    pages = [(page, page.get_text("dict")) for page in doc]

    body_size, line_h = _body_font_size(pages)
    state = {
        "body_size": body_size,
        "line_h": line_h,
        "page_items": [pdict["blocks"] for _p, pdict in pages],
        "edges": _doc_column_edges(pages),
        "abstract_done": False,
        "after_h1": False,
        "byline_text": None,
        "images": {},
        "headings": [],
        "footnotes": [],
        "par": [],
        "item": [],
        "par_page": 0,
        "page_frags": [[] for _ in range(doc.page_count)],
        "heading_seq": 0,
        "drop_edges": drop_edges,
        "boilerplate": (
            _boilerplate_signatures(pages)
            if drop_edges and doc.page_count > 3
            else set()
        ),
    }

    for i, (page, _pdict) in enumerate(pages):
        _render_page(page, i, state)
    if state["par"]:
        state["page_frags"][state["par_page"]].append(
            f"<p>{_join_lines(state['par'])}</p>"
        )
    fn_nums = {n for n, _ in state["footnotes"] if n is not None}
    for frags in state["page_frags"]:
        for k, frag in enumerate(frags):
            frags[k] = _strip_dead_fn_links(frag, fn_nums)

    flat = [
        (pi, frag)
        for pi, frags in enumerate(state["page_frags"])
        for frag in frags
    ]
    boundaries = [
        i for i, (_, f) in enumerate(flat) if '<h1 id="' in f or '<h2 id="' in f
    ]
    if not boundaries:
        boundaries = [
            i
            for i, (pi, _) in enumerate(flat)
            if pi % PAGES_PER_FALLBACK_CHAPTER == 0
            and (i == 0 or flat[i - 1][0] != pi)
        ]
    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    if title is None:
        title = meta.get("title") or ""
        if not _valid_title(title):
            title = next(
                (t for lv, t, _a, _p in state["headings"] if lv == 1),
                pdf_path.stem,
            )
    if author is None:
        author = meta.get("author") or ""
        if not _valid_title(author):
            author = state.get("byline_text") or "Unknown"
            author = re.split(
                r"\s+(?:Department|University|Institute|Center|College|School of)\b",
                author,
            )[0]
            author = re.sub(r"[*†‡§]", "", author).strip(" ,")
        else:
            author = author.strip()

    book = epub.EpubBook()
    book.set_identifier(hashlib.sha1(str(pdf_path).encode()).hexdigest()[:16])
    book.set_title(title)
    book.set_language(BOOK_LANG)
    book.add_author(author)

    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=(
            "p.byline { font-style: italic; } "
            "p.label { font-size: 0.8em; letter-spacing: 0.1em; "
            "text-transform: uppercase; color: #666; } "
            "p.fig { text-align: center; } p.fig img { max-width: 100%; } "
            "p.listitem { margin-left: 2em; } "
            "blockquote { margin: 0.8em 1.5em; font-size: 0.95em; }"
        ),
    )
    book.add_item(css)

    chapters = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(flat)
        content = "\n".join(f for _, f in flat[start:end])
        heading = next(
            (
                re.sub(r"<[^>]+>", "", f)
                for _, f in flat[start:end]
                if f.startswith("<h")
            ),
            None,
        )
        chap = epub.EpubHtml(
            title=heading or f"Section {idx + 1}",
            file_name=f"chap_{idx + 1}.xhtml",
            lang=BOOK_LANG,
        )
        chap.add_item(css)
        chap.content = (
            f"<html><body>{content.strip() or '<p></p>'}</body></html>"
        )
        book.add_item(chap)
        chapters.append((chap, start))

    note_entries = []
    seen_fn = set()
    for num, frag in state["footnotes"]:
        frag = re.sub(
            r"^<sup><a[^>]*>(\d+)</a></sup>", r"<sup>\1</sup>", frag
        )
        # note bodies get the same dead-anchor validation as body text —
        # a raised digit mid-note (e.g. a superscripted year) is not a link
        frag = _strip_dead_fn_links(frag, fn_nums)
        # an unnumbered block is a column/page-split continuation of the
        # previous note unless it opens a new note itself (correspondence
        # and acknowledgment openers — the same pattern that marks the
        # note zone on the page)
        frag_text = re.sub(r"<[^>]+>", "", frag).lstrip("*†‡§ ")
        if (
            num is None
            and note_entries
            and not NOTE_START_RE.match(frag_text)
        ):
            note_entries[-1] = note_entries[-1][:-4] + " " + frag + "</p>"
        elif num is not None and num not in seen_fn:
            seen_fn.add(num)
            note_entries.append(f'<p class="fn" id="fn{num}">{frag}</p>')
        else:
            note_entries.append(f'<p class="fn">{frag}</p>')

    anchor_pos = {}
    for i, (_, f) in enumerate(flat):
        m = re.search(r'id="(h\d+)"', f)
        if m:
            anchor_pos[m.group(1)] = i
    book.toc = []
    for level, text, anchor, _page_i in state["headings"]:
        pos = anchor_pos.get(anchor, 0)
        chap_idx = max(j for j, (_, s) in enumerate(chapters) if s <= pos)
        book.toc.append(
            epub.Link(f"{chapters[chap_idx][0].file_name}#{anchor}", text, anchor)
        )
    if note_entries:
        notes = epub.EpubHtml(
            title="Notes", file_name="notes.xhtml", lang=BOOK_LANG
        )
        notes.content = (
            "<html><body><h1>Notes</h1>" + "".join(note_entries) + "</body></html>"
        )
        book.add_item(notes)
        chapters.append((notes, len(flat)))
        book.toc.append(epub.Link("notes.xhtml", "Notes", "notes"))
    for item in state["images"].values():
        book.add_item(item)
    book.spine = ["nav"] + [c for c, _ in chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(out_path), book)
    return {
        "title": title,
        "pages": doc.page_count,
        "chapters": len(chapters),
        "headings": len(state["headings"]),
        "footnotes": len(state["footnotes"]),
        "images": len(state["images"]),
        "output": str(out_path),
    }


def main(argv=None):
    """Parse CLI arguments and run the conversion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PDF file to convert")
    parser.add_argument("-o", "--output", type=Path, help="output .epub path")
    parser.add_argument("--title", help="override EPUB title")
    parser.add_argument("--author", help="override EPUB author")
    parser.add_argument(
        "--keep-headers",
        action="store_true",
        help="keep header/footer strips (page numbers, running heads)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        parser.error(f"not a file: {args.input}")
    out = args.output or args.input.with_suffix(".epub")

    stats = convert(
        args.input, out, title=args.title, author=args.author,
        drop_edges=not args.keep_headers,
    )
    print(
        f"{stats['output']}: {stats['pages']} pages -> {stats['chapters']} "
        f"chapter(s), {stats['headings']} heading(s), "
        f"{stats['footnotes']} footnote(s), {stats['images']} image(s)"
    )


if __name__ == "__main__":
    sys.exit(main())
