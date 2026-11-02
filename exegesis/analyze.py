"""Document- and page-level analysis: body font metrics, boilerplate
signatures, column geometry, rotated-table regions, footnote zones, and
the /Rotate clip transform. Operates on the page dicts extracted once in
``emit.convert``."""
import re
from collections import Counter
from statistics import median

import fitz

from .const import BOILERPLATE_COVERAGE, EDGE_ZONE
from .text import (
    FURNITURE_RE,
    NOTE_START_RE,
    _block_lines,
    _block_median_size,
    _block_text,
)


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


def _doc_column_edges(pages, boilerplate=frozenset()):
    """Column edges for the document: edges found on ≥half of all pages.

    Per-page clustering produces phantom edges from bylines, headings, and
    other spanning elements; real columns repeat on most pages, so only
    edges that recur survive. Prevents mid-page splits that corrupt both
    ordering and footnote-gap detection.
    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs.
    """
    votes = []
    for page, pdict in pages:
        # furniture shapes can't form column edges — a sidebar or running
        # head recurring at the same x on every page is the classic
        # phantom-edge generator
        items = [
            b
            for b in pdict["blocks"]
            if b["type"] == 0
            and not _is_furniture(b, page.rect, _block_text(b))
            and not _is_signature_furniture(
                b, page.rect.height, boilerplate
            )
        ]
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


def _overlap_cols(bbox, edges, page_width, tol=12):
    """Columns a rect genuinely occupies: its home column (the edge nearest
    x0) plus any column overlapped by at least ``tol`` points.

    ``_covered_cols`` counts any pixel of overlap, so a block bleeding a
    point across the modal gutter claims both columns — fine for ordering
    but too eager for identity tests like caption attribution, where a
    jittery OCR-layer bbox must not borrow the adjacent column.
    """
    x0, _, x1, _ = bbox
    bounds = list(edges) + [page_width]
    cols = {_assign_column({"bbox": bbox}, edges)}
    for i in range(len(edges)):
        if min(x1, bounds[i + 1]) - max(x0, bounds[i]) >= tol:
            cols.add(i)
    return cols


def _is_margin_strip(block, rect):
    """Narrow+tall block in the outer 10% margins — rotated sidebar
    furniture (journal titles running down the page edge)."""
    x0, y0, x1, y1 = block["bbox"]
    return (
        x1 - x0 < 25
        and y1 - y0 > 4 * (x1 - x0)
        and (x0 > rect.width * 0.9 or x1 < rect.width * 0.1)
    )


def _is_furniture(block, rect, text):
    """Edge furniture by shape or content: narrow+tall margin strips in
    the outer 10% margins, or pattern furniture/page numbers in the outer
    7% edge."""
    _, y0, _, y1 = block["bbox"]
    return _is_margin_strip(block, rect) or (
        (y0 > rect.height * 0.93 or y1 < rect.height * 0.07)
        and (FURNITURE_RE.search(text) or re.fullmatch(r"\d{1,4}", text))
    )


def _is_signature_furniture(block, height, boilerplate):
    """Edge-zone text whose normalized signature repeats across pages —
    a running head/footer detected by recurrence rather than content."""
    _, y0, _, y1 = block["bbox"]
    near_edge = y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE)
    return near_edge and _signature(_block_text(block)) in boilerplate


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


def _rotate_clip(rect, matrix):
    """Map a mediabox-space rect into the displayed space that
    ``page.get_pixmap(clip=...)`` expects; pass ``page.rotation_matrix``.

    ``get_text`` reports block bboxes in the page's unrotated mediabox
    coordinates, while ``get_pixmap`` interprets the clip in the rotated
    (displayed) coordinate system — on a ``/Rotate`` page the same table
    region sits somewhere else. ``rotation_matrix`` is exactly this map
    (the identity on unrotated pages).
    """
    return rect * matrix


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
