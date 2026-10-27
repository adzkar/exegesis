"""Per-page rendering: ordered blocks become headings, bylines,
paragraphs, footnotes, figures, and list/quote fragments — collected into
``PipelineState.page_frags`` plus heading/footnote/image side channels."""
import hashlib
import html
import re

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
)
from .const import MAX_HEADING_CHARS, MIN_HEADING_CHARS, MIN_IMAGE_BYTES
from .order import _ordered_blocks
from .state import PipelineState
from .text import (
    LIST_ITEM_RE,
    SECTION_LABEL_RE,
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

CAPTION_RE = re.compile(
    r"(?i)^\s*(fig\.?|figure|table|plate|scheme|chart)\s*\.?\s*\d"
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


def _figure_alt(
    ordered,
    b_i,
    edges,
    page_width,
    region=None,
    fallback="",
    skip_ids=frozenset(),
):
    """Alt text for a figure: the first foreign text block after the image
    is its caption when it opens with a caption pattern ('Fig. 1',
    'Table 2'…), starts within 120pt below the figure's bottom, and
    shares a column with it.

    Stopping at the first non-caption text block keeps an uncaptioned
    figure from inheriting a later figure's caption, and the shared-column
    requirement keeps a two-column page's image from stealing the adjacent
    column's 'Table 3.' caption. ``region`` is the figure's true extent —
    the rotated table's region, since an arbitrary member cell would skew
    both the gap origin and the column band; it defaults to the block at
    ``b_i``. ``skip_ids`` lets the rotated-table call step over its own
    cells — the loop still ends at the first foreign text block, so
    caption reach does not depend on cell count. Column sharing uses
    ``_overlap_cols`` rather than ``_covered_cols`` — a block bleeding a
    point across the gutter must not claim both bands.
    """
    bbox = region if region is not None else ordered[b_i]["bbox"]
    img_cols = _overlap_cols(bbox, edges, page_width)

    def _is_caption(b):
        """The block's escaped text if it is a caption in the figure's
        column band, else None."""
        t = _block_text(b)
        if not CAPTION_RE.match(t):
            return None
        if not img_cols & _overlap_cols(b["bbox"], edges, page_width):
            return None
        return html.escape(t[:200], quote=True)

    def _scan(seq, gap_of):
        """Caption text if the first foreign text block in ``seq`` is one —
        the scan ends at that block either way. ``gap_of`` measures its
        distance from the figure edge facing the scan direction."""
        for b in seq:
            if b["type"] != 0:
                break
            if id(b) in skip_ids:
                continue
            if gap_of(b) > 120:
                break
            return _is_caption(b)
        return None

    if region is not None:
        # journals set 'Table N.' legends above the table body — and text
        # below a table is likelier the *next* table's legend — so the
        # preceding block is tried before the following one
        alt = _scan(
            reversed(ordered[:b_i]), lambda b: bbox[1] - b["bbox"][3]
        )
        if alt is not None:
            return alt
    return _scan(
        iter(ordered[b_i + 1 :]), lambda b: b["bbox"][1] - bbox[3]
    ) or fallback


def _footnote_text(block, body_size):
    """Render a footnote block's text; returns (marker_number, html)."""
    pieces = [_line_html(l, body_size) for l in _block_lines(block)]
    return _sup_marker(block, body_size), _join_lines(
        [p for p in pieces if p.strip()]
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
    ``state.footnotes``.
    """
    body_size = state.body_size
    edges = state.edges
    ordered = _ordered_blocks(
        page,
        state.page_items[page_index],
        state.boilerplate,
        edges,
        state.drop_edges,
    )
    foot_tops = _footnote_tops(
        ordered, edges, body_size, page.rect.height, state.line_h
    )
    frags = state.page_frags[page_index]

    tbl = _rotated_table_region(ordered)
    tbl_ids = (
        {id(b) for b in ordered if fitz.Rect(b["bbox"]).intersects(tbl)}
        if tbl is not None
        else set()
    )
    tbl_done = False

    def flush_par():
        if state.par:
            state.page_frags[state.par_page].append(
                f"<p>{_join_lines(state.par)}</p>"
            )
            state.par = []
        flush_item()

    def flush_item():
        if state.item:
            frags.append(
                f'<p class="listitem">{_join_lines(state.item)}</p>'
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
                digest = hashlib.sha1(data).hexdigest()
                if digest not in state.images:
                    ext = block.get("ext", "png")
                    name = f"images/img_{len(state.images)}.{ext}"
                    state.images[digest] = epub.EpubItem(
                        file_name=name,
                        media_type=f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}",
                        content=data,
                    )
                alt = _figure_alt(ordered, b_i, edges, page.rect.width)
                frags.append(
                    f'<p class="fig"><img src="{state.images[digest].file_name}"'
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
                    fallback="table",
                    skip_ids=tbl_ids,
                )
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
                if digest not in state.images:
                    state.images[digest] = epub.EpubItem(
                        file_name=f"images/table_{digest}.png",
                        media_type="image/png",
                        content=data,
                    )
                frags.append(
                    f'<p class="fig"><img src="{state.images[digest].file_name}"'
                    f' alt="{alt}"/></p>'
                )
            continue

        col = _assign_column(block, edges)
        if col in foot_tops and block["bbox"][1] >= foot_tops[col] - 5:
            state.footnotes.append(_footnote_text(block, body_size))
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
        if is_byline or (state.after_h1 and _authorish(btext)) or (
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
                state.par = [html.escape(tail)]
                state.par_page = page_index
            text = _join_lines(
                _line_html(l, body_size).strip() for l in blines
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
            emit_heading(2, "Abstract")
            state.abstract_done = True

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
                and block["bbox"][1] - prev["bbox"][3] > state.line_h * 0.7
                and nxt is not None
                and nxt["type"] == 0
                and _assign_column(nxt, edges) == col
                and nxt["bbox"][1] - block["bbox"][3] > state.line_h * 0.7
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
        if not new_par and state.par:
            tail = re.sub(r"<[^>]+>", "", state.par[-1]).rstrip()
            new_par = bool(re.search(r"[.!?…][\"”’)\]]*$", tail))
        if new_par and not (
            len(state.par) == 1 and len(state.par[0]) == 1
        ):
            flush_par()
        for li, line in enumerate(_block_lines(block)):
            if li == 0:
                m = _section_label(_line_text(line).strip())
                if m:
                    emit_heading(2, m[0])
                    if m[1]:
                        state.par.append(html.escape(m[1]))
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
                state.item = [t]
                continue
            if state.item:
                state.item.append(t)
                continue
            if not state.par:
                state.par_page = page_index
            state.par.append(t)
        flush_item()
