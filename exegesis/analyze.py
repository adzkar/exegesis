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

from .const import (
    BOILERPLATE_COVERAGE,
    BOILERPLATE_MAX_CHARS,
    BOILERPLATE_MAX_LINES,
    EDGE_ZONE,
    SIGNATURE_PREFIX_MAX_LINES,
)
from .lang import EN, LangProfile
from .text import (
    _block_lines,
    _block_median_size,
    _block_text,
    _join_lines,
    _line_text,
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
    The block must stay inside ``BOILERPLATE_MAX_LINES`` lines and
    ``BOILERPLATE_MAX_CHARS`` normalized letters — bounds sized for real
    running heads (a five-line license head runs ~265 letters), so a
    page-spanning repeated block like a continued-table header can't
    excuse itself. ``pages`` is a list of ``(fitz.Page,
    page.get_text("dict"))`` pairs.
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
                if (
                    3 <= len(sig) <= BOILERPLATE_MAX_CHARS
                    and len(_block_lines(block)) <= BOILERPLATE_MAX_LINES
                ):
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


def _doc_column_edges(
    pages,
    boilerplate: Container[str] = frozenset(),
    lang: LangProfile = EN,
):
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
            and not _is_furniture(
                b, page, _block_text(b, lang), lang
            )
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


def _line_dir(line):
    """Axis direction of a line's reported ``dir``, or None when unreliable.

    ``dir`` is reported in unrotated mediabox space; only near-axis
    vectors count — diagonal, absent, or zero data can neither prove a
    line rotated nor veto the block as upright.
    """
    dx, dy = line.get("dir") or (0, 0)
    if abs(dx) > 0.9 >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    if abs(dy) > 0.9 >= abs(dx):
        return (0, 1 if dy > 0 else -1)
    return None


def _upright_dir(page):
    """The page's upright reading direction in mediabox coordinate space.

    ``page.rotation_matrix`` carries a translation, so the pure rotation
    comes from ``fitz.Matrix(page.rotation)``: displayed-upright (1,0)
    mapped back through the inverse gives the mediabox ``dir`` a normal
    text line reports — (0,-1) on a /Rotate 90 page, (0,1) on /Rotate 270.
    """
    d = fitz.Point(1, 0) * ~fitz.Matrix(page.rotation)
    return (round(d.x), round(d.y))


def _is_margin_strip(block, page, clip=None):
    """Rotated-text strip in the outer 10% margins — rotated sidebar
    furniture (journal titles and preprint stamps running down the page
    edge, like ``arXiv:NNNN.NNNNN`` which overflows the legacy 25pt cap).

    The block must still be tall (>4x height/width) and sit in the outer
    margin; bbox and margin width compare in the same unrotated space
    (``page.mediabox``), because displayed ``page.rect`` swaps dimensions
    on /Rotate pages. A block qualifies when every line's ``dir`` is a
    reliable axis direction differing from the page's upright reading
    direction — any reliably upright line vetoes the classification for
    the whole block, regardless of width, so text that reads upright on
    screen (including on rotated pages) can never be dropped as a strip.
    When direction data is missing or unusable on some line the legacy
    <25pt shape applies as fallback — the veto still holds first.
    ``clip`` exempts blocks inside the rotated-table region ``order``
    builds via ``_strip_clip``: a table's outer cells share the strip's
    geometry but belong to the rasterized grid. The clip's legitimacy is
    established there — only band members reaching the body band seed
    it, so a run of margin strips forms no clip and cannot exempt its
    own members."""
    x0, y0, x1, y1 = block["bbox"]
    width = x1 - x0
    margin = page.mediabox.width
    if not (
        y1 - y0 > 4 * width
        and (x0 > margin * 0.9 or x1 < margin * 0.1)
    ):
        return False
    if clip is not None and fitz.Rect((x0, y0, x1, y1)).intersects(clip):
        return False
    upright = _upright_dir(page)
    dirs = [_line_dir(line) for line in _block_lines(block)]
    if any(d == upright for d in dirs):
        return False
    if dirs and all(d is not None for d in dirs):
        return True
    return width < 25


def _is_pattern_furniture(block, page, text, lang: LangProfile = EN):
    """Pattern furniture or a pure page number in the outer 7% edge —
    content patterns come from the document's language profile, and the
    zone compares displayed ``page.rect`` like the signature layer."""
    _, y0, _, y1 = block["bbox"]
    return (y0 > page.rect.height * 0.93 or y1 < page.rect.height * 0.07) and (
        lang.furniture.search(text)
        or re.fullmatch(r"\d{1,4}", text)
    )


def _is_furniture(block, page, text, lang: LangProfile = EN, clip=None):
    """Edge furniture by shape or content: rotated margin strips in the
    outer 10% margins, or pattern furniture/page numbers in the outer 7%
    edge. Content patterns come from the document's language profile.
    ``clip`` exempts the strip arm only (a rotated table's outer cells);
    edge zones stay on displayed ``page.rect`` like the signature layer."""
    return _is_margin_strip(block, page, clip) or _is_pattern_furniture(
        block, page, text, lang
    )


def _is_signature_furniture(block, height, boilerplate):
    """Edge-zone text whose normalized signature repeats across pages —
    a running head/footer detected by recurrence rather than content."""
    _, y0, _, y1 = block["bbox"]
    near_edge = y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE)
    return near_edge and _signature(_block_text(block)) in boilerplate


def _lines_bbox(lines):
    """Union bbox of a line list — the recomputed bounds of a split block."""
    return (
        min(l["bbox"][0] for l in lines),
        min(l["bbox"][1] for l in lines),
        max(l["bbox"][2] for l in lines),
        max(l["bbox"][3] for l in lines),
    )


def _signature_prefix_len(block, height, boilerplate, lang: LangProfile = EN):
    """Leading lines whose joined signature is boilerplate, else 0.

    A running head fused to the block below it (a title, an abstract
    opener) escapes ``_is_signature_furniture`` twice over: the whole
    block's signature never matches and its bbox straddles the edge
    zone. The prefix test fires only when the block's *first* line lies
    in an edge zone — the line-level version of the signature arm's zone
    test — then joins leading lines incrementally, bounded by
    ``SIGNATURE_PREFIX_MAX_LINES`` (the boilerplate set's own line
    bound — a matched join is already a verified recurring signature)
    and never consuming the whole block: a block whose whole signature
    is already boilerplate is the arm's own case and returns 0 outright
    — splitting it would drop the head lines but leak the remainder,
    which is not independently boilerplate, into body flow. The scan
    keeps the *longest* in-zone match, not the first: alternating heads
    nest as prefixes of each other (verso `JournalX`, recto `JournalX /
    Vol Twelve`), and a longer verified signature is always at least as
    correct — stopping at k=1 would leave the real head's tail fused to
    the body block, the same leak the split exists to fix. The matched
    prefix's union bbox must stay inside the zone: the split head
    classifies as signature furniture organically downstream, while a
    prefix straddling the zone boundary would leak into body flow as a
    paragraph. The union only grows with each added line, so leaving
    the zone is monotone and ends the scan.
    """
    lines = _block_lines(block)
    if len(lines) < 2 or not boilerplate:
        return 0
    first = lines[0]["bbox"]
    if not (
        first[3] < height * EDGE_ZONE
        or first[1] > height * (1 - EDGE_ZONE)
    ):
        return 0
    if _signature(_block_text(block, lang)) in boilerplate:
        return 0
    texts = [_line_text(line).strip() for line in lines]
    best = 0
    for k in range(
        1, min(SIGNATURE_PREFIX_MAX_LINES, len(lines) - 1) + 1
    ):
        _, py0, _, py1 = _lines_bbox(lines[:k])
        if not (
            py1 < height * EDGE_ZONE or py0 > height * (1 - EDGE_ZONE)
        ):
            break
        if _signature(_join_lines(texts[:k], lang)) in boilerplate:
            best = k
    return best


def _split_signature_prefixes(blocks, height, boilerplate, lang: LangProfile = EN):
    """Split fused signature prefixes off ``blocks``, returning a new list.

    Each block whose leading lines join into a boilerplate signature
    (see ``_signature_prefix_len``) becomes two blocks: the matched
    prefix — signature furniture everywhere downstream, dropped by
    default and kept verbatim at the page edge under ``--keep-headers``
    — and the remainder, an ordinary block with a recomputed bbox. The
    remainder re-enters the scan, so stacked heads each independently
    recurring (two running heads fused ahead of one title) split off one
    at a time instead of the second leaking into the body block. Runs
    once per page after the boilerplate set is built and before
    ordering, so column-edge voting, the strip clip, and render all see
    the split blocks — a recurring fused head can no longer vote a
    phantom column edge either.
    """
    out = []
    for block in blocks:
        rest = block
        while rest["type"] == 0:
            k = _signature_prefix_len(rest, height, boilerplate, lang)
            if not k:
                break
            lines = _block_lines(rest)
            out.append(
                dict(rest, lines=lines[:k], bbox=_lines_bbox(lines[:k]))
            )
            rest = dict(rest, lines=lines[k:], bbox=_lines_bbox(lines[k:]))
        out.append(rest)
    return out


def _tall_blocks(blocks):
    """Tall-narrow text blocks — the rotated-table seed shape (>4x
    height/width and >60pt tall), shared so the region, band, and clip
    paths can't drift on the literal thresholds."""
    return [
        b
        for b in blocks
        if b["type"] == 0
        and (b["bbox"][3] - b["bbox"][1]) > 4 * max(1, b["bbox"][2] - b["bbox"][0])
        and (b["bbox"][3] - b["bbox"][1]) > 60
    ]


def _rotated_table_bands(tall):
    """Y-ranges where ≥3 ``tall`` blocks overlap — the bands that make
    rotated text columns stand side by side. Events at the same y are
    applied together so a same-y end/start can't split a band."""
    if len(tall) < 3:
        return []
    events = sorted(
        [(b["bbox"][1], 1) for b in tall] + [(b["bbox"][3], -1) for b in tall]
    )
    bands = []
    cov = 0
    start = None
    i = 0
    while i < len(events):
        y = events[i][0]
        while i < len(events) and events[i][0] == y:
            cov += events[i][1]
            i += 1
        if start is None:
            if cov >= 3:
                start = y
        elif cov < 3:
            bands.append((start, y))
            start = None
    return bands


def _rotated_table_seeds(blocks):
    """The tall blocks belonging to a rotated table's shared band, or [].

    A member covers at least one band point — in *any* ≥3-coverage
    range, not only the first — so a band that opens in the margins and
    gains a body-reaching member later still counts, while a tall block
    disjoint from every band never seeds."""
    tall = _tall_blocks(blocks)
    bands = _rotated_table_bands(tall)
    return [
        b for b in tall
        if any(b["bbox"][1] < e and b["bbox"][3] > s for s, e in bands)
    ]


def _rotated_table_region(blocks):
    """Bbox of a rotated (landscape) table: ≥3 tall-narrow text blocks sharing
    a common y-point, i.e. rotated text columns standing side by side."""
    tall = _tall_blocks(blocks)
    if not _rotated_table_bands(tall):
        return None
    return fitz.Rect(
        min(b["bbox"][0] for b in tall),
        min(b["bbox"][1] for b in tall),
        max(b["bbox"][2] for b in tall),
        max(b["bbox"][3] for b in tall),
    )


def _strip_clip(page, blocks, boilerplate, lang: LangProfile = EN):
    """Clip exempting strip-shaped blocks claimed by a rotated table.

    The clip models what ``render`` will rasterize, so its seeds are the
    blocks that can still reach the body flow: anything the signature or
    pattern furniture arm removes is gone before render's region
    detection and cannot seed a clip that exempts strips for it. The
    region counts only when a band member reaches the body band — a
    band contained wholly in the outer margins is a run of strips, not
    a table, and must not exempt its own members."""
    text_blocks = []
    for block in blocks:
        if block["type"] != 0:
            continue
        text = _block_text(block, lang)
        if (
            text
            and not _is_signature_furniture(
                block, page.rect.height, boilerplate
            )
            and not _is_pattern_furniture(block, page, text, lang)
        ):
            text_blocks.append(block)
    margin = page.mediabox.width
    if not any(
        s["bbox"][0] <= margin * 0.9 and s["bbox"][2] >= margin * 0.1
        for s in _rotated_table_seeds(text_blocks)
    ):
        return None
    return _rotated_table_region(text_blocks)


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
    ``_line_html`` for bold/italic/sup markers. ``cap_ids``/``cap_above``
    record which claimed blocks are caption material and whether the
    legend sits above the run — regions contending for one caption
    block resolve by ownership, not by dropping the weaker grid.
    """

    rect: fitz.Rect
    claimed: set = field(default_factory=set)
    cols: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    header: bool = False
    caption: list | None = None
    cap_ids: set = field(default_factory=set)
    cap_above: bool = False


def _upright_lines(blocks):
    """All ``(line, block_id)`` pairs of text blocks, minus rotated text.

    PyMuPDF reports each line's writing direction: ``dir`` is x-dominant
    for upright text and y-dominant for rotated text (which the
    rotated-table path owns). An aspect-ratio proxy is lossy — a
    legitimate narrow cell (a lone '7' is taller than wide) drops out of
    every band and unclaims its whole block under the straddler rule.
    """
    out = []
    for b in blocks:
        if b["type"] != 0:
            continue
        for l in _block_lines(b):
            dx, dy = l.get("dir", (1.0, 0.0))
            if abs(dx) >= abs(dy):
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


def _union_cols(ivs, min_gutter):
    """Column bounds implied by an interval union.

    Each column spans from the end of one permanent gutter (or the
    union's left edge) to the start of the next (or the right edge).
    Used for the provisional layout inside ``_grid_runs`` and for the
    final one in ``_run_layout`` — single-cell bands never feed the
    union, so a stub or colspan row can never reshape the columns.
    """
    lo = min(u[0] for u in ivs)
    hi = max(u[1] for u in ivs)
    cols = []
    prev = lo
    for g0, g1 in _interior_gaps(ivs, lo, hi, min_gutter):
        cols.append((prev, g0))
        prev = g1
    cols.append((prev, hi))
    return cols


def _grid_runs(bands, min_gutter, max_gap, lang: LangProfile = EN):
    """Group consecutive bands into runs whose union keeps ≥1 gutter.

    A band joins the open run only if its cells preserve a permanent
    interior whitespace strip — a full-width paragraph line kills every
    gutter and so closes the run (body text can't infiltrate a table).
    Single-cell bands are suspect: a narrow one commits only as a
    wrapped-cell continuation of the open row (occupied columns a strict
    subset with column 0 empty — the fold ``_assemble_rows`` applies), so
    a trailing ``Note.``/``n.s.`` line or a prose fragment is never
    absorbed as a fake sparse row; a wide one is held pending as a
    colspan candidate — absorbed if a multi-cell band resumes the grid,
    dropped as ordinary text if not. One pending band is a divider
    candidate; a second consecutive one means paragraph prose is filling
    the gap — the run closes instead of committing a run of colspan
    rows.
    """
    runs = []
    cur = None  # [bands, union, pending, open_row_cols]
    for band in bands:
        lines, y0, _y1 = band
        # a 'Table N …' legend belongs to the caption machinery, never
        # to a row: if it seeds or joins a run it becomes invisible to
        # _table_caption (which only inspects bands outside the run's
        # y-range) while the legend — and whatever shares its band —
        # is mangled into <td> cells; a legend starting in a later
        # column would even pass the wrapped-cell fold. A caption band
        # is a hard boundary: it closes the open run and is skipped.
        # Check every line — band order isn't x-sorted, so a legend
        # sharing its band with stray text may not open the band
        if any(
            lang.table_caption.match(
                "".join(s["text"] for s in l["spans"])
            )
            for l, _ in lines
        ):
            if cur is not None:
                runs.append(cur[0])
                cur = None
            continue
        cells = _band_cells(lines, min_gutter)
        if cur is not None:
            # gap measured from the last *kept* band — pending wide bands
            # count too, or a chain of them drifts the window past
            # max_gap and closes the run early (a spanning row followed
            # by more rows would sever the table in two). Both the last
            # committed and the pending band count — a stub committed
            # after the pending sits lower, and measuring from the
            # pending's bottom overstates the gap by up to a row
            last_y = cur[0][-1][2]
            if cur[2]:
                last_y = max(last_y, cur[2][-1][2])
            if y0 - last_y > max_gap:
                runs.append(cur[0])
                cur = None
        if len(cells) >= 2:
            ivs = [(c[0], c[1]) for c in cells]
            if cur is None:
                cur = [
                    [band], ivs, [],
                    _band_occupied(band, _union_cols(ivs, min_gutter),
                                   min_gutter),
                ]
                continue
            union = sorted(cur[1] + ivs)
            lo = min(u[0] for u in union)
            hi = max(u[1] for u in union)
            if _interior_gaps(union, lo, hi, min_gutter):
                cur[0].extend(cur[2])
                cur[2] = []
                cur[0].append(band)
                cur[1] = union
                cur[3] = _band_occupied(
                    band, _union_cols(union, min_gutter), min_gutter
                )
            else:
                runs.append(cur[0])
                cur = [
                    [band], ivs, [],
                    _band_occupied(band, _union_cols(ivs, min_gutter),
                                   min_gutter),
                ]
        elif cur is not None and cells:
            cx0, cx1 = cells[0][0], cells[0][1]
            lo = min(u[0] for u in cur[1])
            hi = max(u[1] for u in cur[1])
            if cx1 - cx0 < 0.7 * (hi - lo):
                # narrow lone cell — joins only as a wrapped-cell
                # continuation of the open row; anything else is prose
                # adjacent to the table, so it is not even held pending:
                # left out of the run it stays in the body flow instead
                # of surfacing as a fake sparse row, and it never feeds
                # _run_layout where a gutter-straddling interval could
                # kill the whole region
                # pendings stay pending — only a resuming multi-cell
                # band commits a colspan candidate, so a stub can't
                # flush intervening prose into the run as a merged row
                cols = _union_cols(cur[1], min_gutter)
                occ = _band_occupied(band, cols, min_gutter)
                if (
                    occ
                    and 0 not in occ
                    and occ < cur[3]
                    and len(occ) <= max(1, len(cols) - 2)
                ):
                    cur[0].append(band)
                    cur[3] |= occ
            else:
                # a wide band is held pending only while it can still
                # emit as a colspan row: a second consecutive one means
                # prose is filling the gap — close the run — and one
                # covering no recovered column can never emit, so it is
                # left to the body flow instead of claiming its block
                # for a <td> that never materializes
                if cur[2]:
                    runs.append(cur[0])
                    cur = None
                elif _band_occupied(
                    band, _union_cols(cur[1], min_gutter), min_gutter
                ):
                    cur[2].append(band)
    if cur is not None:
        runs.append(cur[0])
    # bands can land out of y order — a pending divider flushed by a
    # resuming band sits after stubs committed while it was held; sort
    # so emitted rows follow reading order
    return [sorted(run, key=lambda b: b[1]) for run in runs]


def _run_layout(bands, min_gutter):
    """Recover column bounds from a run's multi-cell bands.

    Column i spans from the end of the previous permanent gutter (or the
    region's left edge) to the start of the next. Only multi-cell bands
    feed the interval union — a lone-cell band (wrapped-cell stub or
    colspan row) may legitimately sit across a gutter and must not
    reshape the columns; a stub covering the sole surviving gutter used
    to empty the union and reject the whole region. The region rect still
    covers every committed band. Returns ``(region_rect, cols)`` or None
    when no gutter survives.
    """
    ivs = []
    x0s, y0s, x1s, y1s = [], [], [], []
    for lines, y0, y1 in bands:
        cells = _band_cells(lines, min_gutter)
        x0s.append(min(c[0] for c in cells))
        x1s.append(max(c[1] for c in cells))
        y0s.append(y0)
        y1s.append(y1)
        if len(cells) >= 2:
            ivs += [(c[0], c[1]) for c in cells]
    cols = _union_cols(ivs, min_gutter)
    if len(cols) < 2:
        return None
    return (
        fitz.Rect(min(x0s), min(y0s), max(x1s), max(y1s)),
        cols,
    )


def _cells_cols(band, cols, min_gutter):
    """Assign a band's cells to column spans.

    A cell covers a column when it overlaps more than a third of the
    column's width; multi-column coverage becomes a colspan. Cells
    whose covered ranges overlap merge into one assign — a wide
    internal gap splits a logical cell into fragments that must not
    emit as extra ``<td>``s. Returns a list of ``(col_start, span,
    cell)`` sorted by column.
    """
    out = []
    for c in _band_cells(band[0], min_gutter):
        covered = [
            i
            for i, (a, b) in enumerate(cols)
            if min(c[1], b) - max(c[0], a) > 0.35 * (b - a)
            or a - 1 <= c[0] <= b
        ]
        if not covered:
            continue
        col, span = covered[0], covered[-1] - covered[0] + 1
        for i, (pcol, pspan, pc) in enumerate(out):
            if pcol <= col < pcol + pspan or col <= pcol < col + span:
                lo, hi = min(pcol, col), max(pcol + pspan, col + span)
                pc[0] = min(pc[0], c[0])
                pc[1] = max(pc[1], c[1])
                pc[2] += c[2]
                out[i] = (lo, hi - lo, pc)
                break
        else:
            out.append((col, span, c))
    return out


def _band_occupied(band, cols, min_gutter):
    """Column indices a band's cells cover under ``cols`` — the
    'occupied' set ``_assemble_rows`` tests continuation folds against."""
    return {
        c
        for col, span, _cell in _cells_cols(band, cols, min_gutter)
        for c in range(col, col + span)
    }


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


def _table_caption(sbands, run, runs, line_h, lang: LangProfile = EN):
    """The band directly above or below the run if it reads as
    ``Table N ...`` — used as the ``<caption>`` and dropped from text.
    Bands iterate top-down, so an above-run match wins over a below one;
    and a below-run band that is itself within the caption window of a
    following run belongs to that table (legends conventionally sit
    above the grid they label — stacked grids share them downward).
    """
    ry0 = min(b[1] for b in run)
    ry1 = max(b[2] for b in run)
    starts = [min(b[1] for b in r) for r in runs if r is not run]
    for band in sbands:
        _l, y0, y1 = band
        text = " ".join(
            "".join(s["text"] for s in l["spans"]) for l, _ in band[0]
        )
        if not lang.table_caption.match(text):
            continue
        if 0 <= ry0 - y1 <= 2.5 * line_h:
            return band
        if 0 <= y0 - ry1 <= 2.5 * line_h and not any(
            0 <= o0 - y1 <= 2.5 * line_h for o0 in starts
        ):
            return band
    return None


def _region_from_run(run, runs, blocks, sbands, edges, min_gutter,
                     line_h, multi_col, lang: LangProfile = EN):
    """Validate one grid run into a ``_TableRegion`` or reject it.

    Guards (precision-first — a miss degrades to ordinary text flow, a
    false positive mangles it): needs ≥3 recovered rows (≥2 when a
    ``Table N`` caption anchors the region), ≥60% grid-dense rows,
    median band fill under 0.88 (prose fills its band), a first
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
    caption_band = _table_caption(sbands, run, runs, line_h, lang)
    if spans_edge and caption_band is None:
        return None

    claim_rect = fitz.Rect(
        rect.x0 - 4, rect.y0 - 0.4 * line_h,
        rect.x1 + 4, rect.y1 + 0.4 * line_h,
    )
    member_bands = list(run)
    caption_bands = []
    if caption_band is not None:
        # a wrapped 'Table N …' legend puts its continuation in its own
        # band(s) — only bands made purely of the block whose line
        # actually opens with the label may join: a band shared with a
        # foreign block disqualifies the legend (straddler rule), which
        # also keeps the foreign block's own bands out of the caption
        cbid = next(
            (
                bid
                for l, bid in caption_band[0]
                if lang.table_caption.match(
                    "".join(s["text"] for s in l["spans"])
                )
            ),
            caption_band[0][0][1],
        )
        run_ids = {id(band) for band in run}
        caption_bands = [caption_band] + [
            band
            for band in sbands
            if band is not caption_band
            and id(band) not in run_ids
            and all(bid == cbid for _l, bid in band[0])
        ]
        for cb in caption_bands:
            claim_rect.y0 = min(claim_rect.y0, cb[1] - 0.3 * line_h)
            claim_rect.y1 = max(claim_rect.y1, cb[2] + 0.3 * line_h)
    if any(
        b["type"] == 1 and fitz.Rect(b["bbox"]).intersects(rect)
        for b in blocks
    ):
        return None
    # a block joins the table only if it sits inside the claim rect AND
    # every one of its lines lives in a member band — a block whose
    # lines spill into a non-member band is a straddler: it stays in the
    # body flow whole, so no line can be claimed away and then never
    # emitted (the emit loop swallows claimed blocks verbatim)
    member_lines = {
        id(l) for band in member_bands for l, _bid in band[0]
    }
    claimed = set()
    for b in blocks:
        if b["type"] != 0 or fitz.Rect(b["bbox"]) not in claim_rect:
            continue
        lids = {id(l) for l in _block_lines(b)}
        if lids and lids <= member_lines:
            claimed.add(id(b))
    caption = None
    if caption_band is not None:
        # caption bands count toward claiming only when the legend
        # itself validates — every block in its band claimable. If it
        # fails, they stay non-member and the legend remains in the
        # body flow instead of being swallowed un-emitted
        cap_lines = {
            id(l) for band in caption_bands for l, _bid in band[0]
        }
        all_lines = member_lines | cap_lines
        cand = set(claimed)
        for b in blocks:
            if b["type"] != 0 or fitz.Rect(b["bbox"]) not in claim_rect:
                continue
            lids = {id(l) for l in _block_lines(b)}
            if lids and lids <= all_lines:
                cand.add(id(b))
        # the legend must be a single claimable block — a caption band
        # mixing in foreign lines is ambiguous attribution, so it
        # degrades to prose rather than emit a polluted caption
        if {bid for _l, bid in caption_band[0]} == {cbid} and cbid in cand:
            claimed = cand
            caption = sorted(
                (
                    l
                    for band in caption_bands
                    for l, bid in band[0]
                    if bid in claimed
                ),
                key=lambda l: (l["bbox"][1], l["bbox"][0]),
            )
    # an edge-spanning grid needs the *validated* caption, not just a
    # matching band — a straddler legend is no proof this isn't layout
    if spans_edge and caption is None:
        return None
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
        for col, span, c in _cells_cols(band, cols, min_gutter):
            cw = sum(
                cols[i][1] - cols[i][0] for i in range(col, col + span)
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
        cap_ids={
            bid for band in caption_bands for _l, bid in band[0]
        }
        & claimed,
        cap_above=bool(
            caption_band is not None
            and caption_band[2] <= min(b[1] for b in run)
        ),
    )


def _line_is_boldish(line):
    """Every non-empty span bold — header-row signal for tables."""
    spans = [s for s in line["spans"] if s["text"].strip()]
    return bool(spans) and all(_span_is_bold(s) for s in spans)


def _table_regions(blocks, line_h, edges, page_width,
                   lang: LangProfile = EN):
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
        runs = _grid_runs(sbands, min_gutter, max_gap, lang)
        for run in runs:
            region = _region_from_run(
                run, runs, blocks, sbands, edges, min_gutter, line_h,
                len(edges) > 1, lang,
            )
            if region is not None:
                regions.append(region)
    # the same table can surface in both a column-local and the
    # full-width scan — on overlapping claims keep the richer region;
    # a column-local subset of a page-wide grid yields the same row
    # count, so break ties on recovered columns (scan order alone would
    # keep the poorer partial region, which is appended first). When
    # regions contend only for a shared caption block, the legend stays
    # with the run it sits above — the other keeps its grid, caption
    # stripped, rather than the whole region being discarded
    regions.sort(key=lambda r: (-len(r.rows), -len(r.cols)))
    kept = []
    for r in regions:
        drop = False
        for k in kept:
            shared = r.claimed & k.claimed
            if not shared:
                continue
            if shared <= r.cap_ids | k.cap_ids:
                if k.cap_above or not r.cap_above:
                    r.claimed -= shared
                    r.caption = None
                else:
                    k.claimed -= shared
                    k.caption = None
            else:
                drop = True
                break
        if not drop:
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


def _footnote_tops(
    blocks, edges, body_size, page_height, line_h, lang: LangProfile = EN
):
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
                and lang.note_start.match(_block_text(b, lang))
            ):
                tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
    return tops
