"""Figure-region claims for issue #35 — candidate validation, fallbacks, parity.

The semantic fixtures in ``test_layout_baselines.py`` cover the two happy
paths (vector drawing + labels, raster image + overlay/panel labels). These
tests cover the rest of the issue's acceptance grid end to end: every
negative case must degrade to ordinary text flow — labels staying in the
chapter is the documented miss, never mangled or lost prose.
"""
from __future__ import annotations

import re

import fitz
from builders import _page, _save, block, drawing, image, paragraph, text
from helpers import chapter_body_text, doc_html, doc_texts, lint_epub

from exegesis import convert
from exegesis.analyze import (
    _body_font_size,
    _boilerplate_signatures,
    _detect_asides,
    _doc_column_edges,
    _is_body_block,
    _page_claims,
    _split_signature_prefixes,
    _strip_clip,
)
from exegesis.lang import get_lang


def _claims(pdf_path, page_index=0, mutate=None):
    """Replay convert()'s prep and return the page's committed claims.

    ``mutate`` runs on the post-split dicts where ``_detect_asides``
    would mark members, so tests can inject the same ``aside`` flag the
    real detection would set — asides never reach the body set either.
    """
    doc = fitz.open(str(pdf_path))
    try:
        lang = get_lang("en")
        pages = [(p, p.get_text("dict")) for p in doc]
        body_size, line_h = _body_font_size(pages)
        boiler = _boilerplate_signatures(pages)
        for page, d in pages:
            d["blocks"] = _split_signature_prefixes(
                d["blocks"], page.rect.height, boiler, lang)
        if mutate is not None:
            mutate(pages)
        _detect_asides(pages, line_h, boiler, lang=lang)
        edges = _doc_column_edges(pages, boiler, lang)
        page, pdict = pages[page_index]
        blocks = pdict["blocks"]
        clip = _strip_clip(
            page, [b for b in blocks if b.get("aside") is None],
            boiler, lang)
        body = [
            b for b in blocks
            if b.get("aside") is None
            and _is_body_block(b, page, boiler, lang, clip)
        ]
        return _page_claims(
            page, body, blocks, edges, page.rect.width, line_h,
            body_size, lang)
    finally:
        doc.close()


def _fig_imgs(xhtmls):
    return [m for h in xhtmls.values()
            for m in re.findall(r'<img[^>]*src="images/fig_[^"]*"[^>]*>', h)]


def test_two_figures_one_page(tmp_path):
    """Two validated regions on one page each emit their own raster."""
    doc = fitz.open()
    page = _page(doc)
    for y0, tag in ((230, "ONE"), (520, "TWO")):
        image(page, (180, y0, 440, y0 + 160))
        text(page, 210, y0 + 60, f"{tag}ALPHA")
        text(page, 210, y0 + 110, f"{tag}BETA")
    text(page, 72, 440, "Figure 1. First panels.")
    text(page, 72, 730, "Figure 2. Second panels.")
    pdf = _save(doc, tmp_path / "two_figs.pdf")
    out = tmp_path / "two_figs.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 2
    xhtmls = doc_texts(out)
    assert sum("Figure 1." in t for t in xhtmls.values()) == 1
    assert sum("Figure 2." in t for t in xhtmls.values()) == 1
    body = chapter_body_text(out)
    assert "ONEALPHA" not in body and "TWOALPHA" not in body
    assert len(_fig_imgs(doc_html(out))) == 2
    assert not lint_epub(out)


def test_multi_image_panels_merge(tmp_path):
    """Adjacent panels of one captioned figure share a single raster."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (180, 290, 300, 470))
    image(page, (305, 290, 440, 470))
    text(page, 210, 350, "PANELONE")
    text(page, 320, 420, "PANELTWO")
    text(page, 72, 520, "Figure 1. Two panels.")
    pdf = _save(doc, tmp_path / "panels.pdf")
    out = tmp_path / "panels.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    assert len(_fig_imgs(doc_html(out))) == 1
    body = chapter_body_text(out)
    assert "PANELONE" not in body and "PANELTWO" not in body
    assert body.count("Figure 1. Two panels.") == 1


def test_straddler_falls_back(tmp_path):
    """A block mixing a member line with foreign lines is ambiguous."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
              "Straddler body precedes the figure and stays ordinary prose.")
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "INNERONE")
    text(page, 210, 400, "INNERTWO")
    # first line inside the artwork, second across the bottom edge —
    # one fused block, part label part prose
    block(page, 200, 468,
          ["EDGELINE",
           "wrapped prose continuation that belongs to the text flow."])
    text(page, 72, 520, "Figure 1. Straddler.")
    pdf = _save(doc, tmp_path / "straddler.pdf")
    out = tmp_path / "straddler.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    for kept in ("INNERONE", "INNERTWO", "EDGELINE",
                 "wrapped prose continuation", "Figure 1. Straddler."):
        assert kept in body, kept


def test_foreign_block_in_union_falls_back(tmp_path):
    """Prose crossing the claim can't be cropped around — page fallback."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (180, 290, 440, 470))
    text(page, 210, 330, "INNERONE")
    text(page, 210, 360, "INNERTWO")
    paragraph(page, (160, 390, 520, 460),
              "Foreign prose crosses the figure's union edge to bottom. "
              "It can never rasterize into the claim so the claim dies.")
    text(page, 72, 520, "Figure 1. Unsafe crop.")
    pdf = _save(doc, tmp_path / "unsafe.pdf")
    out = tmp_path / "unsafe.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    assert "INNERONE" in body and "INNERTWO" in body
    assert "Foreign prose crosses" in body


def test_genuine_note_survives_claim(tmp_path):
    """A real note keeps its zone through the provisional-label mask."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
              "Noted body precedes the figure and cites the footnote.")
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "NOTEONE")
    text(page, 210, 400, "NOTETWO")
    text(page, 72, 520, "Figure 1. With note.")
    paragraph(page, (72, 570, 540, 620),
              "Continuation paragraph after the captioned figure.")
    text(page, 72, 700, "1 Genuine note content that must reach notes.",
         size=8)
    pdf = _save(doc, tmp_path / "noted.pdf")
    out = tmp_path / "noted.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    texts = doc_texts(out)
    assert "Genuine note content" in texts.get("notes.xhtml", "")


def test_furniture_inside_union_falls_back(tmp_path):
    """A margin strip inside the union is a barrier, never a member."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
              "Furniture page body keeps ordinary prose.")
    image(page, (36, 290, 440, 470))
    # rotated strip in the left margin, inside the figure's union —
    # the same shape eLife's marginal annotations take; long enough to
    # clear the >4x tall-narrow strip bar
    text(page, 48, 510, "Margin strip runs down the side",
         rotate=90, size=9)
    text(page, 210, 350, "FURNONE")
    text(page, 210, 400, "FURNTWO")
    text(page, 72, 540, "Figure 1. Furniture.")
    pdf = _save(doc, tmp_path / "furn.pdf")
    assert [r for r in _claims(pdf, 0).rasters if r.kind == "figure"] == []
    out = tmp_path / "furn.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    assert "FURNONE" in chapter_body_text(out)


def test_aside_inside_union_falls_back(tmp_path):
    """An aside-marked block inside the union is likewise a barrier."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (36, 290, 440, 470))
    text(page, 60, 380, "INFIELD")  # off the other labels' baselines
    text(page, 210, 350, "ASIDEONE")
    text(page, 210, 430, "ASIDETWO")
    text(page, 72, 520, "Figure 1. Aside.")
    pdf = _save(doc, tmp_path / "aside.pdf")
    figs = [r for r in _claims(pdf, 0).rasters if r.kind == "figure"]
    assert len(figs) == 1

    def _mark(pages):
        for b in pages[0][1]["blocks"]:
            if b["type"] == 0 and fitz.Rect(b["bbox"]).intersects(
                    fitz.Rect(50, 370, 90, 390)):
                b["aside"] = 0

    claims2 = _claims(pdf, 0, mutate=_mark)
    assert [r for r in claims2.rasters if r.kind == "figure"] == []


def test_no_caption_no_claim(tmp_path):
    """Labelled artwork with no figure caption stays ordinary text."""
    doc = fitz.open()
    page = _page(doc)
    drawing(page, (180, 290, 440, 480))
    for y, label in ((330, "NOCAP1"), (390, "NOCAP2"), (450, "NOCAP3")):
        text(page, 210, y, label)
    paragraph(page, (72, 560, 540, 630),
              "Uncaptioned prose discusses the unlabelled figure.")
    pdf = _save(doc, tmp_path / "nocap.pdf")
    out = tmp_path / "nocap.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    for kept in ("NOCAP1", "NOCAP2", "NOCAP3"):
        assert kept in body


def test_artwork_alone_untouched(tmp_path):
    """A drawing with no labels and no caption is not a figure."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
              "Decorative body precedes the rule art.")
    drawing(page, (180, 290, 440, 480))
    text(page, 72, 520, "Figure 1. Bare artwork.")
    paragraph(page, (72, 570, 540, 640),
              "Decorative continuation follows the captioned page.")
    pdf = _save(doc, tmp_path / "bare.pdf")
    out = tmp_path / "bare.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    assert "Figure 1. Bare artwork." in body


def test_short_caption_in_pad_ring_anchors(tmp_path):
    """A terse caption grazing only the pad ring anchors its own claim —
    it must not convict itself as a caption/label mix (#52 review)."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "RINGA")
    text(page, 210, 400, "RINGB")
    # ≤FIGURE_LABEL_CHARS, ~3pt below the artwork bottom — inside the
    # 14pt ring without touching the union
    text(page, 200, 484, "Fig. 1")
    pdf = _save(doc, tmp_path / "ringcap.pdf")
    out = tmp_path / "ringcap.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    body = chapter_body_text(out)
    assert "Fig. 1" in body  # the anchor still renders in flow
    xhtml = "\n".join(doc_html(out).values())
    assert 'alt="Fig. 1"' in xhtml


def test_caption_fused_in_artwork_rejects(tmp_path):
    """A caption-shaped block inside the artwork stays the ambiguous
    mix the straddler rule exists for (#52 review)."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "FUSEA")
    text(page, 210, 400, "FUSEB")
    text(page, 210, 460, "Fig. 1")  # fused inside the artwork
    text(page, 72, 520, "Figure 1. Outer caption.")
    pdf = _save(doc, tmp_path / "fusedcap.pdf")
    out = tmp_path / "fusedcap.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    for kept in ("FUSEA", "FUSEB", "Fig. 1", "Figure 1. Outer caption."):
        assert kept in body


def test_rotated_table_wins_precedence(tmp_path):
    """A rotated-table region inside the candidate keeps its claim."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 150, 540, 210),
              "Rotated precedence body precedes the overlaid table.")
    drawing(page, (190, 280, 460, 460))
    # three rotated tall-narrow blocks sharing a y-band → raster table
    for x in (230, 320, 410):
        text(page, x, 430, f"Cell {x} rotated column",
             rotate=90, size=9)
    text(page, 200, 310, "TICKA")
    text(page, 200, 350, "TICKB")
    text(page, 72, 500, "Figure 1. Overlaid table.")
    paragraph(page, (72, 560, 540, 630),
              "Rotated precedence continuation follows the claim zone.")
    pdf = _save(doc, tmp_path / "rotwins.pdf")
    out = tmp_path / "rotwins.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    assert stats["raster_tables"] == 1
    body = chapter_body_text(out)
    assert "Cell 230" not in body  # rasterized with the table
    assert "TICKA" in body  # fallback leaves labels in the flow


def test_bold_label_never_emits_heading(tmp_path):
    """An in-area bold label claims as artwork text — never an h2."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
              "Heading-adjacent body precedes the bold-labelled figure.")
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "BOLDLABEL", font="hebo")
    text(page, 210, 400, "PLAINLABEL")
    text(page, 72, 520, "Figure 1. Bold label.")
    pdf = _save(doc, tmp_path / "boldlabel.pdf")
    out = tmp_path / "boldlabel.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    assert not re.search(r"<h[12][^>]*>[^<]*BOLDLABEL",
                         "\n".join(doc_html(out).values()))


def test_keep_headers_still_claims(tmp_path):
    """drop_edges=False keeps furniture text but never changes claims."""
    doc = fitz.open()
    for _ in range(3):
        page = _page(doc)
        text(page, 200, 45, "Recurring Journal Head", size=9)
    image(doc[0], (180, 290, 440, 470))
    text(doc[0], 210, 350, "KEEPONE")
    text(doc[0], 210, 400, "KEEPTWO")
    text(doc[0], 72, 520, "Figure 1. Kept head.")
    for page in doc:
        paragraph(page, (72, 190, 540, 250),
                  "Keep-headers body keeps the signature recurring.")
    pdf = _save(doc, tmp_path / "keep.pdf")
    out = tmp_path / "keep.epub"
    stats = convert(pdf, out, drop_edges=False)
    assert stats["raster_figures"] == 1
    body = chapter_body_text(out)
    assert "Recurring Journal Head" in body  # kept as furniture
    assert "KEEPONE" not in body


def test_unrelated_image_emits_separately(tmp_path):
    """A distant image without labels is not pulled into the claim."""
    doc = fitz.open()
    page = _page(doc)
    image(page, (180, 230, 440, 390))
    text(page, 210, 280, "LONEONE")
    text(page, 210, 320, "LONETWO")
    text(page, 72, 440, "Figure 1. Claimed.")
    # far away, no labels, no caption of its own
    image(page, (180, 560, 440, 700))
    pdf = _save(doc, tmp_path / "unrelated.pdf")
    out = tmp_path / "unrelated.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    xhtml = "\n".join(doc_html(out).values())
    assert xhtml.count('src="images/fig_') == 1
    assert 'src="images/img_' in xhtml  # the unrelated image still emits


def test_caption_in_other_column_no_claim(tmp_path):
    """A caption in the adjacent column shares no band with the
    artwork — `_overlap_cols` leaves the candidate unanchored."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 150, 290, 215),
              "Left column body sets the first column edge here.")
    paragraph(page, (322, 150, 540, 215),
              "Right column body sets the second column edge too.")
    image(page, (100, 290, 300, 470))
    text(page, 130, 350, "COLA")
    text(page, 200, 400, "COLB")
    text(page, 330, 490, "Figure 1. Right side.")
    paragraph(page, (72, 560, 290, 630),
              "Left column prose continues below the figure zone.")
    pdf = _save(doc, tmp_path / "othercol.pdf")
    out = tmp_path / "othercol.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 0
    body = chapter_body_text(out)
    for kept in ("COLA", "COLB", "Figure 1. Right side."):
        assert kept in body


def test_full_width_figure_spans_gutter(tmp_path):
    """A figure crossing the gutter shares a band with a caption in
    either column and claims normally."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 150, 290, 215),
              "Left column body sets the first column edge here.")
    paragraph(page, (322, 150, 540, 215),
              "Right column body sets the second column edge too.")
    image(page, (100, 290, 500, 470))
    text(page, 150, 350, "WIDEA")
    text(page, 350, 400, "WIDEB")
    text(page, 72, 520, "Figure 1. Full width.")
    paragraph(page, (72, 560, 290, 630),
              "Left column prose continues below the figure zone.")
    paragraph(page, (322, 560, 540, 630),
              "Right column prose continues below the figure too.")
    pdf = _save(doc, tmp_path / "wide.pdf")
    out = tmp_path / "wide.epub"
    stats = convert(pdf, out)
    assert stats["raster_figures"] == 1
    assert len(_fig_imgs(doc_html(out))) == 1
    body = chapter_body_text(out)
    assert "WIDEA" not in body and "WIDEB" not in body
    assert "Figure 1. Full width." in body
