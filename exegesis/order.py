"""Reading-order reconstruction: column-aware block ordering plus the
header/footer furniture drops."""
from .analyze import (
    _assign_column,
    _covered_cols,
    _is_furniture,
    _is_signature_furniture,
    _is_spanning,
)
from .text import _block_text


def _ordered_blocks(page, blocks, boilerplate, edges, drop_furniture=True):
    """Return page blocks in reading order for any column layout.

    ``blocks`` is the page's pre-extracted ``get_text("dict")["blocks"]``;
    ``page`` supplies only geometry (rect).

    Blocks above the first spanning element come first (page header zone).
    Each column then emits its own sequence — non-spanning blocks plus the
    spanning elements that cover it, in y order — columns left to right.
    A span covering several columns emits at its first encounter only.
    Text blocks whose signature is in ``boilerplate``, margin strips,
    pattern furniture, and page numbers are dropped when ``drop_furniture``
    is true; when false (``--keep-headers``) they are kept but flagged
    ``is_furniture``, held in a side list, and emitted at the page's
    top/bottom edge — they render verbatim without reaching any classifier
    or perturbing the spans/cut/column machinery (a gutter-crossing footer
    would otherwise collapse the page into interleaved top-zone order).
    """
    rect = page.rect
    items = []
    furniture_items = []
    for block in blocks:
        if block["type"] == 0:
            text = _block_text(block)
            if not text:
                continue
            sig_furniture = _is_signature_furniture(
                block, rect.height, boilerplate
            )
            if sig_furniture or _is_furniture(block, rect, text):
                if drop_furniture:
                    continue
                block["is_furniture"] = True
                furniture_items.append(block)
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
    # kept furniture slots at the page edge it belongs to — running heads
    # before the content, footers/sidebars after it
    top_f = sorted(
        (b for b in furniture_items if b["bbox"][1] < rect.height * 0.5),
        key=key,
    )
    bottom_f = sorted(
        (b for b in furniture_items if b["bbox"][1] >= rect.height * 0.5),
        key=key,
    )
    return top_f + ordered + bottom_f
