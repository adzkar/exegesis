"""The shared <aside> path: mark lifting, placement, isolation, markup.

These tests pin the machinery contract with synthetic marks — they set
``block["aside"]`` region keys on extracted blocks directly, the same
marks the front-matter info-box detector assigns in ``convert()``
(#41). Nothing here depends on that detector: a region key groups
members into one ``<aside class="sidebar">`` fragment, an optional
``aside_title`` flag renders ``<p class="aside-title">``.
"""
from __future__ import annotations

import zipfile

import fitz
import pytest
from builders import _page, _save, block, image, text

from exegesis import convert
from exegesis.analyze import (
    _body_font_size,
    _boilerplate_signatures,
    _doc_column_edges,
    _reading_columns,
    _split_signature_prefixes,
)
from exegesis.render import _flush_paragraph, _render_page
from exegesis.state import PipelineState
from exegesis.text import _block_text

ASIDE_FIELDS = ("Received: 3 March 2024", "Accepted: 9 April 2024")
BODY_BLOCK = (
    "Ordinary article prose keeps its place in the body flow and",
    "establishes the single body column this page is measured by.",
)
# no ", "/" and "/terminal punctuation — a block right after an h1 that
# looked like a name list would classify as a byline instead of opening
# the paragraph these tests exercise
PAR_OPEN = (
    "A paragraph that opens on one page yet does not close keeps its",
    "words inside the same open paragraph until the argument reaches",
)


def _render_doc(pdf_path, mark=None, drop_edges=True, flush=True):
    """Drive ``_render_page`` over a built PDF with convert's own prep.

    ``mark`` receives the post-split ``pages`` and sets the synthetic
    ``block["aside"]`` region keys the machinery honors. ``flush``
    runs the shared paragraph/aside drain emit calls at document end;
    pass False to inspect the pre-drain queue. Returns the filled
    ``PipelineState`` plus the detected edges.
    """
    with fitz.open(pdf_path) as doc:
        pages = [(p, p.get_text("dict")) for p in doc]
        body_size, line_h = _body_font_size(pages)
        boilerplate = _boilerplate_signatures(pages)
        for page, pdict in pages:
            pdict["blocks"] = _split_signature_prefixes(
                pdict["blocks"], page.rect.height, boilerplate
            )
        if mark is not None:
            mark(pages)
        edges = _doc_column_edges(pages, boilerplate)
        state = PipelineState(
            body_size=body_size,
            line_h=line_h,
            page_items=[pdict["blocks"] for _p, pdict in pages],
            edges=edges,
            col_map=_reading_columns(pages, edges, boilerplate),
            drop_edges=drop_edges,
            boilerplate=boilerplate,
            page_frags=[[] for _ in pages],
        )
        for i, (page, _pdict) in enumerate(pages):
            _render_page(page, i, state)
    if flush:
        _flush_paragraph(state)
    return state, edges


def _mark(pages, page_index, *needles):
    """Mark every page-``page_index`` block whose text contains a needle."""
    marked = 0
    for b in pages[page_index][1]["blocks"]:
        if any(n in _block_text(b) for n in needles):
            b["aside"] = 0
            marked += 1
    assert marked == len(needles), (
        f"expected {len(needles)} marked blocks, got {marked}"
    )


def _aside_frags(state, page_index):
    return [f for f in state.page_frags[page_index] if f.startswith("<aside")]


def test_aside_lift_preserves_fields_and_markup(tmp_path):
    """Marked blocks leave body flow into one <aside class="sidebar">."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "An aside machinery control", size=18)
    text(page, 40, 240, ASIDE_FIELDS[0], size=9)
    text(page, 40, 264, ASIDE_FIELDS[1], size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, *ASIDE_FIELDS)
    )

    frags = state.page_frags[0]
    aside = _aside_frags(state, 0)
    assert len(aside) == 1
    assert aside[0].startswith('<aside class="sidebar">')
    assert aside[0].endswith("</aside>")
    assert "epub:type" not in aside[0]
    for field in ASIDE_FIELDS:
        assert f"<p>{field}</p>" in aside[0]
        assert not any(
            field in f for f in frags if not f.startswith("<aside")
        )
    # the aside lands after body flow — it is this page's last fragment
    assert frags[-1] == aside[0]
    assert state.asides == 1
    assert state.pending_asides == []


def test_ordered_blocks_lifts_before_furniture(tmp_path):
    """A claimed block survives a furniture match; the unclaimed drop."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Furniture precedence control", size=18)
    # a claimed block in the pattern-furniture edge zone and an
    # unclaimed page number beside it
    text(page, 40, 770, "Copyright: 2024 Example Journal", size=8)
    text(page, 300, 784, "7", size=8)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, "Copyright:")
    )
    aside = _aside_frags(state, 0)
    assert len(aside) == 1
    assert "<p>Copyright: 2024 Example Journal</p>" in aside[0]
    body = "\n".join(
        f for f in state.page_frags[0] if not f.startswith("<aside")
    )
    assert "Copyright" not in body
    assert ">7<" not in body and " 7 " not in body


def test_aside_members_cast_no_column_votes(tmp_path):
    """A recurring lifted column cannot vote a phantom document edge."""
    doc = fitz.open()
    for _ in range(2):
        page = _page(doc)
        text(page, 40, 240, ASIDE_FIELDS[0], size=9)
        text(page, 40, 264, ASIDE_FIELDS[1], size=9)
        block(page, 180, 300, BODY_BLOCK, leading=14)
        block(page, 180, 460, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    with fitz.open(pdf) as doc:
        pages = [(p, p.get_text("dict")) for p in doc]
        boilerplate = _boilerplate_signatures(pages)
        # unmarked, the x40 stack clusters into a second edge on both pages
        unmarked = _doc_column_edges(pages, boilerplate)
        assert unmarked[0] == pytest.approx(40, abs=2)
        for b in pages[0][1]["blocks"] + pages[1][1]["blocks"]:
            if "Received" in _block_text(b) or "Accepted" in _block_text(
                b
            ):
                b["aside"] = 0
        marked = _doc_column_edges(pages, boilerplate)
    assert marked == pytest.approx([180])


def test_aside_never_reaches_any_classifier(tmp_path):
    """Heading-shaped and bottom-zone marked blocks emit only as aside."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 40, 240, "RECEIVED", size=18)
    text(page, 40, 620, "Received: bottom zone field", size=8)
    text(page, 40, 640, "Accepted: bottom zone field", size=8)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf,
        lambda pages: _mark(
            pages, 0, "RECEIVED", "Received: bottom", "Accepted: bottom"
        ),
    )
    assert state.headings == []
    assert state.footnotes == []
    aside = _aside_frags(state, 0)[0]
    assert "<h" not in aside
    assert "RECEIVED" in aside and "bottom zone field" in aside


def test_aside_queues_behind_open_paragraph(tmp_path):
    """A page-0 box with a cross-page paragraph emits after its close."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Placement control", size=18)
    text(page, 40, 240, ASIDE_FIELDS[0], size=9)
    text(page, 40, 264, ASIDE_FIELDS[1], size=9)
    block(page, 180, 400, PAR_OPEN, leading=14)
    page = _page(doc)
    block(
        page, 180, 100,
        ("the closing sentence of the cross-page paragraph.",),
        leading=14,
    )
    block(page, 180, 220, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, *ASIDE_FIELDS)
    )
    p0 = state.page_frags[0]
    aside = _aside_frags(state, 0)[0]
    par = next(f for f in p0 if f.startswith("<p>"))
    # the intact paragraph emits on its start page, the aside right
    # behind it — never spliced between the paragraph's own lines
    assert p0.index(aside) == p0.index(par) + 1
    assert "the closing sentence of the cross-page paragraph." in par
    assert not any(
        "Received:" in f or "Accepted:" in f
        for f in state.page_frags[1]
    )


def test_aside_appends_same_page_when_paragraph_closed(tmp_path):
    """A page whose last paragraph closes on the page emits its aside."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Same-page placement", size=18)
    block(
        page, 180, 300,
        ("A complete paragraph that closes before the page ends.",),
        leading=14,
    )
    text(page, 40, 500, ASIDE_FIELDS[0], size=9)
    text(page, 40, 524, ASIDE_FIELDS[1], size=9)
    page = _page(doc)
    block(
        page, 180, 200,
        ("A fresh second-page paragraph that closes on its own page.",),
        leading=14,
    )
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, *ASIDE_FIELDS)
    )
    p0 = state.page_frags[0]
    aside = _aside_frags(state, 0)[0]
    par = next(f for f in p0 if f.startswith("<p>"))
    # the aside lands on its own page ahead of the next page's content
    assert p0.index(aside) == p0.index(par) + 1
    assert not any(
        "Received:" in f or "Accepted:" in f
        for f in state.page_frags[1]
    )


def test_queued_asides_keep_page_order(tmp_path):
    """Consecutive pages under one long paragraph drain in page order."""
    doc = fitz.open()
    for n in range(3):
        page = _page(doc)
        text(page, 40, 240, f"Field page {n} marker", size=9)
        text(page, 40, 264, f"Second field page {n}", size=9)
        block(page, 180, 400, PAR_OPEN, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark_all(pages):
        for pi in range(3):
            for b in pages[pi][1]["blocks"]:
                if _block_text(b).startswith(("Field", "Second field")):
                    b["aside"] = pi

    # the paragraph never closes — every page's box queues, then the
    # document-end drain emits them together in page order
    state, _edges = _render_doc(pdf, mark_all)
    p0 = state.page_frags[0]
    asides = _aside_frags(state, 0)
    assert len(asides) == 3
    for n, aside in enumerate(asides):
        assert f"Field page {n} marker" in aside
        assert f"Second field page {n}" in aside
    assert p0.index(asides[0]) > p0.index(
        next(f for f in p0 if f.startswith("<p>"))
    )


def test_document_ending_mid_paragraph_drains_queue(tmp_path):
    """``_flush_paragraph`` — the shared emit tail — keeps every aside."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 40, 240, ASIDE_FIELDS[0], size=9)
    text(page, 40, 264, ASIDE_FIELDS[1], size=9)
    block(page, 180, 400, PAR_OPEN, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, *ASIDE_FIELDS), flush=False
    )
    assert len(state.pending_asides) == 1
    _flush_paragraph(state)
    assert state.pending_asides == []
    p0 = state.page_frags[0]
    assert p0[-1].startswith("<aside")
    assert p0[-2].startswith("<p>")


def test_aside_local_grid_and_caption(tmp_path):
    """A right-side aside (x350-560) emits a real <table> whose rightmost
    cell survives and a captioned image whose alt comes from inside."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 110, "Aside geometry control", size=18)
    # cell widths vary so the recovered columns are wider than the
    # median cell — uniform cells fill their columns edge-to-edge and
    # the prose guard rejects the grid as wrapped paragraph lines
    rows = (
        ("Alpha", "350 msec long value"),
        ("Bravo a longer one", "412"),
        ("Charlie", "298"),
    )
    for ri, (left, right) in enumerate(rows):
        text(page, 360, 200 + ri * 20, left, size=8)
        text(page, 470, 200 + ri * 20, right, size=8)
    image(page, (400, 300, 470, 360))
    text(page, 360, 380, "Figure 1. An aside caption.", size=9)
    block(page, 72, 500, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark(pages):
        for b in pages[0][1]["blocks"]:
            if b["type"] == 1 or _block_text(b).startswith(
                ("Alpha", "Bravo", "Charlie", "Figure 1")
            ):
                b["aside"] = 0

    state, _edges = _render_doc(pdf, mark)
    aside = _aside_frags(state, 0)[0]
    assert "<table>" in aside
    assert "<td>Charlie</td>" in aside and "<td>298</td>" in aside
    assert "<td>350 msec long value</td>" in aside
    assert '<img src="images/img_0.png"' in aside
    assert 'alt="Figure 1. An aside caption."' in aside
    assert "<p>Figure 1. An aside caption.</p>" in aside


def test_aside_image_overlap_vetoes_grid(tmp_path):
    """An image intersecting the would-be grid rejects the <table> —
    the aside grid pass keeps the body pass's image-overlap guard."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 110, "Overlap guard control", size=18)
    rows = (
        ("Alpha", "350 msec long value"),
        ("Bravo a longer one", "412"),
        ("Charlie", "298"),
    )
    for ri, (left, right) in enumerate(rows):
        text(page, 360, 200 + ri * 20, left, size=8)
        text(page, 470, 200 + ri * 20, right, size=8)
    # a figure mixed into the cell rows: a claimed region holding both
    # degrades the labels to paragraphs rather than emitting a spurious
    # table beside the image
    image(page, (400, 195, 470, 250))
    block(page, 72, 500, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark(pages):
        for b in pages[0][1]["blocks"]:
            if b["type"] == 1 or _block_text(b).startswith(
                ("Alpha", "Bravo", "Charlie", "350", "412", "298")
            ):
                b["aside"] = 0

    state, _edges = _render_doc(pdf, mark)
    aside = _aside_frags(state, 0)[0]
    assert "<table>" not in aside
    assert '<img src="images/img_0.png"' in aside
    # the row blocks degrade to paragraphs — each row's two cells are
    # one extracted block, so its lines join with a space
    assert "<p>Alpha 350 msec long value</p>" in aside
    assert "<p>Charlie 298</p>" in aside


def test_body_figure_never_adopts_aside_caption(tmp_path):
    """A body image beside a lifted 'Figure N.' keeps the empty alt."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 110, "Caption isolation control", size=18)
    text(page, 400, 240, "Figure 9. An aside-only caption.", size=9)
    image(page, (72, 300, 200, 380))
    block(page, 72, 500, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    state, _edges = _render_doc(
        pdf, lambda pages: _mark(pages, 0, "Figure 9.")
    )
    html = "\n".join(state.page_frags[0])
    assert '<img src="images/img_0.png" alt=""/>' in html
    aside = _aside_frags(state, 0)[0]
    assert "Figure 9. An aside-only caption." in aside


def test_aside_superscripts_never_link_notes(tmp_path):
    """A raised digit inside an aside stays a plain <sup>."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Superscript control", size=18)
    text(page, 40, 240, ASIDE_FIELDS[0], size=9)
    text(page, 40, 264, ASIDE_FIELDS[1], size=9)
    text(page, 40, 290, "7", size=6)
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark(pages):
        for b in pages[0][1]["blocks"]:
            t = _block_text(b)
            if t.startswith(("Received", "Accepted")) or t == "7":
                b["aside"] = 0

    state, _edges = _render_doc(pdf, mark)
    aside = _aside_frags(state, 0)[0]
    assert "<sup>7</sup>" in aside
    assert "notes.xhtml" not in aside


def test_aside_grid_superscript_stays_plain(tmp_path):
    """A sup digit inside an aside grid cell emits <sup>, never a
    notes.xhtml link — link_fns=False threads through _table_html."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 110, "Grid superscript control", size=18)
    text(page, 360, 200, "Alpha", size=8)
    text(page, 470, 200, "350 msec long value", size=8)
    text(page, 360, 220, "Bravo a longer one", size=8)
    # a note-marker digit as a cell — aside text is never a note
    # source, so it must not cross-link into notes.xhtml
    text(page, 470, 220, "7", size=6)
    text(page, 360, 240, "Charlie", size=8)
    text(page, 470, 240, "298", size=8)
    block(page, 72, 500, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark(pages):
        for b in pages[0][1]["blocks"]:
            t = _block_text(b)
            if t == "7" or t.startswith(
                ("Alpha", "Bravo", "Charlie", "350", "298")
            ):
                b["aside"] = 0

    state, _edges = _render_doc(pdf, mark)
    aside = _aside_frags(state, 0)[0]
    assert "<table>" in aside
    assert "<td><sup>7</sup></td>" in aside
    assert "notes.xhtml" not in aside


def test_aside_title_flag_renders_bold_paragraph(tmp_path):
    """``aside_title`` marks a region's own title — never an <hN>."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 40, 240, "ARTICLE INFORMATION", size=9)
    text(page, 40, 264, ASIDE_FIELDS[0], size=9)
    text(page, 40, 288, ASIDE_FIELDS[1], size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    def mark(pages):
        for b in pages[0][1]["blocks"]:
            t = _block_text(b)
            if t == "ARTICLE INFORMATION":
                b["aside"], b["aside_title"] = 0, True
            elif t.startswith(("Received", "Accepted")):
                b["aside"] = 0

    state, _edges = _render_doc(pdf, mark)
    aside = _aside_frags(state, 0)[0]
    assert '<p class="aside-title">ARTICLE INFORMATION</p>' in aside
    assert "<h" not in aside
    assert state.headings == []


def test_keep_headers_leaves_aside_path_unchanged(tmp_path):
    """drop_edges on/off changes furniture only — the aside is identical."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Keep-headers control", size=18)
    text(page, 40, 240, ASIDE_FIELDS[0], size=9)
    text(page, 40, 264, ASIDE_FIELDS[1], size=9)
    text(page, 300, 770, "7", size=8)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")

    mark = lambda pages: _mark(pages, 0, *ASIDE_FIELDS)
    dropped, _ = _render_doc(pdf, mark, drop_edges=True)
    kept, _ = _render_doc(pdf, mark, drop_edges=False)
    assert (
        _aside_frags(dropped, 0)
        == _aside_frags(kept, 0)
        != []
    )


def test_stylesheet_carries_aside_rules(tmp_path):
    """style.css gains the aside rule — no floats, no fixed widths."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Stylesheet control", size=18)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    assert stats["asides"] == 0
    with zipfile.ZipFile(out) as zf:
        name = next(n for n in zf.namelist() if n.endswith("style.css"))
        css = zf.read(name).decode("utf8")
    assert (
        "aside.sidebar { margin: 1em 0; padding: 0.5em 0.8em; "
        "border: 1px solid #999; font-size: 0.92em; }" in css
    )
    assert "p.aside-title { font-weight: bold; margin-top: 0; }" in css
    assert "float" not in css
