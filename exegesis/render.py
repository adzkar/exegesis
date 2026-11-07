"""Per-page rendering: ordered blocks become headings, bylines,
paragraphs, footnotes, figures, and list/quote fragments — collected into
``PipelineState.page_frags`` plus heading/footnote/image side channels."""
from __future__ import annotations

import hashlib
import html
import re
from collections.abc import Container

import fitz
from ebooklib import epub

from .analyze import (
    _assign_column,
    _covered_cols,
    _footnote_tops,
    _overlap_cols,
    _rotate_clip,
    _rotated_table_region,
    _sup_marker,
    _table_regions,
)
from .const import MAX_HEADING_CHARS, MIN_HEADING_CHARS, MIN_IMAGE_BYTES
from .lang import EN, LangProfile
from .order import _ordered_blocks
from .state import PipelineState
from .text import (
    FN_LINK_RE,
    LIST_ITEM_RE,
    _block_lines,
    _block_median_size,
    _block_text,
    _first_line_indent,
    _join_lines,
    _line_is_bold,
    _line_max_size,
    _line_text,
    _span_is_bold,
    _span_is_italic,
)


def _section_label(text, lang: LangProfile = EN):
    """Leading section label if a text starts with a known heading phrase.

    Labels like 'References and Notes' often share a block/line with the
    first entry, so font-size heading detection can't see them. The rest of
    the text is returned separately to continue as a paragraph. The label
    set comes from the document's language profile.
    """
    m = lang.section_label.match(text)
    if not m:
        return None
    rest = text[m.end():].strip()
    if rest and not (rest[0].isdigit() or rest[0].isupper()):
        return None
    # group 1 is the documented label slot, but a custom profile whose
    # pattern captures nothing — or whose first group sat out this
    # alternative — still yields the whole match
    label = m.group(1) if m.re.groups and m.group(1) else m.group(0)
    return label, rest


def _authorish(text, joiner=" and "):
    """True if a short block plausibly lists author names (not a sentence).
    ``joiner`` is the language's 'and' inside a name list."""
    t = text.strip()
    return (
        3 < len(t) < 170
        and (", " in t or joiner in t)
        and not t.endswith((".", ":", ";", "?", "!"))
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
        if all(
            _line_max_size(l) >= body_size * 1.6 for l in lines
        ) and _looks_like_heading(text):
            return 1
        if all(_line_is_bold(l) for l in lines) and _looks_like_heading(text):
            return 2
        return None
    size = _line_max_size(lines[0])
    if size >= body_size * 1.6:
        level = 1
    elif size >= body_size * 1.08 or (
        size >= body_size * 0.9 and _line_is_bold(lines[0])
    ):
        level = 2
    else:
        return None
    text = _line_text(lines[0]).strip()
    return level if _looks_like_heading(text) else None


def _line_html(line, body_size, link_fns=True):
    """Render a line's spans to HTML: sup markers link to notes, font-name
    bold/italic suffixes become <b>/<i>. ``link_fns`` is false inside
    asides — aside text is never a note source and never links into
    notes.xhtml, so a digit sup stays a plain ``<sup>``."""
    parts = []
    for s in line["spans"]:
        text = html.escape(s["text"])
        if s["size"] < body_size * 0.75 and s["text"].strip():
            inner = html.escape(s["text"].strip())
            if (
                link_fns
                and s["text"].strip().isdigit()
                and len(inner) <= 4
            ):
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


def _figure_alt(
    ordered,
    b_i,
    edges,
    page_width,
    region=None,
    fallback="",
    skip_ids: Container[int] = frozenset(),
    pattern=EN.caption,
    above_pattern=EN.caption,
    claimed: set | None = None,
    lang: LangProfile = EN,
    col_map=None,
):
    """Alt text for a figure: the nearest foreign text block beyond the
    figure's facing edge is its caption when it opens with a caption
    pattern ('Fig. 1', 'Table 2'…), starts within 120pt of that edge, and
    shares a column with it.

    Candidates must lie clearly past the edge facing the scan — blocks
    beside or wholly beyond the figure are skipped, since column-major
    order can interleave a later column's content between the figure and
    its true caption. The first candidate still ends the scan, so an
    uncaptioned figure cannot inherit a later figure's caption, and the
    shared-column requirement (``_overlap_cols``, not the tolerance-free
    ``_covered_cols``) keeps a two-column page's image from stealing the
    adjacent column's 'Table 3.' caption. ``region`` is the figure's true
    extent — the rotated table's region, since an arbitrary member cell
    would skew both the gap origin and the column band; it defaults to
    the block at ``b_i``. ``skip_ids`` steps over blocks that can't
    donate a caption — callers pass the page's non-body set (rasterized
    table cells, footnote-zone blocks, kept furniture), so a 'Figure N.'
    bound for notes.xhtml or a caption-shaped running head can't alt an
    image while still emitting in the flow. The loop still ends at the
    first candidate, so caption reach does not depend on cell count.
    ``claimed`` is a
    page-shared set of already-adopted caption blocks — one caption
    cannot alt two images (a figure's 'Figure N.' directly above a
    rotated table would otherwise caption the table too).
    """
    if col_map is None:
        col_map = list(range(len(edges)))
    bbox = region if region is not None else ordered[b_i]["bbox"]
    # the column band is a *reading*-column band: an outdented caption
    # shares its figure's column through ``col_map``, while a genuinely
    # adjacent column still can't donate
    img_cols = {col_map[c] for c in _overlap_cols(bbox, edges, page_width)}
    claimed = claimed if claimed is not None else set()

    def _is_caption(b, pat):
        """The block's escaped text if it is a ``pat`` caption in the
        figure's column band, else None."""
        t = _block_text(b, lang)
        if not pat.match(t):
            return None
        if not img_cols & {
            col_map[c] for c in _overlap_cols(b["bbox"], edges, page_width)
        }:
            return None
        return html.escape(t[:200], quote=True)

    def _scan(seq, gap_of, past_edge, pat, stray=None):
        """(block, alt) at the first candidate in ``seq`` — the scan ends
        there whether or not the block is a caption — else None.
        ``gap_of`` measures a candidate's distance from the figure edge
        facing the scan direction; ``past_edge`` requires it to lie
        beyond that edge (modulo ~10pt of grazing), so a block inside
        the band or wholly on the far side is skipped rather than
        adopted or terminal. ``stray`` optionally re-checks such a
        skipped block — the region call uses it to rescue a legend
        seated above the region in a later column, which column-major
        order emits after the cells."""
        for b in seq:
            if b["type"] != 0:
                break
            if id(b) in skip_ids or id(b) in claimed:
                continue
            if not past_edge(b):
                if stray is not None and (alt := stray(b)) is not None:
                    return b, alt
                continue
            if gap_of(b) > 120:
                break
            return b, _is_caption(b, pat)
        return None

    stray_above = None
    if region is not None:
        # journals set 'Table N.' legends above the table body — and text
        # below a table is likelier the *next* table's legend — so the
        # preceding block is tried first, against the full label set
        # (a text-heavy rotated figure can carry a 'Figure N.' legend)
        above_gap = lambda b: bbox[1] - b["bbox"][3]
        # a later column's above-legend emits after the cells; rescue it
        # under the table's own label set — a 'Figure N.' over there is
        # that column's figure caption, not this table's legend
        stray_above = lambda b: (
            _is_caption(b, pattern) if -10 <= above_gap(b) <= 120 else None
        )
        hit = _scan(
            reversed(ordered[:b_i]),
            above_gap,
            lambda b: above_gap(b) >= -10,
            above_pattern,
        )
        if hit is not None:
            b, alt = hit
            if alt is not None:
                claimed.add(id(b))
                return alt
    hit = _scan(
        iter(ordered[b_i + 1 :]),
        lambda b: b["bbox"][1] - bbox[3],
        lambda b: b["bbox"][1] >= bbox[3] - 10,
        pattern,
        stray=stray_above,
    )
    if hit is not None and hit[1] is not None:
        claimed.add(id(hit[0]))
        return hit[1]
    return fallback


def _footnote_text(block, body_size, lang: LangProfile = EN):
    """Render a footnote block's text; returns (marker_number, html)."""
    pieces = [_line_html(l, body_size) for l in _block_lines(block)]
    return _sup_marker(block, body_size), _join_lines(
        [p for p in pieces if p.strip()], lang
    )


def _table_html(region, body_size, lang: LangProfile = EN):
    """Render a detected grid table to ``<table>`` markup.

    Recovered rows become ``<tr>``; the first row promotes to ``<thead>``
    when every cell is bold. Cells keep span-level markup (bold/italic/
    sup links) via ``_line_html``; holes between occupied columns emit
    empty cells so column positions stay aligned.
    """
    parts = ["<table>"]
    if region.caption:
        cap = _join_lines(
            (_line_html(l, body_size).strip() for l in region.caption),
            lang,
        )
        if cap:
            parts.append(f"<caption>{cap}</caption>")

    def row_html(row, tag):
        cells = []
        cur = 0
        for col, span, lns in sorted(row, key=lambda t: t[0]):
            while cur < col:
                cells.append(f"<{tag}></{tag}>")
                cur += 1
            text = _join_lines(
                (_line_html(l, body_size).strip() for l, _b in lns),
                lang,
            )
            sp = f' colspan="{span}"' if span > 1 else ""
            cells.append(f"<{tag}{sp}>{text}</{tag}>")
            cur = col + span
        while cur < len(region.cols):
            cells.append(f"<{tag}></{tag}>")
            cur += 1
        return "".join(cells)

    rows = region.rows
    if region.header:
        parts.append(f"<thead><tr>{row_html(rows[0], 'th')}</tr></thead>")
        rows = rows[1:]
    parts.append("<tbody>")
    for row in rows:
        parts.append(f"<tr>{row_html(row, 'td')}</tr>")
    parts.append("</tbody></table>")
    return "".join(parts)


def _register_image(state, data, *, ext, name):
    """Package image bytes once, keyed by content sha1; return the EpubItem.

    Body figures and rasterized tables share this — dedup by digest so
    the same pixels never package twice. ``name`` is a format template
    filled with ``n`` (image count at registration) and ``digest`` (the
    truncated content hash the raster names carry).
    """
    digest = hashlib.sha1(data).hexdigest()
    if digest not in state.images:
        state.images[digest] = epub.EpubItem(
            file_name=name.format(
                n=len(state.images), digest=digest[:16], ext=ext
            ),
            media_type=(
                f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}"
            ),
            content=data,
        )
    return state.images[digest]


def _rasterize(page, rect):
    """Rasterize a page region to PNG bytes — the rotated-table path.

    ``rect`` arrives in unrotated mediabox space (block bboxes); the
    padded clip maps into displayed space via ``page.rotation_matrix``
    because ``get_pixmap`` clips in rotated coordinates.
    """
    mb = page.mediabox
    padded = fitz.Rect(
        max(0, rect.x0 - 8), max(0, rect.y0 - 20),
        min(mb.width, rect.x1 + 8), min(mb.height, rect.y1 + 20),
    )
    clip = _rotate_clip(padded, page.rotation_matrix)
    return page.get_pixmap(clip=clip, matrix=fitz.Matrix(2, 2)).tobytes("png")


def _flush_paragraph(state):
    """Emit the open paragraph into its start page's frags, then drain
    queued asides right behind it.

    The one paragraph-flush site — render's ``flush_par`` closure and
    emit's end-of-document flush both fold into this so a document that
    ends mid-paragraph can't drop asides queued while it was open.
    Draining into ``page_frags[par_page]`` keeps an aside in the
    paragraph's chapter, ahead of any following heading.
    """
    if state.par:
        state.page_frags[state.par_page].append(
            f"<p>{_join_lines(state.par, state.lang)}</p>"
        )
        state.par = []
    if state.pending_asides:
        state.page_frags[state.par_page].extend(state.pending_asides)
        state.pending_asides.clear()


def _aside_groups(blocks):
    """Group marked blocks into aside regions, ordered by top member.

    ``block["aside"]`` carries the region key its detector assigned;
    members emit in (y, x) reading order inside their aside.
    """
    groups = {}
    for b in blocks:
        groups.setdefault(b["aside"], []).append(b)
    return sorted(
        groups.values(),
        key=lambda ms: (
            min(m["bbox"][1] for m in ms),
            min(m["bbox"][0] for m in ms),
        ),
    )


def _aside_frag(members, page, state):
    """Render one lifted region to a ``<aside class="sidebar">`` fragment.

    Everything a detector claimed is preserved: text blocks become
    aside-local paragraphs, grid tables real ``<table>`` markup, and
    images/rotated tables embedded figures — the same structuring the
    body pass applies, rerun on the region's own geometry. The local
    helpers get edges ``[aside_x0]`` and the absolute right boundary as
    their ``page_width`` (``_table_regions`` takes it with a +8 pad
    since it insets its own right band by 8pt — a tight content bbox
    must not exclude the rightmost cell). With a single edge the doc
    grid's multi-column guard can't reinterpret the strip as body
    columns. Whatever the local pass can't structure degrades to
    paragraphs — nothing is dropped. Superscripts never link into
    notes.xhtml, and no ``<hN>``/heading id can appear inside, so the
    fragment never triggers a chapter split or TOC anchor.
    """
    body_size = state.body_size
    members = sorted(
        members, key=lambda b: (b["bbox"][1], b["bbox"][0])
    )
    a_edges = [min(b["bbox"][0] for b in members)]
    a_right = max(b["bbox"][2] for b in members)
    texts = [b for b in members if b["type"] == 0]
    tbl = _rotated_table_region(texts)
    if tbl is not None:
        state.raster_tables += 1
    tbl_ids = (
        {id(b) for b in texts if fitz.Rect(b["bbox"]).intersects(tbl)}
        if tbl is not None
        else set()
    )
    grids = _table_regions(
        [b for b in texts if id(b) not in tbl_ids],
        state.line_h,
        a_edges,
        a_right + 8,
        state.lang,
    )
    state.grid_tables += len(grids)
    grid_of = {bid: g for g in grids for bid in g.claimed}
    skip_ids = tbl_ids | set(grid_of)
    claimed = set()
    inner, tbl_done, grid_done = [], False, set()
    for b_i, b in enumerate(members):
        if b["type"] == 1:
            data = b.get("image")
            if not data or len(data) < MIN_IMAGE_BYTES:
                continue
            item = _register_image(
                state, data,
                ext=b.get("ext", "png"), name="images/img_{n}.{ext}",
            )
            alt = _figure_alt(
                members, b_i, a_edges, a_right,
                pattern=state.lang.figure_caption,
                skip_ids=skip_ids, claimed=claimed, lang=state.lang,
            )
            inner.append(
                f'<p class="fig"><img src="{item.file_name}"'
                f' alt="{alt}"/></p>'
            )
            continue
        if id(b) in tbl_ids:
            if not tbl_done:
                tbl_done = True
                alt = _figure_alt(
                    members, b_i, a_edges, a_right,
                    region=tbl, fallback=state.lang.table_alt,
                    pattern=state.lang.table_caption,
                    above_pattern=state.lang.caption,
                    skip_ids=skip_ids, claimed=claimed, lang=state.lang,
                )
                item = _register_image(
                    state, _rasterize(page, tbl),
                    ext="png", name="images/table_{digest}.{ext}",
                )
                inner.append(
                    f'<p class="fig"><img src="{item.file_name}"'
                    f' alt="{alt}"/></p>'
                )
            continue
        if id(b) in grid_of:
            g = grid_of[id(b)]
            if id(g) not in grid_done:
                grid_done.add(id(g))
                inner.append(_table_html(g, body_size, state.lang))
            continue
        text = _join_lines(
            (
                _line_html(l, body_size, link_fns=False).strip()
                for l in _block_lines(b)
            ),
            state.lang,
        )
        if not text:
            continue
        if b.get("aside_title"):
            inner.append(f'<p class="aside-title">{text}</p>')
        else:
            inner.append(f"<p>{text}</p>")
    return f'<aside class="sidebar">{"".join(inner)}</aside>'


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


def _render_page(page, page_index: int, state: PipelineState):
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
    ``state.footnotes``. Blocks carrying an ``aside`` mark were lifted by
    ``_ordered_blocks`` before any classifier could see them; they render
    through the shared aside path at page end — appended to the page's
    frags when no paragraph is open, else queued into
    ``state.pending_asides`` for the paragraph-close drain.
    """
    body_size = state.body_size
    edges = state.edges
    col_map = state.col_map
    ordered = _ordered_blocks(
        page,
        state.page_items[page_index],
        state.boilerplate,
        edges,
        state.drop_edges,
        lang=state.lang,
        col_map=col_map,
    )
    # aside members never reach ``ordered`` — collect them from the
    # page's own items for the shared aside path at page end
    aside_blocks = [
        b for b in state.page_items[page_index] if b.get("aside") is not None
    ]
    # kept furniture is render-only: it never feeds footnote-zone detection
    # or rotated-table candidate sets
    body_blocks = [b for b in ordered if not b.get("is_furniture")]
    # note zones stay physical even though their membership feeds the
    # mapped reading column below: a zone is page geometry (a horizontal
    # band at a column's foot), not column identity — merging tops by min
    # across a folded edge lets a note-shaped strip block widen the swept
    # region and misfile body text as notes (a strip's own top still files
    # its notes; they just can't claim the body column's blocks)
    foot_tops = _footnote_tops(
        body_blocks, edges, body_size, page.rect.height, state.line_h,
        lang=state.lang,
    )
    frags = state.page_frags[page_index]

    tbl = _rotated_table_region(body_blocks)
    if tbl is not None:
        state.raster_tables += 1
    tbl_ids = (
        {id(b) for b in body_blocks if fitz.Rect(b["bbox"]).intersects(tbl)}
        if tbl is not None
        else set()
    )
    # footnote-region blocks leave the flow like rasterized cells and
    # kept furniture do — excluded up front, a note can't seed a grid
    # and can't be claimed into <td>s (the emit loop tests grid_of
    # before foot_tops, so a swallowed note would never reach
    # notes.xhtml)
    foot_ids = {
        id(b)
        for b in body_blocks
        if b["type"] == 0
        and (fc := _assign_column(b, edges)) in foot_tops
        and b["bbox"][1] >= foot_tops[fc] - 5
    }
    # horizontal tables claim their cell blocks like the rotated path
    # does, but emit real <table> markup instead of a rasterized image
    grids = _table_regions(
        [b for b in body_blocks if id(b) not in tbl_ids | foot_ids],
        state.line_h,
        edges,
        page.rect.width,
        state.lang,
    )
    state.grid_tables += len(grids)
    grid_of = {bid: g for g in grids for bid in g.claimed}
    # ids of blocks that leave (or never enter) the body flow — rasterized
    # table cells, footnote-region blocks, kept furniture — none of them can
    # count as a bold-only h2's follower or donate alt text to a figure
    nonbody_ids = tbl_ids | set(grid_of) | foot_ids | {
        id(b) for b in ordered if b.get("is_furniture")
    }
    tbl_done = False
    grid_done = set()
    claimed_caps = set()

    def flush_par():
        _flush_paragraph(state)
        flush_item()

    def flush_item():
        if state.item:
            frags.append(
                f'<p class="listitem">{_join_lines(state.item, state.lang)}</p>'
            )
            state.item = []

    def emit_heading(level, text):
        flush_par()
        if level == 1:
            state.after_h1 = True
        state.heading_seq += 1
        anchor = f"h{state.heading_seq}"
        state.headings.append((level, text, anchor, page_index))
        frags.append(f'<h{level} id="{anchor}">{html.escape(text)}</h{level}>')

    for b_i, block in enumerate(ordered):
        if block["type"] == 1:
            data = block.get("image")
            if data and len(data) >= MIN_IMAGE_BYTES:
                flush_par()
                item = _register_image(
                    state,
                    data,
                    ext=block.get("ext", "png"),
                    name="images/img_{n}.{ext}",
                )
                alt = _figure_alt(
                    ordered,
                    b_i,
                    edges,
                    page.rect.width,
                    pattern=state.lang.figure_caption,
                    skip_ids=nonbody_ids,
                    claimed=claimed_caps,
                    lang=state.lang,
                    col_map=col_map,
                )
                frags.append(
                    f'<p class="fig"><img src="{item.file_name}"'
                    f' alt="{alt}"/></p>'
                )
            continue

        if id(block) in tbl_ids:
            if not tbl_done:
                # tbl_ids is empty when the region is None, so tbl exists here
                assert tbl is not None
                tbl_done = True
                flush_par()
                alt = _figure_alt(
                    ordered,
                    b_i,
                    edges,
                    page.rect.width,
                    region=tbl,
                    fallback=state.lang.table_alt,
                    skip_ids=nonbody_ids,
                    pattern=state.lang.table_caption,
                    above_pattern=state.lang.caption,
                    claimed=claimed_caps,
                    lang=state.lang,
                    col_map=col_map,
                )
                item = _register_image(
                    state,
                    _rasterize(page, tbl),
                    ext="png",
                    name="images/table_{digest}.{ext}",
                )
                frags.append(
                    f'<p class="fig"><img src="{item.file_name}"'
                    f' alt="{alt}"/></p>'
                )
            continue

        if id(block) in grid_of:
            g = grid_of[id(block)]
            if id(g) not in grid_done:
                grid_done.add(id(g))
                flush_par()
                frags.append(_table_html(g, body_size, state.lang))
            continue

        if block.get("is_furniture"):
            # kept furniture emits verbatim as its own paragraph — it must
            # not merge with the open paragraph or reach any classifier.
            # Only a same-page paragraph is flushed: a paragraph carried
            # from the previous page must stay open so its continuation
            # merges and lands on the start page (flushing here would
            # split it mid-sentence around every running head)
            if state.par_page == page_index:
                flush_par()
            frags.append(
                f"<p>{html.escape(_block_text(block, state.lang))}</p>"
            )
            continue

        col = _assign_column(block, edges)
        rcol = col_map[col]
        if col in foot_tops and block["bbox"][1] >= foot_tops[col] - 5:
            state.footnotes.append(
                _footnote_text(block, body_size, state.lang)
            )
            continue

        btext = _block_text(block, state.lang)
        if (
            block["bbox"][1] < page.rect.height * 0.12
            and btext.isupper()
            and len(btext) < 30
        ):
            flush_par()
            frags.append(f'<p class="label">{html.escape(btext)}</p>')
            continue

        level = _standalone_heading_level(block, body_size)
        demoted = False
        # bold-only candidates carry no size signal: they must sit
        # outside the edge zones AND introduce following *body* text in
        # their column — a trailing bold line at a section/page end is
        # far more often a note than a heading (a block that is itself a
        # heading doesn't count either). The scan is column- and
        # page-local on the *reading* column: a bold line ending a
        # column or page can't see its next-column/next-page follower
        # and stays demoted, while an outdented head shares its body
        # column's followers through ``col_map``.
        in_edge = (
            block["bbox"][1] < page.rect.height * 0.12
            or block["bbox"][3] > page.rect.height * 0.88
        )
        follower = None
        if not in_edge:
            for b in ordered[b_i + 1 :]:
                if (
                    b["type"] != 0
                    or id(b) in nonbody_ids
                    or rcol
                    not in {
                        col_map[c]
                        for c in _covered_cols(b, edges, page.rect.width)
                    }
                ):
                    continue
                # the first body-flow block in the column decides: a
                # heading ends the section, so the tail precedes no body
                if _standalone_heading_level(b, body_size) is None:
                    follower = b
                break
        bold_h2_ok = follower is not None
        if (
            level == 2
            and _line_max_size(_block_lines(block)[0]) < body_size * 1.08
            and not bold_h2_ok
        ):
            level = None
            demoted = True
        if (
            level == 2
            and page_index == 0
            and not state.headings
            and _line_max_size(_block_lines(block)[0]) >= body_size * 1.3
        ):
            level = 1
        if level:
            emit_heading(
                level,
                " ".join(_line_text(l).strip() for l in _block_lines(block)),
            )
            continue

        med = _block_median_size(block)
        is_byline = med >= body_size * 1.15
        if is_byline or (
            state.after_h1 and _authorish(btext, state.lang.name_joiner)
        ) or (
            frags
            and frags[-1].startswith('<p class="byline">')
            and len(_block_lines(block)) == 1
            and len(_line_text(block["lines"][0]).strip()) < 100
        ):
            flush_par()
            blines = [l for l in _block_lines(block) if _line_text(l).strip()]
            tail = _line_text(blines[-1]).strip() if blines else ""
            if (
                len(tail) == 1
                and tail.isupper()
                and tail not in state.lang.single_letter_words
            ):
                blines = blines[:-1]
                state.par = [html.escape(tail)]
                state.par_page = page_index
            text = _join_lines(
                (_line_html(l, body_size).strip() for l in blines),
                state.lang,
            )
            if frags and frags[-1].startswith('<p class="byline">'):
                frags[-1] = frags[-1][:-4] + " " + text + "</p>"
                state.byline_text = re.sub(r"<[^>]+>", "", frags[-1])
            else:
                frags.append(f'<p class="byline">{text}</p>')
                state.byline_text = re.sub(r"<[^>]+>", "", text)
            continue

        state.after_h1 = False
        if (
            not state.abstract_done
            and page_index <= 1
            and 0.75 * body_size < med < 0.92 * body_size
            and block["bbox"][0] > page.rect.width * 0.15
            and block["bbox"][1] < page.rect.height * 0.5
        ):
            emit_heading(2, state.lang.abstract_title)
            state.abstract_done = True

        if (
            block["bbox"][0] - edges[col] > 12
            and med <= body_size * 1.05
            and len(btext) > 60
        ):
            # isolation is measured against body blocks — kept furniture
            # sits at the page edges and must not count as a neighbor
            prev = next(
                (
                    b
                    for b in reversed(ordered[:b_i])
                    if not b.get("is_furniture")
                ),
                None,
            )
            nxt = next(
                (
                    b
                    for b in ordered[b_i + 1 :]
                    if not b.get("is_furniture")
                ),
                None,
            )
            isolated = (
                prev is not None
                and prev["type"] == 0
                and col_map[_assign_column(prev, edges)] == rcol
                and block["bbox"][1] - prev["bbox"][3] > state.line_h * 0.7
                and nxt is not None
                and nxt["type"] == 0
                and col_map[_assign_column(nxt, edges)] == rcol
                and nxt["bbox"][1] - block["bbox"][3] > state.line_h * 0.7
            )
            if isolated:
                flush_par()
                qhtml = _join_lines(
                    (
                        _line_html(l, body_size).strip()
                        for l in _block_lines(block)
                        if _line_text(l).strip()
                    ),
                    state.lang,
                )
                frags.append(f"<blockquote><p>{qhtml}</p></blockquote>")
                continue

        new_par = _first_line_indent(block) > 4
        if not new_par and state.par:
            tail = re.sub(r"<[^>]+>", "", state.par[-1]).rstrip()
            new_par = bool(re.search(r"[.!?…][\"”’)\]]*$", tail))
        if new_par and not (
            len(state.par) == 1 and len(state.par[0]) == 1
        ):
            flush_par()
        for li, line in enumerate(_block_lines(block)):
            # labels respect the bold-only demotion — "Funding NIH grant…"
            # tails are plausible notes, not section heads; a page-final
            # "References" stays demoted when its body isn't visible
            if li == 0 and not demoted:
                m = _section_label(
                    _line_text(line).strip(), state.lang
                )
                if m:
                    emit_heading(2, m[0])
                    if m[1]:
                        state.par.append(html.escape(m[1]))
                    continue
            # blocks opening with a footnote marker are note material —
            # none of their lines may classify as headings. A demoted
            # block's *size-signaled* lines may still emit; only its
            # bold-band lines are silenced by the gate below
            line_level = (
                _heading_level(line, body_size)
                if btext[:1] not in "*†‡§"
                else None
            )
            # a line-level h2 promoted by the bold-only band (size <
            # 1.08×body is unreachable via the size path) needs the same
            # edge/follower check as a block-level candidate — a bold line
            # inside a mixed-weight block is just as often a note
            if (
                line_level == 2
                and _line_max_size(line) < body_size * 1.08
                and not bold_h2_ok
            ):
                line_level = None
            if line_level:
                emit_heading(line_level, _line_text(line).strip())
                continue
            t = _line_html(line, body_size).strip()
            if not t:
                continue
            if LIST_ITEM_RE.match(_line_text(line).strip()):
                flush_par()
                state.item = [t]
                continue
            if state.item:
                state.item.append(t)
                continue
            if not state.par:
                state.par_page = page_index
            state.par.append(t)
        flush_item()

    # aside members emit last — they never merged with the flow above.
    # List items already flushed per block, so only an open paragraph
    # defers the page's asides: they queue in page order and drain into
    # par_page's frags right behind the closing paragraph; with no
    # paragraph open they append to this page's frags after body flow.
    # The known cost — an aside can drift to the end of a long
    # multi-page paragraph — is accepted: prose integrity wins.
    if aside_blocks:
        target = state.pending_asides if state.par else frags
        for members in _aside_groups(aside_blocks):
            state.asides += 1
            target.append(_aside_frag(members, page, state))
