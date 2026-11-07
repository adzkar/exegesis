"""Margin-band marginalia detection (#36): lift, anchor, negatives.

``_detect_margins`` runs between the column-edge vote and the reading-
column map: blocks wholly outside the grid's occupied body bands are
candidates, and a candidate strip earns a ``block["aside"]`` region key
only with sidebar evidence — a table legend, a ``Label:`` opener,
grouped ``info_labels`` fields, or a punctuation-structured fragment
recurring across pages. Serial-numbered strips (manuscript line
numbering) never carry evidence; ``Figure N`` legends stay with the
figure machinery; and an outermost *edge* can classify as a voted
margin column only when narrow and member-dominated by sidebar
evidence, with one bounded grid re-vote.
"""
from __future__ import annotations

import fitz
from builders import _page, _save, block, image, paragraph, text
from helpers import chapter_body_text, corpus_pdf, doc_html, lint_epub
from layout_fixtures import outdented_heads, signature_outdent_margin

from exegesis import convert
from exegesis.analyze import (
    _body_font_size,
    _boilerplate_signatures,
    _detect_asides,
    _detect_margins,
    _doc_column_edges,
    _split_signature_prefixes,
)
from exegesis.text import _block_text

BODY_X = 200
MARGIN_X = 36
PROSE = (
    "Body prose fills the column and keeps ordinary reading order",
    "through the page so the margin strip beside it stays secondary.",
)


def _marks(pdf_path):
    """Blocks the margin detector claimed, as ``(page, key, text)``."""
    with fitz.open(pdf_path) as doc:
        pages = [(p, p.get_text("dict")) for p in doc]
        _, line_h = _body_font_size(pages)
        boilerplate = _boilerplate_signatures(pages)
        for page, pdict in pages:
            pdict["blocks"] = _split_signature_prefixes(
                pdict["blocks"], page.rect.height, boilerplate
            )
        _detect_asides(pages, line_h, boilerplate)
        edges = _doc_column_edges(pages, boilerplate)
        _detect_margins(pages, edges, boilerplate)
        return [
            (pi, b["aside"], _block_text(b))
            for pi, (_p, pdict) in enumerate(pages)
            for b in pdict["blocks"]
            if isinstance(b.get("aside"), tuple)
        ]


def _full_html(path):
    """Every chapter document concatenated — asides included."""
    return "\n".join(doc_html(path).values())


def _aside_text(html):
    return "\n".join(f for f in html.splitlines() if f.startswith("<aside"))


def _margin_table(path):
    """A sparse-margin table: a grid in the x200 body column whose legend
    and significance notes sit in the x36 margin — the PLOS shape.

    Two pages: page 0 is plain body prose. The x36 strip votes its own
    edge on page 1 (a *voted* margin column), so the lift comes through
    the voted-edge arm and the grid re-votes back to one edge."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 200, 560, 300), PROSE[0] + " opening page.")
    paragraph(page, (BODY_X, 400, 560, 500), PROSE[1] + " opening page.")
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0] + " above the table.")
    rows = (
        ("Alpha", "350 msec long value"),
        ("Beta value", "412"),
        ("Gamma", "298"),
    )
    for ri, (left, right) in enumerate(rows):
        text(page, BODY_X, 300 + ri * 20, left, size=8)
        text(page, 400, 300 + ri * 20, right, size=8)
    text(page, MARGIN_X, 290, "Table 1. Sparse margin legend.", size=9)
    text(page, MARGIN_X, 340, "Note: *p<0.1;", size=9)
    paragraph(page, (BODY_X, 430, 560, 495), PROSE[1] + " below the table.")
    return _save(doc, path)


def test_margin_table_legend_anchors_into_table(tmp_path):
    """The margin legend lands in ``<caption>`` beside its table — the
    significance notes emit in an ``<aside>`` immediately after, never
    interleaved with body paragraphs."""
    pdf = _margin_table(tmp_path / "margin_table.pdf")
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    html = _full_html(out)

    assert stats["grid_tables"] == 1
    assert stats["asides"] == 1
    assert lint_epub(out) == []
    assert "<caption>Table 1. Sparse margin legend.</caption>" in html
    # the remaining legend member rides the anchored aside right behind
    # the table — adjacent, not at page end among the paragraphs
    tail = html.split("</table>", 1)[1]
    assert tail.lstrip().startswith('<aside class="sidebar">')
    assert "Note: *p&lt;0.1;" in tail.split("</aside>", 1)[0]
    # neither legend piece leaks into body-flow paragraphs
    body = chapter_body_text(out)
    assert body.count("Table 1. Sparse margin legend.") == 1
    assert body.count("Note: *p<0.1;") == 1


MARGIN_LEGEND = (
    "Table 1. Rotated margin legend with a long descriptive title "
    "fused to its first sentence so the alt must carry the whole "
    "block — a tail like this one ending in ZZALTAIL can never "
    "silently drop out of the raster entirely."
)


def _margin_raster_table(path):
    """A rotated table in the body column whose margin strip carries the
    legend — the raster arm of legend anchoring: the ``Table N`` block
    donates the PNG's alt and the rest of the strip emits as an aside.
    The legend runs past 200 chars so an alt cap would lose its tail."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 200, 560, 300), PROSE[0] + " opening page.")
    paragraph(page, (BODY_X, 400, 560, 500), PROSE[1] + " opening page.")
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0] + " above the table.")
    for i, x in enumerate((BODY_X, 260, 320)):
        page.insert_textbox(
            fitz.Rect(x, 380, x + 15, 520), f"r1c{i} r2c{i} r3c{i}",
            fontsize=10,
        )
    rc = page.insert_textbox(
        fitz.Rect(MARGIN_X, 330, MARGIN_X + 110, 590), MARGIN_LEGEND,
        fontsize=9,
    )
    assert rc >= 0, "margin legend textbox overflowed"
    text(page, MARGIN_X, 620, "Note: *p<0.1;", size=9)
    paragraph(page, (BODY_X, 620, 560, 685), PROSE[1] + " below the table.")
    return _save(doc, path)


def test_margin_raster_legend_binds_into_alt(tmp_path):
    """A margin legend beside a *raster* table donates the PNG's alt —
    the ``Table N`` block leaves the aside members, so the whole legend
    emits inside ``alt`` only while the note rides the anchored aside."""
    pdf = _margin_raster_table(tmp_path / "margin_raster.pdf")
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    html = _full_html(out)

    assert stats["raster_tables"] == 1
    assert stats["asides"] == 1
    assert lint_epub(out) == []
    assert 'alt="Table 1.' in html
    # the whole donated block carries — a >200-char legend keeps its
    # tail inside the alt rather than dropping silently
    assert "ZZALTAIL" in html
    # the remaining legend member rides the anchored aside right behind
    # the raster — adjacent, not at page end among the paragraphs
    tail = html.split('alt="Table 1.', 1)[1]
    rest = tail.split('"/></p>', 1)[1]
    assert rest.lstrip().startswith('<aside class="sidebar">')
    assert "Note: *p&lt;0.1;" in rest.split("</aside>", 1)[0]
    # the legend emits only inside the alt — never as visible text —
    # while the note stays visible through the aside
    body = chapter_body_text(out)
    assert "descriptive" not in body
    assert "ZZALTAIL" not in body
    assert body.count("Note: *p<0.1;") == 1


def test_margin_members_all_preserved(tmp_path):
    """A margin group preserves every text token and image member —
    a strip below the edge quorum is a *band outsider*, and its image
    member rides the lifted group."""
    doc = fitz.open()
    for _ in range(4):
        page = _page(doc)
        paragraph(page, (BODY_X, 200, 560, 280), PROSE[0])
        paragraph(page, (BODY_X, 400, 560, 480), PROSE[1])
    page = doc[1]  # the strip lives only on page 1 — below the quorum
    text(
        page, MARGIN_X, 260,
        "Competing interests: none declared.", size=8,
    )
    text(page, MARGIN_X, 300, "Funding: none received.", size=8)
    image(page, (30, 380, 140, 460))
    pdf = _save(doc, tmp_path / "margin_members.pdf")
    out = tmp_path / "out.epub"
    convert(pdf, out)
    html = _full_html(out)

    asides = [f for f in html.splitlines() if f.startswith("<aside")]
    assert len(asides) == 1
    aside = asides[0]
    assert "Competing interests: none declared." in aside
    assert "Funding: none received." in aside
    assert '<img src="images/img_0.png"' in aside
    body = chapter_body_text(out)
    for token in ("Competing interests", "Funding", PROSE[0]):
        assert token in body


def test_line_numbered_margin_stays_body(tmp_path):
    """Serial-numbered margin strips — manuscript line numbering — are
    vetoed: they keep emitting in body flow and never mark an aside."""
    doc = fitz.open()
    for number in range(6):
        page = _page(doc)
        paragraph(
            page, (150, 190, 570, 260),
            f"Manuscript body {number} fills the column.",
        )
        text(page, 90, 320, f"Aim {number:02d}", font="hebo")
        text(page, 90, 510, f"Data {number:02d}", font="hebo")
    pdf = _save(doc, tmp_path / "numbered_margin.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    for number in range(6):
        assert f"Aim {number:02d}" in body
        assert f"Data {number:02d}" in body


def test_outdented_heads_never_lift(tmp_path):
    """A voted outdent edge's heads stay body flow — the voted-margin arm
    can't claim a strip of section heads."""
    pdf = outdented_heads(tmp_path / "outdented.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    for number in range(3, 6):
        assert f"Section {number} alpha" in body
        assert f"Section {number} beta" in body


def test_signature_outdent_margin_never_lift(tmp_path):
    """The 29-page outdent margin fixture keeps its serial strip."""
    pdf = signature_outdent_margin(tmp_path / "sigmargin.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    assert "Aim 00" in body and "Data 00" in body


def test_pull_quote_stays_in_flow(tmp_path):
    """An indented, isolated quote inside the band is a blockquote — the
    margin detector can't see inside-band blocks at all."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0] + ".")
    block(
        page, BODY_X + 40, 300,
        ["An indented pull quote sits inside the body band",
         "and must keep emitting as a blockquote."],
        leading=16,
    )
    paragraph(page, (BODY_X, 430, 560, 495), PROSE[1])
    pdf = _save(doc, tmp_path / "pullquote.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    convert(pdf, out)
    assert "blockquote" in _full_html(out)


def test_full_width_span_stays_in_flow(tmp_path):
    """A span crossing the body edge intersects the band — it can never
    be a margin candidate."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0])
    paragraph(
        page, (36, 260, 576, 330),
        "Full-width content crosses the column's left edge and must "
        "survive in body flow exactly once.",
    )
    paragraph(page, (BODY_X, 430, 560, 495), PROSE[1])
    pdf = _save(doc, tmp_path / "span.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    convert(pdf, out)
    assert chapter_body_text(out).count("Full-width content crosses") == 1


def test_body_figure_and_caption_unchanged(tmp_path):
    """A figure with an in-band caption stays in body flow; the
    ``Figure N`` legend is figure-domain, never a margin candidate —
    even when it sits outside the band (it may be adopted as alt text,
    but never lifted into an aside)."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0])
    image(page, (BODY_X, 250, 340, 400))
    text(page, BODY_X, 440, "Figure 1. Body caption.", size=9)
    # a margin-positioned figure legend stays out of asides too
    text(page, MARGIN_X, 500, "Figure 2. Margin-side legend.", size=9)
    paragraph(page, (BODY_X, 560, 560, 625), PROSE[1])
    pdf = _save(doc, tmp_path / "figcap.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    convert(pdf, out)
    html = _full_html(out)
    assert "Figure 1. Body caption." in chapter_body_text(out)
    assert "Figure 2. Margin-side legend." not in _aside_text(html)


def test_lone_margin_block_needs_evidence(tmp_path):
    """A lone margin block with no sidebar evidence stays body flow."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0])
    block(
        page, 18, 300,
        ["Ordinary margin prose", "without any label or repeat."],
    )
    paragraph(page, (BODY_X, 430, 560, 495), PROSE[1])
    pdf = _save(doc, tmp_path / "lone.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    convert(pdf, out)
    body = chapter_body_text(out)
    assert "Ordinary margin prose without any label" in body


def test_lone_info_label_is_not_marginalia(tmp_path):
    """A lone ``Received:`` field is #41's box fragment — one labeled
    field alone never self-evidences a margin aside."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (BODY_X, 120, 560, 185), PROSE[0])
    text(page, 40, 260, "Received: 3 March 2024", size=9)
    paragraph(page, (BODY_X, 430, 560, 495), PROSE[1])
    pdf = _save(doc, tmp_path / "lonelabel.pdf")
    assert _marks(pdf) == []
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    assert stats["asides"] == 0
    assert "Received: 3 March 2024" in chapter_body_text(out)


def test_keep_headers_margin_parity(tmp_path):
    """``--keep-headers`` changes furniture handling only — the margin
    strip still lifts into the same aside."""
    pdf = _margin_table(tmp_path / "keep_headers.pdf")
    out = tmp_path / "out.epub"
    stats = convert(pdf, out, drop_edges=False)
    html = _full_html(out)
    assert stats["asides"] == 1
    assert "<caption>Table 1. Sparse margin legend.</caption>" in html
    assert "Note: *p&lt;0.1;" in html


def test_ambiguous_two_tables_degrade_to_page_aside(tmp_path):
    """Two detected tables make the legend's owner ambiguous — it emits
    as a whole page-end aside, caption included, rather than binding."""
    doc = fitz.open()
    for _ in range(4):
        _page(doc)
    for pi in (0, 2, 3):
        page = doc[pi]
        paragraph(page, (BODY_X, 200, 560, 280), PROSE[0])
        paragraph(page, (BODY_X, 400, 560, 480), PROSE[1])
    page = doc[1]
    # page 1 carries two grids inside the same body column, with its
    # margin legend beside the upper one
    paragraph(page, (BODY_X, 150, 560, 210), PROSE[0])
    paragraph(page, (BODY_X, 650, 560, 710), PROSE[1])
    rows_a = (
        ("Alpha", "350 msec long value"),
        ("Beta value", "412"),
        ("Gamma", "298"),
    )
    for ri, (left, right) in enumerate(rows_a):
        text(page, BODY_X, 250 + ri * 20, left, size=8)
        text(page, 400, 250 + ri * 20, right, size=8)
    rows_b = (
        ("Delta", "second measurement value"),
        ("Epsilon", "2.00"),
        ("Zeta", "3.00"),
    )
    for ri, (left, right) in enumerate(rows_b):
        text(page, BODY_X, 360 + ri * 20, left, size=8)
        text(page, 400, 360 + ri * 20, right, size=8)
    text(page, MARGIN_X, 240, "Table 1. Ambiguous margin legend.", size=9)
    text(page, MARGIN_X, 300, "Note: *p<0.1;", size=9)
    pdf = _save(doc, tmp_path / "twotables.pdf")
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    html = _full_html(out)

    assert stats["grid_tables"] == 2
    assert stats["asides"] == 1
    # the legend stays whole inside the aside — it is not spliced into
    # either table's <caption>
    aside = next(f for f in html.splitlines() if f.startswith("<aside"))
    assert "Table 1. Ambiguous margin legend." in aside
    assert "Note: *p&lt;0.1;" in aside
    assert html.count("<caption>") == 0


def test_voted_margin_column_lift_and_revote(tmp_path):
    """A sidebar column dense enough to vote an edge classifies as a
    voted margin column: members lift and the grid re-votes away the
    phantom edge once. Members are generic ``Label:`` fragments — not
    ``info_labels`` — so #41's page-0/1 box detector doesn't reach them
    first."""
    doc = fitz.open()
    for _ in range(4):
        page = _page(doc)
        text(page, 40, 240, "Competing interests: none", size=9)
        text(page, 40, 264, "Reprint requests: lab@x.org", size=9)
        text(page, 40, 288, "Printer: Example Press", size=9)
        paragraph(page, (180, 300, 560, 365), PROSE[0])
        paragraph(page, (180, 430, 560, 495), PROSE[1])
    pdf = _save(doc, tmp_path / "voted.pdf")
    marks = _marks(pdf)
    assert marks
    assert all(key[0] == "margin-edge" for _pi, key, _text in marks)
    out = tmp_path / "out.epub"
    stats = convert(pdf, out)
    body = chapter_body_text(out)
    for field in ("Competing interests: none", "Reprint requests: lab@x.org",
                  "Printer: Example Press"):
        assert field in body
    assert _full_html(out).count("<aside") >= 1
    assert stats["columns"] == 1  # the phantom sidebar edge surrendered


# ----------------------------- corpus ---------------------------------

PLOS = "journals/research-and-open-science/plosone_marginalia.pdf"
PEERJ = "journals/natural-sciences/peerj_oa.pdf"
ELIFE = "journals/psychology-and-neuroscience/elife_marginalia.pdf"
BUNDESGES = "journals/medicine-and-public-health/bundesges_de.pdf"
EBIOMEDICINE = "journals/medicine-and-public-health/ebiomedicine_lancet.pdf"


def test_plos_margin_legends(tmp_path):
    """PLOS' sparse-margin legends lift into captions + asides; the
    competing-interest note and DOI links leave body flow."""
    pdf = corpus_pdf(PLOS)
    out = tmp_path / "plos.epub"
    stats = convert(pdf, out)
    html = _full_html(out)

    assert lint_epub(out) == []
    assert stats["asides"] >= 4
    assert (
        "<caption><b>Table 5. Results for the base model.</b></caption>"
        in html
    )
    assert (
        "<caption><b>Table 6. Results for the full model.</b></caption>"
        in html
    )
    aside = _aside_text(html)
    for needle in (
        "Competing interests:",
        "journal.pone.0311493.t002",
        "journal.pone.0311493.t005",
        "journal.pone.0311493.t006",
    ):
        assert needle in aside, needle
    # legend members remain visible text (asides stay inside body text)
    body = chapter_body_text(out)
    assert "Competing interests" in body
    assert "journal.pone.0311493.t006" in body


def test_peerj_sidebar_lifts(tmp_path):
    """PeerJ's article-information sidebar lifts to an aside."""
    pdf = corpus_pdf(PEERJ)
    out = tmp_path / "peerj.epub"
    stats = convert(pdf, out)
    html = _full_html(out)

    assert lint_epub(out) == []
    assert stats["asides"] >= 3
    aside = _aside_text(html)
    for needle in (
        "Submitted 11 March 2026",
        "Academic editor",
        "peerj.21912",
        "OPEN ACCESS",
        "Notes.",
    ):
        assert needle in aside, needle


def test_elife_frontmatter_margin(tmp_path):
    """eLife's front-matter correspondence/funding strip lifts; deep-page
    figure labels (axis ticks, panel letters) never do."""
    pdf = corpus_pdf(ELIFE)
    marks = _marks(pdf)
    marked = {text for _pi, _key, text in marks}
    assert any("For correspondence" in t for t in marked)
    assert any("Funding: See page" in t for t in marked)
    # figure-domain fragments must not be stolen into asides
    assert not any(t in {"A", "B", "C", "D", "G", "or"} for t in marked)
    out = tmp_path / "elife.epub"
    stats = convert(pdf, out)
    assert lint_epub(out) == []
    assert stats["asides"] >= 1
    aside = _aside_text(_full_html(out))
    assert "For correspondence" in aside
    assert "Funding: See page" in aside


def test_bundesges_sidebar_stays_column(tmp_path):
    """Bundesges' sidebar is a *voted* body column, not marginalia —
    the detector leaves it in body flow."""
    pdf = corpus_pdf(BUNDESGES)
    assert _marks(pdf) == []


def test_ebiomedicine_citation_margin(tmp_path):
    """eBioMedicine's citation strip is real marginalia and lifts."""
    pdf = corpus_pdf(EBIOMEDICINE)
    out = tmp_path / "ebiomedicine.epub"
    convert(pdf, out)
    assert lint_epub(out) == []
    aside = _aside_text(_full_html(out))
    assert "eBioMedicine" in aside and "106528" in aside
