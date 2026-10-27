"""Synthetic-PDF integration tests: pages are built with PyMuPDF at
controlled positions/sizes, so assertions hold without any corpus files."""
import zipfile

import fitz
import pytest
from helpers import chapter_text, doc_html, doc_texts, lint_epub, snapshot_epub

import pdf_to_epub as m

W, H = 612, 792
BODY = 10


def _save(doc, path):
    doc.save(str(path))
    doc.close()


def _page(doc):
    return doc.new_page(width=W, height=H)


def test_two_column_reading_order(tmp_path):
    doc = fitz.open()
    left_a = "The left column opens here with a complete sentence."
    left_b = "More left text follows, still in the first column."
    right_a = "The right column reads only after the left ends."
    right_b = "Final right column sentence closes the paper."
    for _ in range(2):
        p = _page(doc)
        # keep column text below the top edge zone — identical text
        # repeated near the top of every page reads as a running head and
        # is dropped as signature boilerplate
        p.insert_textbox(fitz.Rect(72, 180, 290, 300), left_a, fontsize=BODY)
        p.insert_textbox(fitz.Rect(72, 340, 290, 460), left_b, fontsize=BODY)
        p.insert_textbox(fitz.Rect(340, 180, 560, 300), right_a, fontsize=BODY)
        p.insert_textbox(fitz.Rect(340, 340, 560, 460), right_b, fontsize=BODY)
    pdf = tmp_path / "twocol.pdf"
    _save(doc, pdf)
    out = tmp_path / "twocol.epub"
    m.convert(pdf, out)
    assert lint_epub(out) == []
    text = chapter_text(out)
    assert "left column" in text and "right column" in text
    assert text.index("left column") < text.index("first column")
    assert text.index("first column") < text.index("right column")


def test_furniture_and_page_number_stripped(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        "Body text of a single column document with several sentences.",
        fontsize=BODY,
    )
    p.insert_textbox(fitz.Rect(72, 760, 540, 780), "www.journal.org VOL 340", fontsize=9)
    p.insert_textbox(fitz.Rect(300, 20, 330, 45), "7", fontsize=9)
    pdf = tmp_path / "furniture.pdf"
    _save(doc, pdf)
    out = tmp_path / "furniture.epub"
    m.convert(pdf, out)
    text = chapter_text(out)
    assert "journal.org" not in text
    assert "single column document" in text
    # the lone page-number digit must not survive as a paragraph
    assert "7" not in text


def test_keep_headers_preserves_furniture(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        "Body text of a single column document with several sentences.",
        fontsize=BODY,
    )
    p.insert_textbox(fitz.Rect(72, 760, 540, 780), "www.journal.org VOL 340", fontsize=9)
    p.insert_textbox(fitz.Rect(300, 20, 330, 45), "7", fontsize=9)
    pdf = tmp_path / "keephdr.pdf"
    _save(doc, pdf)
    out = tmp_path / "keephdr.epub"
    m.convert(pdf, out, drop_edges=False)
    # furniture is retained verbatim as standalone paragraphs — it never
    # merges into body paragraphs or lands in notes.xhtml
    text = " ".join(doc_texts(out).values())
    assert "journal.org" in text
    assert "VOL 340" in text
    assert "7" in text
    htmls = doc_html(out)
    assert any("<p>7</p>" in h for h in htmls.values())
    assert any(
        "<p>www.journal.org VOL 340</p>" in h
        for n, h in htmls.items()
        if n.startswith("chap_")
    )


def test_keep_headers_wide_footer_keeps_column_order(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    left_a = "The left column opens here with a complete sentence."
    left_b = "More left text follows, still in the first column."
    right_a = "The right column reads only after the left ends."
    right_b = "Final right column sentence closes the paper."
    p.insert_textbox(fitz.Rect(72, 80, 290, 200), left_a, fontsize=BODY)
    p.insert_textbox(fitz.Rect(72, 320, 290, 440), left_b, fontsize=BODY)
    p.insert_textbox(fitz.Rect(340, 80, 560, 200), right_a, fontsize=BODY)
    p.insert_textbox(fitz.Rect(340, 320, 560, 440), right_b, fontsize=BODY)
    # a full-width furniture footer crosses the gutter (its glyph extent,
    # not the box) — kept verbatim, but it must not become a span whose
    # `cut` collapses the page into interleaved (y0, x0) order
    p.insert_textbox(
        fitz.Rect(72, 745, 560, 765),
        "www.journal.org VOL 340 ISSN 1234-5678 downloaded from journal.org"
        " archive 10 Oct 2026",
        fontsize=9,
    )
    pdf = tmp_path / "widefooter.pdf"
    _save(doc, pdf)
    out = tmp_path / "widefooter.epub"
    m.convert(pdf, out, drop_edges=False)
    text = chapter_text(out)
    assert text.index("left column") < text.index("first column")
    assert text.index("first column") < text.index("right column")
    assert text.index("right column") < text.index("closes the paper")
    assert "journal.org" in text


def test_bold_marker_tail_line_not_heading(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 400),
        " ".join(["Reference entries fill the column above."] * 10),
        fontsize=BODY,
    )
    # a bold footnote-marker line trailing the section must not promote
    p.insert_textbox(
        fitz.Rect(72, 600, 540, 640),
        "*indicates the marked entries",
        fontsize=BODY,
        fontname="hebo",
    )
    pdf = tmp_path / "markerh2.pdf"
    _save(doc, pdf)
    out = tmp_path / "markerh2.epub"
    m.convert(pdf, out)
    htmls = doc_html(out)
    assert not any("<h2" in h and "indicates" in h for h in htmls.values())


def test_bold_tail_line_without_follower_not_heading(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 400),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # last block of its column and outside the edge zones: a bold line that
    # introduces nothing must not promote to h2
    p.insert_textbox(
        fitz.Rect(72, 550, 540, 590),
        "Closing bold remark",
        fontsize=BODY,
        fontname="hebo",
    )
    pdf = tmp_path / "tailline.pdf"
    _save(doc, pdf)
    out = tmp_path / "tailline.epub"
    m.convert(pdf, out)
    htmls = doc_html(out)
    assert not any("<h2" in h and "Closing bold" in h for h in htmls.values())
    assert "Closing bold remark" in chapter_text(out)


def test_demoted_bold_tail_lines_stay_demoted(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 400),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # 10.6pt sits in the [1.05, 1.08) band: the block-level gate demotes it
    # (no follower), but the per-line check would re-promote it — the
    # demotion must hold for every line of the block
    p.insert_textbox(
        fitz.Rect(72, 550, 540, 590),
        "Closing bold remark",
        fontsize=10.6,
        fontname="hebo",
    )
    pdf = tmp_path / "demoted.pdf"
    _save(doc, pdf)
    out = tmp_path / "demoted.epub"
    m.convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2" in h or "<h1" in h for h in chaps)
    assert "Closing bold remark" in chapter_text(out)


def test_label_respects_bold_demotion(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # a trailing bold "Funding …" tail is a note, not a section head —
    # demotion must suppress the label path too
    p.insert_textbox(
        fitz.Rect(72, 550, 540, 570),
        "Funding NIH grant R01-GM123456",
        fontsize=10.6,
        fontname="hebo",
    )
    pdf = tmp_path / "funding.pdf"
    _save(doc, pdf)
    out = tmp_path / "funding.epub"
    m.convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2" in h or "<h1" in h for h in chaps)
    assert "Funding NIH grant" in chapter_text(out)


def test_mixed_block_bold_band_line_demoted(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # a mixed-weight block never classifies at block level, but its bold
    # [1.05,1.08)x line still needs a body follower — this one has none
    p.insert_text(
        fitz.Point(72, 512), "Tail remark", fontsize=10.6, fontname="hebo"
    )
    p.insert_text(
        fitz.Point(72, 525), "and a trailing sentence.", fontsize=BODY
    )
    pdf = tmp_path / "mixtail.pdf"
    _save(doc, pdf)
    out = tmp_path / "mixtail.epub"
    m.convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2" in h or "<h1" in h for h in chaps)
    assert "Tail remark" in chapter_text(out)


def test_demoted_multiline_bold_block_no_line_headings(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 400),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # two-line all-bold trailing block: demoted once, but each line used to
    # re-emit its own h2 + chapter split
    p.insert_textbox(
        fitz.Rect(72, 550, 152, 600),
        "Closing bold remark",
        fontsize=10.6,
        fontname="hebo",
    )
    pdf = tmp_path / "demotedmulti.pdf"
    _save(doc, pdf)
    out = tmp_path / "demotedmulti.epub"
    m.convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2" in h or "<h1" in h for h in chaps)
    assert "Closing bold remark" in chapter_text(out)


def test_bold_line_followed_only_by_notes_not_heading(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 300),
        " ".join(["Reference entries fill the column above."] * 10),
        fontsize=BODY,
    )
    # the bold line's only follower is the footnote zone — note blocks leave
    # the body flow, so they can't qualify it as a section heading
    p.insert_textbox(
        fitz.Rect(72, 400, 540, 440),
        "Section tail remark",
        fontsize=BODY,
        fontname="hebo",
    )
    p.insert_textbox(
        fitz.Rect(72, 650, 540, 720),
        "Requests for reprints should be addressed to the author.",
        fontsize=8.5,
    )
    pdf = tmp_path / "notetail.pdf"
    _save(doc, pdf)
    out = tmp_path / "notetail.epub"
    m.convert(pdf, out)
    htmls = doc_html(out)
    texts = doc_texts(out)
    assert not any("<h2" in h and "Section tail" in h for h in htmls.values())
    assert "Section tail remark" in chapter_text(out)
    assert "reprints" in texts["notes.xhtml"]


def test_bold_line_followed_only_by_table_not_heading(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Body text above the section tail.",
        fontsize=BODY,
    )
    # only followers are rasterized table cells — they leave the body flow,
    # so a trailing bold line still must not promote to h2
    p.insert_textbox(
        fitz.Rect(72, 230, 540, 260),
        "Introduces a table",
        fontsize=BODY,
        fontname="hebo",
    )
    for i, x in enumerate((200, 260, 320)):
        p.insert_textbox(
            fitz.Rect(x, 400, x + 15, 520),
            f"r1c{i} r2c{i} r3c{i}",
            fontsize=BODY,
        )
    pdf = tmp_path / "tabletail.pdf"
    _save(doc, pdf)
    out = tmp_path / "tabletail.epub"
    m.convert(pdf, out)
    with zipfile.ZipFile(out) as zf:
        assert [n for n in zf.namelist() if "table_" in n], "expected table image"
    htmls = doc_html(out)
    assert not any(
        "<h2" in h and "Introduces a table" in h for h in htmls.values()
    )


def test_keep_headers_margin_strips_not_rasterized(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 400),
        " ".join(["Body text fills the single column."] * 8),
        fontsize=BODY,
    )
    # three rotated margin strips share a y-band — kept verbatim under
    # --keep-headers, but they must never seed a rotated-table region
    for i, x in enumerate((565, 580, 595)):
        p.insert_text(
            fitz.Point(x, 350), f"JOURNAL SIDEBAR {i}", fontsize=9, rotate=90
        )
    pdf = tmp_path / "sidebars.pdf"
    _save(doc, pdf)
    out = tmp_path / "sidebars.epub"
    m.convert(pdf, out, drop_edges=False)
    with zipfile.ZipFile(out) as zf:
        assert not [n for n in zf.namelist() if "table_" in n]
    assert "JOURNAL SIDEBAR" in chapter_text(out)


def test_keep_headers_strip_does_not_inject_abstract(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 400),
        " ".join(["Body text fills the single column."] * 8),
        fontsize=BODY,
    )
    # a small-font sidebar in the top half of page 0 satisfies the abstract
    # heuristic's inset+size+position checks — kept furniture must never
    # reach it
    p.insert_text(
        fitz.Point(585, 350), "JOURNAL OF EXAMPLES", fontsize=9, rotate=90
    )
    pdf = tmp_path / "sidebarabstract.pdf"
    _save(doc, pdf)
    out = tmp_path / "sidebarabstract.epub"
    m.convert(pdf, out, drop_edges=False)
    htmls = doc_html(out)
    assert not any("<h2" in h and "Abstract" in h for h in htmls.values())
    assert "JOURNAL OF EXAMPLES" in chapter_text(out)


def test_keep_headers_signature_head_no_abstract(tmp_path):
    doc = fitz.open()
    for _ in range(5):
        p = _page(doc)
        # a running head repeated on every page is signature boilerplate —
        # kept verbatim under --keep-headers, but flagged like any other
        # furniture so it can't reach the abstract/heading/follower logic
        p.insert_textbox(
            fitz.Rect(200, 40, 400, 65), "Journal of Studies", fontsize=9
        )
        p.insert_textbox(
            fitz.Rect(72, 100, 540, 400),
            " ".join(["Body text fills the single column."] * 8),
            fontsize=BODY,
        )
    pdf = tmp_path / "sighead.pdf"
    _save(doc, pdf)
    out = tmp_path / "sighead.epub"
    m.convert(pdf, out, drop_edges=False)
    htmls = doc_html(out)
    assert not any("<h2" in h and "Abstract" in h for h in htmls.values())
    assert "Journal of Studies" in chapter_text(out)


def test_keep_headers_signature_head_flagged_short_doc(tmp_path):
    doc = fitz.open()
    for _ in range(3):
        p = _page(doc)
        # three pages: enough for the signature layer to recognize a
        # repeated running head even though page_count <= 3
        p.insert_textbox(
            fitz.Rect(200, 40, 400, 65), "Journal of Studies", fontsize=9
        )
        p.insert_textbox(
            fitz.Rect(72, 100, 540, 400),
            " ".join(["Body text fills the single column."] * 8),
            fontsize=BODY,
        )
    pdf = tmp_path / "sighead3.pdf"
    _save(doc, pdf)
    out = tmp_path / "sighead3.epub"
    m.convert(pdf, out, drop_edges=False)
    htmls = doc_html(out)
    assert not any("<h2" in h and "Abstract" in h for h in htmls.values())
    assert "Journal of Studies" in chapter_text(out)


def test_keep_headers_furniture_in_table_region(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    # three tall-narrow columns sharing a y-band seed a rotated-table
    # region; the kept page number falls inside it
    for x in (100, 180, 260):
        p.insert_textbox(
            fitz.Rect(x, 20, x + 40, 580),
            " ".join(["col word"] * 40),
            fontsize=BODY,
        )
    p.insert_text(fitz.Point(150, 45), "42", fontsize=BODY)
    pdf = tmp_path / "tblfurn.pdf"
    _save(doc, pdf)
    out = tmp_path / "tblfurn.epub"
    m.convert(pdf, out, drop_edges=False)
    # the table still rasterizes, but the kept page number must emit
    # verbatim rather than being consumed into the PNG
    assert 'alt="table"' in "".join(doc_html(out).values())
    assert "42" in chapter_text(out)


def test_keep_headers_furniture_not_blockquote_neighbor(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 30, 300, 48), "www.journal.org", fontsize=BODY
    )
    # indented long block — isolated enough to look quotable only if the
    # kept header counts as its neighbor
    p.insert_textbox(
        fitz.Rect(100, 200, 540, 260),
        " ".join(["An indented remark that reads like a quote."] * 3),
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 420, 540, 500),
        " ".join(["Normal body text resumes here."] * 8),
        fontsize=BODY,
    )
    pdf = tmp_path / "bqfurn.pdf"
    _save(doc, pdf)
    out = tmp_path / "bqfurn.epub"
    m.convert(pdf, out, drop_edges=False)
    html = "".join(doc_html(out).values())
    assert "<blockquote>" not in html
    assert "www.journal.org" in chapter_text(out)


def test_keep_headers_furniture_not_a_follower(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 300),
        " ".join(["Reference entries fill the column above."] * 10),
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 340, 540, 380),
        "Section tail remark",
        fontsize=BODY,
        fontname="hebo",
    )
    # the bold line's only follower is a kept margin strip (below the 60%
    # footnote-gap line, no note opener) — furniture can't qualify it
    p.insert_text(
        fitz.Point(585, 520), "JOURNAL SIDEBAR", fontsize=9, rotate=90
    )
    pdf = tmp_path / "furnfollower.pdf"
    _save(doc, pdf)
    out = tmp_path / "furnfollower.epub"
    m.convert(pdf, out, drop_edges=False)
    htmls = doc_html(out)
    assert not any("<h2" in h and "Section tail" in h for h in htmls.values())
    text = chapter_text(out)
    assert "Section tail remark" in text
    assert "JOURNAL SIDEBAR" in text


def test_heading_becomes_h1_and_toc(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 60, 540, 100), "A Synthetic Study", fontsize=18)
    p.insert_textbox(
        fitz.Rect(72, 120, 540, 300),
        "Body paragraph one. Body paragraph continues on.",
        fontsize=BODY,
    )
    pdf = tmp_path / "heading.pdf"
    _save(doc, pdf)
    out = tmp_path / "heading.epub"
    stats = m.convert(pdf, out)
    assert int(stats["headings"]) >= 1
    snap = snapshot_epub(out)
    texts = [t for _lv, t in snap["toc"]]
    assert any("Synthetic Study" in t for t in texts)
    htmls = doc_html(out)
    assert any("<h1" in h and "Synthetic Study" in h for h in htmls.values())


def test_bold_body_size_line_becomes_h2(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 200),
        "Some body text comes first here.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 250, 540, 280), "Bold Section", fontsize=BODY, fontname="hebo"
    )
    p.insert_textbox(
        fitz.Rect(72, 320, 540, 420),
        "Body text resumes under the heading.",
        fontsize=BODY,
    )
    pdf = tmp_path / "boldh2.pdf"
    _save(doc, pdf)
    out = tmp_path / "boldh2.epub"
    m.convert(pdf, out)
    htmls = doc_html(out)
    assert any("<h2" in h and "Bold Section" in h for h in htmls.values())


def test_italic_font_marks_up(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        "A line of italics for emphasis.",
        fontsize=BODY,
        fontname="heit",
    )
    pdf = tmp_path / "italic.pdf"
    _save(doc, pdf)
    out = tmp_path / "italic.epub"
    m.convert(pdf, out)
    htmls = doc_html(out)
    assert any("<i>" in h and "italics" in h for h in htmls.values())


def test_unnumbered_note_moves_to_notes(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 400),
        " ".join(["Body paragraph text fills the upper page."] * 8),
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 650, 540, 720),
        "Requests for reprints should be addressed to the author.",
        fontsize=8.5,
    )
    pdf = tmp_path / "note.pdf"
    _save(doc, pdf)
    out = tmp_path / "note.epub"
    m.convert(pdf, out)
    texts = doc_texts(out)
    assert "notes.xhtml" in texts
    assert "reprints" in texts["notes.xhtml"]
    assert not any(
        "reprints" in t for n, t in texts.items() if n.startswith("chap_")
    )


def test_tall_narrow_region_rasterized(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200), "A caption above the rotated table.", fontsize=BODY
    )
    for i, x in enumerate((200, 260, 320)):
        p.insert_textbox(
            fitz.Rect(x, 400, x + 15, 520),
            f"r1c{i} r2c{i} r3c{i}",
            fontsize=BODY,
        )
    p.insert_textbox(
        fitz.Rect(72, 560, 540, 700), "Body text resumes below the table.", fontsize=BODY
    )
    pdf = tmp_path / "rtable.pdf"
    _save(doc, pdf)
    out = tmp_path / "rtable.epub"
    m.convert(pdf, out)
    with zipfile.ZipFile(out) as zf:
        tables = [n for n in zf.namelist() if "table_" in n]
    assert tables, "expected a rasterized table image"
    assert "r1c0" not in chapter_text(out)


@pytest.mark.parametrize("rotation", [90, 270])
def test_rotated_table_clip_on_rotated_page(tmp_path, rotation):
    """On a /Rotate page the raster must come from the region the table
    actually occupies in displayed space.

    The red patch is painted where the correct clip (mediabox rect pushed
    through ``page.rotation_matrix``) lands but the transposed 90/270 map
    does not — under the old swapped transform the PNG contains no red.
    """
    doc = fitz.open()
    p = _page(doc)
    p.set_rotation(rotation)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200), "A caption above the rotated table.",
        fontsize=BODY,
    )
    for i, x in enumerate((200, 260, 320)):
        p.insert_textbox(
            fitz.Rect(x, 400, x + 15, 520), f"r1c{i} r2c{i} r3c{i}",
            fontsize=BODY,
        )
    p.draw_rect(fitz.Rect(200, 430, 330, 500), color=None, fill=(1, 0, 0))
    p.insert_textbox(
        fitz.Rect(72, 560, 540, 700), "Body text resumes below the table.",
        fontsize=BODY,
    )
    pdf = tmp_path / "rtable_rot.pdf"
    _save(doc, pdf)
    out = tmp_path / "rtable_rot.epub"
    m.convert(pdf, out)
    with zipfile.ZipFile(out) as zf:
        tables = [n for n in zf.namelist() if "table_" in n]
        assert tables, "expected a rasterized table image"
        pix = fitz.Pixmap(zf.read(tables[0]))
    reds = sum(
        1
        for y in range(0, pix.height, 2)
        for x in range(0, pix.width, 2)
        if pix.pixel(x, y)[0] > 200
        and pix.pixel(x, y)[1] < 80
        and pix.pixel(x, y)[2] < 80
    )
    assert reds > 0, "table image misses the painted region (wrong clip)"


def test_paragraph_merges_across_page_break(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 700, 540, 770),
        "The paragraph begins near the page bottom and the sentence",
        fontsize=BODY,
    )
    p2 = _page(doc)
    p2.insert_textbox(
        fitz.Rect(72, 60, 540, 160),
        "continues at the top of the following page.",
        fontsize=BODY,
    )
    pdf = tmp_path / "merge.pdf"
    _save(doc, pdf)
    out = tmp_path / "merge.epub"
    m.convert(pdf, out)
    text = chapter_text(out)
    assert "page bottom and the sentence continues at the top" in text
