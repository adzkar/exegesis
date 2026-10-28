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
    _rotate_clip,
    _rotated_table_region,
    _sup_marker,
)
from .const import MAX_HEADING_CHARS, MIN_HEADING_CHARS, MIN_IMAGE_BYTES
from .order import _ordered_blocks
from .state import PipelineState
from .text import (
    FN_LINK_RE,
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
    # kept furniture is render-only: it never feeds footnote-zone detection
    # or rotated-table candidate sets
    body_blocks = [b for b in ordered if not b.get("is_furniture")]
    foot_tops = _footnote_tops(
        body_blocks, edges, body_size, page.rect.height, state.line_h
    )
    frags = state.page_frags[page_index]

    tbl = _rotated_table_region(body_blocks)
    tbl_ids = (
        {id(b) for b in body_blocks if fitz.Rect(b["bbox"]).intersects(tbl)}
        if tbl is not None
        else set()
    )
    # ids of blocks that leave (or never enter) the body flow — rasterized
    # table cells, footnote-region blocks, kept furniture — none of them can
    # count as a bold-only h2's follower
    nonbody_ids = tbl_ids | {
        id(b)
        for b in ordered
        if b.get("is_furniture")
        or (
            b["type"] == 0
            and (fc := _assign_column(b, edges)) in foot_tops
            and b["bbox"][1] >= foot_tops[fc] - 5
        )
    }
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
                frags.append(
                    f'<p class="fig"><img src="{state.images[digest].file_name}" alt=""/></p>'
                )
            continue

        if id(block) in tbl_ids:
            if not tbl_done:
                # tbl_ids is empty when the region is None, so tbl exists here
                assert tbl is not None
                tbl_done = True
                flush_par()
                mb = page.mediabox
                tbl = fitz.Rect(
                    max(0, tbl.x0 - 8), max(0, tbl.y0 - 20),
                    min(mb.width, tbl.x1 + 8),
                    min(mb.height, tbl.y1 + 20),
                )
                clip = _rotate_clip(tbl, page.rotation_matrix)
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
                    f'<p class="fig"><img src="{state.images[digest].file_name}" alt="table"/></p>'
                )
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
            frags.append(f"<p>{html.escape(_block_text(block))}</p>")
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
        demoted = False
        # bold-only candidates carry no size signal: they must sit
        # outside the edge zones AND introduce following *body* text in
        # their column — a trailing bold line at a section/page end is
        # far more often a note than a heading (a block that is itself a
        # heading doesn't count either). The scan is column- and
        # page-local: a bold line ending a column or page can't see its
        # next-column/next-page follower and stays demoted.
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
                    or col not in _covered_cols(b, edges, page.rect.width)
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
            # labels respect the bold-only demotion — "Funding NIH grant…"
            # tails are plausible notes, not section heads; a page-final
            # "References" stays demoted when its body isn't visible
            if li == 0 and not demoted:
                m = _section_label(_line_text(line).strip())
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
