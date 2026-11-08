"""Reading-order reconstruction: column-aware block ordering plus the
header/footer furniture drops."""
from collections import Counter
from statistics import median

from .analyze import (
    _assign_column,
    _covered_cols,
    _heading_like,
    _is_furniture,
    _is_signature_furniture,
    _is_spanning,
    _strip_clip,
)
from .const import BAND_MIN_DEPTH, BAND_MIN_GAP
from .lang import EN, LangProfile
from .text import _block_lines, _block_text, _line_text, _span_is_bold


def _ordered_blocks(
    page,
    blocks,
    boilerplate,
    edges,
    drop_furniture=True,
    lang: LangProfile = EN,
    col_map=None,
    line_h=None,
    body_size=None,
):
    """Return page blocks in reading order for any column layout.

    ``blocks`` is the page's pre-extracted ``get_text("dict")["blocks"]``;
    ``page`` supplies only geometry (rect).

    Blocks above the first spanning element come first (page header zone).
    Each reading column then emits its own sequence — non-spanning blocks
    plus the spanning elements that cover it, in y order — columns left to
    right. A page-wide whitespace strip taller than ``BAND_MIN_GAP``
    line heights whose boundary carries at most one marked block
    (``_mark``: oversized head, named section label, or all-bold
    heading-shaped block) commits as a section seam and switches
    emission to band-major — each band's columns left to right, bands
    top to bottom — so a section banded across the columns stays
    sequential (#61); shallow sides, competing marks, and unmarked
    strips all keep column-major order. ``col_map`` translates physical
    edge indices into reading
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

    if col_map is None:
        col_map = list(range(len(edges)))
    cut = min((b["bbox"][1] for b in items if id(b) in spans), default=None)
    # The page header zone is for front matter that precedes the columns,
    # not for columnar body that happens to sit above a mid-page span.
    # The set above the first span is columnar — and stays in column
    # flow — when it fills at least two reading columns with multiple
    # members each; a sparse region (one column's worth, or lone blocks)
    # keeps the front-loaded slot it always had. A wide table band or
    # full-width figure must not y-interleave the columns above it (#60).
    above = [b for b in nonspans if cut is not None and b["bbox"][3] < cut]
    occupancy = Counter(col_map[_assign_column(b, edges)] for b in above)
    columnar = sum(1 for n in occupancy.values() if n >= 2) >= 2
    top_zone = [] if columnar else sorted(above, key=key)
    top_ids = {id(b) for b in top_zone}
    nonspans = (
        [b for b in nonspans if id(b) not in top_ids]
        if top_zone
        else nonspans
    )

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

    # band seams (#61): a horizontal strip no column's items intersect —
    # page-wide whitespace taller than ``BAND_MIN_GAP`` line heights —
    # splits every column at the same place, so a section banded across
    # the columns (declarations over references, front matter over an
    # abstract) keeps its bands sequential instead of splitting around
    # the other column's content. A strip alone is not enough — figures
    # and ragged column tails leave page-wide gaps in continuous flow —
    # so a seam commits only when some column's block touching it marks
    # the break: an oversized head, a named section label, or a bold
    # heading-shaped block — the h2 gates' own vocabulary. Emission is
    # then band-major (band, then column). A
    # block belongs to the band below a seam once it starts past the
    # seam's far edge; nothing can sit inside the strip itself. With no
    # committed seam the page keeps ordinary column-major order.
    seams = []
    if len(cols) > 1:
        ivs = sorted((b["bbox"][1], b["bbox"][3]) for b in items)
        merged = []
        for y0, y1 in ivs:
            if merged and y0 <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], y1)
            else:
                merged.append([y0, y1])
        if line_h is None:
            hs = [
                l["bbox"][3] - l["bbox"][1]
                for b in items
                if b["type"] == 0
                for l in _block_lines(b)
            ]
            line_h = median(hs) if hs else 12.0
        if body_size is None:
            ss = [
                s["size"]
                for b in items
                if b["type"] == 0
                for l in _block_lines(b)
                for s in l["spans"]
            ]
            body_size = median(ss) if ss else 10.0

        def _label_mark(s):
            """True when s opens with a named section label that doesn't
            run on into lowercase prose — render._section_label's rest
            check, mirrored (render imports order, not the reverse): a
            label word opening prose — 'Funding for this work…' — starts
            a sentence, not a section head."""
            m = lang.section_label.match(s)
            if not m:
                return False
            rest = s[m.end():].strip()
            return not rest or rest[0].isdigit() or rest[0].isupper()

        def _mark(b):
            """A section mark — an oversized head, a named label, or an
            all-bold heading-shaped block (the h2 gates' vocabulary, so
            figure tick labels, page numbers, and math fragments can't
            mark)."""
            if b["type"] != 0:
                return False
            t = _block_text(b, lang)
            if not t:
                return False
            lines = _block_lines(b)
            spans = [s for l in lines for s in l["spans"]]
            if max((s["size"] for s in spans), default=0) >= (
                body_size * 1.3
            ):
                return True
            # the h2 gate applies the label check to the first *line* —
            # a fused 'Data Availability'/'data are…' head still heads
            # in render — while a wrapped head like 'References and'/
            # 'Notes' only matches the joined text, so both surfaces
            # are tried
            if _label_mark(t) or (
                lines and _label_mark(_line_text(lines[0]).strip())
            ):
                return True
            return bool(spans) and all(
                _span_is_bold(s) or not s["text"].strip() for s in spans
            ) and _heading_like(b, t)

        cands = [
            i
            for i in range(len(merged) - 1)
            if merged[i + 1][0] - merged[i][1] >= BAND_MIN_GAP * line_h
        ]
        seams = []
        prev_start = 0
        for k, i in enumerate(cands):
            hi = cands[k + 1] if k + 1 < len(cands) else len(merged) - 1
            # each side of the strip must be a real band — a few lines
            # deep — not a page-top head strip or a trailing colophon
            up_depth = merged[i][1] - merged[prev_start][0]
            lo_depth = merged[hi][1] - merged[i + 1][0]
            # a band is one section across the columns: its boundary may
            # carry a mark (dedup'd — a spanning head touches several
            # column sequences) but never competing marks in different
            # columns — parallel sections read column-major
            g0, g1 = merged[i][1], merged[i + 1][0]
            marked_above = set()
            marked_below = set()
            for seq in cols.values():
                above = [b for b in seq if b["bbox"][3] <= g0 + 0.5]
                below = [b for b in seq if b["bbox"][1] >= g1 - 0.5]
                if above and _mark(above[-1]):
                    marked_above.add(id(above[-1]))
                if below and _mark(below[0]):
                    marked_below.add(id(below[0]))
            if (
                up_depth >= BAND_MIN_DEPTH * line_h
                and lo_depth >= BAND_MIN_DEPTH * line_h
                and len(marked_above) <= 1
                and len(marked_below) <= 1
                and (marked_above or marked_below)
            ):
                seams.append((g0, g1))
                # a rejected strip is whitespace inside the band, not a
                # boundary — the next candidate's upper band still spans it
                prev_start = i + 1

    ordered = list(top_zone)
    emitted = set()

    def _emit(c, band=None):
        for b in cols[c]:
            if id(b) in emitted:
                continue
            if band is not None and (
                sum(1 for g in seams if b["bbox"][1] >= g[1]) != band
            ):
                continue
            emitted.add(id(b))
            ordered.append(b)

    if seams:
        for band in range(len(seams) + 1):
            for c in sorted(cols):
                _emit(c, band)
    else:
        for c in sorted(cols):
            _emit(c)
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
