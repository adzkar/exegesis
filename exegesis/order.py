"""Reading-order reconstruction: column-aware block ordering plus the
header/footer furniture drops."""
from .analyze import (
    _assign_column,
    _covered_cols,
    _is_furniture,
    _is_signature_furniture,
    _is_spanning,
    _strip_clip,
)
from .lang import EN, LangProfile
from .text import _block_text


def _ordered_blocks(
    page,
    blocks,
    boilerplate,
    edges,
    drop_furniture=True,
    lang: LangProfile = EN,
    col_map=None,
):
    """Return page blocks in reading order for any column layout.

    ``blocks`` is the page's pre-extracted ``get_text("dict")["blocks"]``;
    ``page`` supplies only geometry (rect).

    Blocks above the first spanning element come first (page header zone).
    Each reading column then emits its own sequence — non-spanning blocks
    plus the spanning elements that cover it, in y order — columns left to
    right. ``col_map`` translates physical edge indices into reading
    columns (``_reading_columns``; identity when omitted): an outdent
    edge's heads merge into the body column's sequence and interleave by
    y instead of front-loading the page, and a span's covered columns
    collapse the same way so no span-only outdent sequence survives.
    A span covering several columns emits at its first encounter only.
    Text blocks whose signature is in ``boilerplate``, margin strips,
    pattern furniture, and page numbers are dropped when ``drop_furniture``
    is true; when false (``--keep-headers``) they are kept but flagged
    ``is_furniture``, held in a side list, and emitted at the page's
    top/bottom edge — they render verbatim without reaching any classifier
    or perturbing the spans/cut/column machinery (a gutter-crossing footer
    would otherwise collapse the page into interleaved top-zone order).
    Blocks carrying an ``aside`` mark leave through the shared aside path
    instead: checked before the furniture tests (a claimed block survives
    a furniture-matching line), they never enter ``items`` and render via
    ``_aside_frag`` at page end.
    """
    rect = page.rect
    items = []
    furniture_items = []
    # a rotated table's outer cells share the strip's tall/rotated shape
    # and can sit in the outer margin — the detected region exempts them
    # from the margin-strip arm so no cell is dropped as furniture. Only
    # blocks that can still reach the body flow seed it, and only when a
    # band member reaches the body band: a margin-strips-only band is a
    # run of furniture, not a table, and must not exempt its own members.
    # Aside members can't reach the body flow either — they lift out
    # below — so they seed no clip
    clip = _strip_clip(
        page,
        [b for b in blocks if b.get("aside") is None],
        boilerplate,
        lang,
    )
    for block in blocks:
        # the aside mark is checked before the furniture tests: a block
        # its detector positively claimed is preserved through the aside
        # path even when its text or position also matches a furniture
        # rule — and it never joins the spans/cut/column machinery (a
        # gutter-crossing aside must not become the top-zone cut)
        if block.get("aside") is not None:
            continue
        if block["type"] == 0:
            text = _block_text(block, lang)
            if not text:
                continue
            sig_furniture = _is_signature_furniture(
                block, rect.height, boilerplate
            )
            if sig_furniture or _is_furniture(
                block, page, text, lang, clip
            ):
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

    if col_map is None:
        col_map = list(range(len(edges)))
    # key the sequences by reading column, not physical edge: mapping both
    # non-span assignments and every span's covered columns through
    # ``col_map`` merges an outdent edge's members into the body column's
    # y-ordered sequence (and dedupes a span that covered two edges now
    # sharing one reading column)
    cols = {i: [] for i in set(col_map)}
    for b in nonspans:
        cols[col_map[_assign_column(b, edges)]].append(b)
    for b in items:
        if id(b) in spans:
            for c in {col_map[k] for k in _covered_cols(b, edges, rect.width)}:
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
