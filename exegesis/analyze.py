"""Document- and page-level analysis: body font metrics, boilerplate
signatures, column geometry, rotated- and horizontal-table regions,
footnote zones, and the /Rotate clip transform. Operates on the page
dicts extracted once in ``emit.convert``."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Container
from dataclasses import dataclass, field
from statistics import median

import fitz

from .const import BOILERPLATE_COVERAGE, EDGE_ZONE
from .text import (
    FURNITURE_RE,
    NOTE_START_RE,
    TABLE_CAPTION_RE,
    _block_lines,
    _block_median_size,
    _block_text,
    _span_is_bold,
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


def _doc_column_edges(pages, boilerplate: Container[str] = frozenset()):
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


# first column made almost entirely of enumeration/bullet marks is a
# list, not a table — rejecting it keeps list rendering intact
_LIST_CELL_RE = re.compile(r"^\s*(?:\d{1,3}[.)]|[•·\-–—*†‡§])\s*$")


@dataclass
class _TableRegion:
    """An upright grid table detected on a page.

    ``claimed`` holds the ``id()`` of every block the table swallows
    (cells plus caption); ``cols`` are the recovered column x-bounds;
    ``rows`` are emitted in order, each a list of ``(col, span, lines)``
    cells — ``lines`` are raw dict lines so the renderer can reuse
    ``_line_html`` for bold/italic/sup markers.
    """

    rect: fitz.Rect
    claimed: set = field(default_factory=set)
    cols: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    header: bool = False
    caption: list | None = None


def _upright_lines(blocks):
    """All ``(line, block_id)`` pairs of text blocks, minus rotated text.

    Turned-table and vertical-text lines are taller than wide; they must
    not seed row bands here (the rotated-table path owns them).
    """
    out = []
    for b in blocks:
        if b["type"] != 0:
            continue
        for l in _block_lines(b):
            x0, y0, x1, y1 = l["bbox"]
            if x1 - x0 >= (y1 - y0) * 0.5:
                out.append((l, id(b)))
    return out


def _line_bands(lines, line_h):
    """Cluster lines into row bands: groups whose y-ranges overlap.

    Bands are the atomic rows of grid analysis — cells in a band share a
    vertical slice regardless of which block they came from.
    """
    bands = []
    for l, bid in sorted(lines, key=lambda lb: (lb[0]["bbox"][1], lb[0]["bbox"][0])):
        y0, y1 = l["bbox"][1], l["bbox"][3]
        if bands and y0 < bands[-1][2] - 0.35 * line_h:
            bands[-1][0].append((l, bid))
            bands[-1][2] = max(bands[-1][2], y1)
        else:
            bands.append([[(l, bid)], y0, y1])
    return bands


def _band_cells(band_lines, min_gutter):
    """Split one band's lines into cells at x-gaps of ≥ ``min_gutter``.

    Returns ``[(x0, x1, [(line, block_id), ...])]`` left to right; lines
    closer than the gutter merge into one cell fragment.
    """
    cells = []
    for l, bid in sorted(band_lines, key=lambda lb: lb[0]["bbox"][0]):
        x0, x1 = l["bbox"][0], l["bbox"][2]
        if cells and x0 - cells[-1][1] >= min_gutter:
            cells.append([x0, x1, [(l, bid)]])
        elif cells:
            cells[-1][1] = max(cells[-1][1], x1)
            cells[-1][2].append((l, bid))
        else:
            cells.append([x0, x1, [(l, bid)]])
    return cells


def _interior_gaps(intervals, lo, hi, min_gutter):
    """Gaps in ``[lo, hi]`` left uncovered by every interval, ≥ min_gutter.

    These are the permanent vertical whitespace strips that delimit table
    columns: no row's text may cross them.
    """
    gaps = []
    cur = lo
    for a, b in sorted(intervals):
        if a - cur >= min_gutter:
            gaps.append((cur, a))
        cur = max(cur, b)
    return gaps


def _grid_runs(bands, min_gutter, max_gap):
    """Group consecutive bands into runs whose union keeps ≥1 gutter.

    A band joins the open run only if its cells preserve a permanent
    interior whitespace strip — a full-width paragraph line kills every
    gutter and so closes the run (body text can't infiltrate a table).
    Wide single-cell bands are held pending: absorbed as colspan rows if
    a multi-cell band resumes the grid, dropped as ordinary text if not.
    """
    runs = []
    cur = None  # [bands, union, pending]
    for band in bands:
        lines, y0, _y1 = band
        cells = _band_cells(lines, min_gutter)
        if cur is not None and y0 - cur[0][-1][2] > max_gap:
            runs.append(cur[0])
            cur = None
        if len(cells) >= 2:
            ivs = [(c[0], c[1]) for c in cells]
            if cur is None:
                cur = [ [band], ivs, [] ]
                continue
            union = sorted(cur[1] + ivs)
            lo = min(u[0] for u in union)
            hi = max(u[1] for u in union)
            if _interior_gaps(union, lo, hi, min_gutter):
                cur[0].extend(cur[2])
                cur[2] = []
                cur[0].append(band)
                cur[1] = union
            else:
                runs.append(cur[0])
                cur = [[band], ivs, []]
        elif cur is not None and cells:
            cx0, cx1 = cells[0][0], cells[0][1]
            lo = min(u[0] for u in cur[1])
            hi = max(u[1] for u in cur[1])
            if cx1 - cx0 < 0.7 * (hi - lo):
                # narrow lone cell — a wrapped-cell stub; it can't kill a
                # gutter so it joins without touching the union
                cur[0].extend(cur[2])
                cur[2] = []
                cur[0].append(band)
            else:
                cur[2].append(band)
    if cur is not None:
        runs.append(cur[0])
    return runs


def _run_layout(bands, min_gutter):
    """Recover column bounds from a run's cell union.

    Column i spans from the end of the previous permanent gutter (or the
    region's left edge) to the start of the next (or the right edge).
    Returns ``(region_rect, cols)`` or None when no gutter survives.
    """
    ivs = []
    x0s, y0s, x1s, y1s = [], [], [], []
    for lines, y0, y1 in bands:
        cells = _band_cells(lines, min_gutter)
        ivs += [(c[0], c[1]) for c in cells]
        x0s.append(min(c[0] for c in cells))
        x1s.append(max(c[1] for c in cells))
        y0s.append(y0)
        y1s.append(y1)
    lo, hi = min(x0s), max(x1s)
    gaps = _interior_gaps(ivs, lo, hi, min_gutter)
    if not gaps:
        return None
    bounds = []
    prev = lo
    for g0, g1 in gaps:
        bounds.append((prev, g0))
        prev = g1
    bounds.append((prev, hi))
    return fitz.Rect(lo, min(y0s), hi, max(y1s)), bounds


def _cells_cols(band, cols, min_gutter):
    """Assign a band's cells to column spans.

    A cell covers a column when it overlaps more than a third of the
    column's width; multi-column coverage becomes a colspan. Returns a
    list of ``(col_start, span, cell)`` sorted by column.
    """
    out = []
    for c in _band_cells(band[0], min_gutter):
        covered = [
            i
            for i, (a, b) in enumerate(cols)
            if min(c[1], b) - max(c[0], a) > 0.35 * (b - a)
            or a - 1 <= c[0] <= b
        ]
        if covered:
            out.append((covered[0], covered[-1] - covered[0] + 1, c))
    return out


def _assemble_rows(bands, cols, min_gutter, claimed):
    """Turn run bands into table rows, folding wrapped-cell continuations.

    A band whose occupied columns are a *strict* subset of the previous
    row's, with column 0 empty, and covering at most ``ncols - 2``
    columns is a continuation: each of its cells appends to the covering
    cell above (multi-line cell text). A band missing only column 0 is a
    real sparse row (empty category cell), not a wrap. Only lines of
    claimed blocks contribute cell text — straddlers stay in the body
    flow instead of being swallowed.
    """
    rows = []
    for band in bands:
        assigns = [
            (col, span, [lb for lb in c[2] if lb[1] in claimed])
            for col, span, c in _cells_cols(band, cols, min_gutter)
        ]
        assigns = [a for a in assigns if a[2]]
        if not assigns:
            continue
        occupied = {
            c for col, span, _l in assigns for c in range(col, col + span)
        }
        if (
            rows
            and 0 not in occupied
            and occupied < rows[-1][0]
            and len(occupied) <= max(1, len(cols) - 2)
        ):
            for col, span, lns in assigns:
                for pcol, pspan, plns in rows[-1][1]:
                    if pcol <= col < pcol + pspan:
                        plns += lns
                        break
                else:
                    rows[-1][1].append((col, span, lns))
                    rows[-1][1].sort(key=lambda t: t[0])
            rows[-1][0] |= occupied
            continue
        rows.append([occupied, assigns])
    return [assigns for _occ, assigns in rows]


def _join_cell_text(lns):
    """Plain text of a cell's ``(line, block_id)`` pairs."""
    return " ".join(
        "".join(s["text"] for s in l["spans"]).strip() for l, _ in lns
    ).strip()


def _table_caption(sbands, run, line_h):
    """The band directly above or below the run if it reads as
    ``Table N ...`` — used as the ``<caption>`` and dropped from text.
    Bands iterate top-down, so an above-run match wins over a below one.
    """
    ry0 = min(b[1] for b in run)
    ry1 = max(b[2] for b in run)
    for band in sbands:
        _l, y0, y1 = band
        text = " ".join(
            "".join(s["text"] for s in l["spans"]) for l, _ in band[0]
        )
        if (
            0 <= ry0 - y1 <= 2.5 * line_h
            or 0 <= y0 - ry1 <= 2.5 * line_h
        ) and TABLE_CAPTION_RE.match(text):
            return band
    return None


def _region_from_run(run, blocks, sbands, edges, min_gutter, line_h,
                     multi_col):
    """Validate one grid run into a ``_TableRegion`` or reject it.

    Guards (precision-first — a miss degrades to ordinary text flow, a
    false positive mangles it): needs ≥3 recovered rows, ≥60% grid-dense
    rows, median band fill under 0.88 (prose fills its band), a first
    column that isn't just list markers, and no image overlap. Runs that
    span a document column edge additionally need a ``Table N`` caption
    AND interior gutters that don't merely mirror the page's own column
    edges — the page layout itself is not a table.
    """
    lay = _run_layout(run, min_gutter)
    if lay is None:
        return None
    rect, cols = lay
    if len(cols) < 2:
        return None
    spans_edge = multi_col and any(
        rect.x0 < e < rect.x1 for e in edges[1:]
    )
    if spans_edge and len(cols) - 1 <= len(edges) - 1 and all(
        any(
            cols[i][1] - 12 <= e <= cols[i + 1][0] + 12 for e in edges[1:]
        )
        for i in range(len(cols) - 1)
    ):
        return None
    caption_band = _table_caption(sbands, run, line_h)
    if spans_edge and caption_band is None:
        return None

    claim_rect = fitz.Rect(
        rect.x0 - 4, rect.y0 - 0.4 * line_h,
        rect.x1 + 4, rect.y1 + 0.4 * line_h,
    )
    member_bands = list(run)
    if caption_band is not None:
        member_bands = member_bands + [caption_band]
        cy0, cy1 = caption_band[1], caption_band[2]
        claim_rect.y0 = min(claim_rect.y0, cy0 - 0.3 * line_h)
        claim_rect.y1 = max(claim_rect.y1, cy1 + 0.3 * line_h)
    if any(
        b["type"] == 1 and fitz.Rect(b["bbox"]).intersects(rect)
        for b in blocks
    ):
        return None
    # a block joins the table only if it sits inside the claim rect AND
    # actually contributes lines to a member band — blocks that merely
    # fall inside the padded rect stay in the body flow instead of
    # vanishing; a caption whose block is a straddler isn't used either
    member_bids = {
        bid for band in member_bands for _l, bid in band[0]
    }
    claimed = {
        id(b)
        for b in blocks
        if b["type"] == 0
        and id(b) in member_bids
        and fitz.Rect(b["bbox"]) in claim_rect
    }
    caption = None
    if caption_band is not None and {
        bid for _l, bid in caption_band[0]
    } <= claimed:
        caption = [l for l, _ in caption_band[0]]
    rows = _assemble_rows(run, cols, min_gutter, claimed)
    if len(rows) < (2 if caption is not None else 3):
        return None
    dense = sum(len(r) >= 2 for r in rows) / len(rows)
    if dense < 0.6:
        return None
    fills = [
        sum(c[1] - c[0] for c in _band_cells(b[0], min_gutter))
        / max(1.0, rect.width)
        for b in run
        if len(_band_cells(b[0], min_gutter)) >= 2
    ]
    if fills and median(fills) > 0.88:
        return None
    # prose guards — uncaptioned only: cells that fill their columns
    # edge-to-edge or read like sentence fragments are wrapped paragraph
    # lines, not table content (real data cells are short and narrow)
    cell_fills = []
    cell_chars = []
    for band in run:
        for c in _band_cells(band[0], min_gutter):
            covered = [
                i
                for i, (a, b) in enumerate(cols)
                if min(c[1], b) - max(c[0], a) > 0.35 * (b - a)
                or a - 1 <= c[0] <= b
            ]
            if not covered:
                continue
            cw = sum(
                cols[i][1] - cols[i][0]
                for i in range(covered[0], covered[-1] + 1)
            )
            cell_fills.append((c[1] - c[0]) / max(1.0, cw))
            cell_chars.append(len(_join_cell_text(c[2])))
    if caption is None:
        if cell_fills and median(cell_fills) > 0.9:
            return None
        if cell_chars and median(cell_chars) > 45:
            return None
    col0_texts = [
        _join_cell_text(c[2])
        for band in run
        for c in _band_cells(band[0], min_gutter)
        if c[0] <= cols[0][1] and c[1] > cols[0][0]
    ]
    if col0_texts and sum(
        bool(_LIST_CELL_RE.match(t)) for t in col0_texts
    ) / len(col0_texts) > 0.6:
        return None
    return _TableRegion(
        rect=rect,
        claimed=claimed,
        cols=cols,
        rows=rows,
        header=bool(
            rows
            and all(
                _line_is_boldish(l)
                for _col, _span, lns in rows[0]
                for l, _bid in lns
            )
        ),
        caption=caption,
    )


def _line_is_boldish(line):
    """Every non-empty span bold — header-row signal for tables."""
    spans = [s for s in line["spans"] if s["text"].strip()]
    return bool(spans) and all(_span_is_bold(s) for s in spans)


def _table_regions(blocks, line_h, edges, page_width):
    """Detect upright grid tables; returns a list of ``_TableRegion``.

    Candidate row bands must share permanent interior whitespace (the
    column gutters). Runs are found per document column and once at full
    page width when the document is multi-column.
    """
    bands = _line_bands(_upright_lines(blocks), line_h)
    min_gutter = max(6.0, 0.45 * line_h)
    max_gap = 3.0 * line_h

    bounds = list(edges) + [page_width]
    x_bands = [(e - 20, bounds[i + 1] - 8) for i, e in enumerate(edges)]
    if len(edges) > 1:
        x_bands.append((0.0, page_width))

    regions = []
    for bx0, bx1 in x_bands:
        sel = [
            (l, bid)
            for band in bands
            for l, bid in band[0]
            if l["bbox"][0] >= bx0 and l["bbox"][2] <= bx1
        ]
        sbands = _line_bands(sel, line_h)
        for run in _grid_runs(sbands, min_gutter, max_gap):
            region = _region_from_run(
                run, blocks, sbands, edges, min_gutter, line_h,
                len(edges) > 1,
            )
            if region is not None:
                regions.append(region)
    # the same table can surface in both a column-local and the
    # full-width scan — on overlapping claims keep the richer region
    regions.sort(key=lambda r: -len(r.rows))
    kept = []
    for r in regions:
        if any(r.claimed & k.claimed for k in kept):
            continue
        kept.append(r)
    return kept


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
