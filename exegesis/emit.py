"""EPUB emission: chapter chunking at headings, notes collection, TOC
assembly, and the ``convert`` entry point."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, cast

import fitz
from ebooklib import epub

from .analyze import (
    _body_font_size,
    _boilerplate_signatures,
    _doc_column_edges,
)
from .const import BOOK_LANG, PAGES_PER_FALLBACK_CHAPTER
from .render import _render_page
from .state import PipelineState
from .text import NOTE_START_RE, _join_lines


def _valid_title(t):
    """True if a metadata title is usable (has letters, not pagination junk)."""
    return bool(t) and bool(re.search(r"[A-Za-z]", t)) and not re.match(
        r"^[\d\s.\-_()]+$", t
    )


def _extract_pages(doc) -> list[tuple[fitz.Page, dict[str, Any]]]:
    """One ``get_text("dict")`` pass over the document: (page, dict) pairs.

    ``get_text`` is typed as returning several shapes keyed by its mode
    argument, so the dict variant is pinned with ``cast``.
    """
    return [(p, cast(dict[str, Any], p.get_text("dict"))) for p in doc]


def convert(
    pdf_path: Path,
    out_path: Path,
    title: str | None = None,
    author: str | None = None,
    drop_edges: bool = True,
) -> dict[str, str | int]:
    """Convert ``pdf_path`` to an EPUB at ``out_path``; returns a stats dict."""
    doc = fitz.open(pdf_path)
    meta = doc.metadata or {}
    # single extraction pass: every analysis/render step reads these dicts
    pages = _extract_pages(doc)

    body_size, line_h = _body_font_size(pages)
    state = PipelineState(
        body_size=body_size,
        line_h=line_h,
        page_items=[pdict["blocks"] for _p, pdict in pages],
        edges=_doc_column_edges(pages),
        drop_edges=drop_edges,
        boilerplate=(
            _boilerplate_signatures(pages)
            if drop_edges and doc.page_count > 3
            else set()
        ),
        page_frags=[[] for _ in range(doc.page_count)],
    )

    for i, (page, _pdict) in enumerate(pages):
        _render_page(page, i, state)
    if state.par:
        state.page_frags[state.par_page].append(
            f"<p>{_join_lines(state.par)}</p>"
        )
    fn_nums = {n for n, _ in state.footnotes if n is not None}
    for frags in state.page_frags:
        for k, frag in enumerate(frags):
            frags[k] = re.sub(
                r'<sup><a href="notes.xhtml#fn(\d+)">(\d+)</a></sup>',
                lambda m: m.group(0)
                if int(m.group(1)) in fn_nums
                else f"<sup>{m.group(2)}</sup>",
                frag,
            )

    flat = [
        (pi, frag)
        for pi, frags in enumerate(state.page_frags)
        for frag in frags
    ]
    boundaries = [
        i for i, (_, f) in enumerate(flat) if '<h1 id="' in f or '<h2 id="' in f
    ]
    if not boundaries:
        boundaries = [
            i
            for i, (pi, _) in enumerate(flat)
            if pi % PAGES_PER_FALLBACK_CHAPTER == 0
            and (i == 0 or flat[i - 1][0] != pi)
        ]
    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    if title is None:
        title = meta.get("title") or ""
        if not _valid_title(title):
            title = next(
                (t for lv, t, _a, _p in state.headings if lv == 1),
                pdf_path.stem,
            )
    if author is None:
        author = meta.get("author") or ""
        if not _valid_title(author):
            name: str = state.byline_text or "Unknown"
            name = re.split(
                r"\s+(?:Department|University|Institute|Center|College|School of)\b",
                name,
            )[0]
            author = re.sub(r"[*†‡§]", "", name).strip(" ,")
        else:
            author = author.strip()

    book = epub.EpubBook()
    book.set_identifier(hashlib.sha1(str(pdf_path).encode()).hexdigest()[:16])
    book.set_title(title)
    book.set_language(BOOK_LANG)
    book.add_author(author)

    css = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=(
            "p.byline { font-style: italic; } "
            "p.label { font-size: 0.8em; letter-spacing: 0.1em; "
            "text-transform: uppercase; color: #666; } "
            "p.fig { text-align: center; } p.fig img { max-width: 100%; } "
            "p.listitem { margin-left: 2em; } "
            "blockquote { margin: 0.8em 1.5em; font-size: 0.95em; }"
        ),
    )
    book.add_item(css)

    chapters = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(flat)
        content = "\n".join(f for _, f in flat[start:end])
        heading = next(
            (
                re.sub(r"<[^>]+>", "", f)
                for _, f in flat[start:end]
                if f.startswith("<h")
            ),
            None,
        )
        chap = epub.EpubHtml(
            title=heading or f"Section {idx + 1}",
            file_name=f"chap_{idx + 1}.xhtml",
            lang=BOOK_LANG,
        )
        chap.add_item(css)
        chap.content = (
            f"<html><body>{content.strip() or '<p></p>'}</body></html>"
        )
        book.add_item(chap)
        chapters.append((chap, start))

    note_entries = []
    seen_fn = set()
    for num, frag in state.footnotes:
        frag = re.sub(
            r"^<sup><a[^>]*>(\d+)</a></sup>", r"<sup>\1</sup>", frag
        )
        # an unnumbered block is a column/page-split continuation of the
        # previous note unless it opens a new note itself (correspondence
        # and acknowledgment openers — the same pattern that marks the
        # note zone on the page)
        frag_text = re.sub(r"<[^>]+>", "", frag).lstrip("*†‡§ ")
        if (
            num is None
            and note_entries
            and not NOTE_START_RE.match(frag_text)
        ):
            note_entries[-1] = note_entries[-1][:-4] + " " + frag + "</p>"
        elif num is not None and num not in seen_fn:
            seen_fn.add(num)
            note_entries.append(f'<p class="fn" id="fn{num}">{frag}</p>')
        else:
            note_entries.append(f'<p class="fn">{frag}</p>')

    anchor_pos = {}
    for i, (_, f) in enumerate(flat):
        m = re.search(r'id="(h\d+)"', f)
        if m:
            anchor_pos[m.group(1)] = i
    # nested TOC: each h1 opens a section list that absorbs following h2s;
    # an h2 before the first h1 stays top-level
    toc = []
    for level, text, anchor, _page_i in state.headings:
        pos = anchor_pos.get(anchor, 0)
        chap_idx = max(j for j, (_, s) in enumerate(chapters) if s <= pos)
        link = epub.Link(
            f"{chapters[chap_idx][0].file_name}#{anchor}", text, anchor
        )
        if level == 1:
            toc.append([link, []])
        elif toc and isinstance(toc[-1], list):
            toc[-1][1].append(link)
        else:
            toc.append(link)
    if note_entries:
        notes = epub.EpubHtml(
            title="Notes", file_name="notes.xhtml", lang=BOOK_LANG
        )
        notes.content = (
            "<html><body><h1>Notes</h1>" + "".join(note_entries) + "</body></html>"
        )
        book.add_item(notes)
        chapters.append((notes, len(flat)))
        toc.append(epub.Link("notes.xhtml", "Notes", "notes"))
    # a section tuple always renders a nested <ol>, so a childless h1
    # must degrade to a bare Link or nav.xhtml gets an invalid empty <ol/>
    book.toc = [
        (entry[0], tuple(entry[1]))
        if isinstance(entry, list) and entry[1]
        else (entry[0] if isinstance(entry, list) else entry)
        for entry in toc
    ]
    for item in state.images.values():
        book.add_item(item)
    book.spine = ["nav"] + [c for c, _ in chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(out_path), book)
    return {
        "title": title,
        "pages": doc.page_count,
        "chapters": len(chapters),
        "headings": len(state.headings),
        "footnotes": len(state.footnotes),
        "images": len(state.images),
        "output": str(out_path),
    }
