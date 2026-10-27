"""Reading-order reconstruction: column-aware block ordering plus the
header/footer furniture drops."""
import re

from .analyze import (
    _assign_column,
    _covered_cols,
    _is_spanning,
    _signature,
)
from .const import EDGE_ZONE
from .text import FURNITURE_RE, _block_text


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
    for seq in cols.values():
        seq.sort(key=key)

    ordered = list(top_zone)
    emitted = set()
    for c in sorted(cols):
        for b in cols[c]:
            if id(b) in emitted:
                continue
            emitted.add(id(b))
            ordered.append(b)
    return ordered
