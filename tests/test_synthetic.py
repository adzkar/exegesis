"""Synthetic-PDF integration tests: pages are built with PyMuPDF at
controlled positions/sizes, so assertions hold without any corpus files."""
import zipfile

import fitz
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
        p.insert_textbox(fitz.Rect(72, 80, 290, 200), left_a, fontsize=BODY)
        p.insert_textbox(fitz.Rect(72, 320, 290, 440), left_b, fontsize=BODY)
        p.insert_textbox(fitz.Rect(340, 80, 560, 200), right_a, fontsize=BODY)
        p.insert_textbox(fitz.Rect(340, 320, 560, 440), right_b, fontsize=BODY)
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
    # furniture is retained verbatim — the footer may land in notes.xhtml
    # because footnote-zone detection is independent of header stripping
    text = " ".join(doc_texts(out).values())
    assert "journal.org" in text
    assert "VOL 340" in text
    assert "7" in text


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


def test_note_interior_sup_link_validated(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 400),
        " ".join(["Body paragraph text fills the upper page."] * 8),
        fontsize=BODY,
    )
    # one note-zone block, two lines: the second is a raised 4-digit token
    # (e.g. a superscripted year) with no matching note — it must not emit
    # a dead #fn2010 anchor inside notes.xhtml
    p.insert_textbox(
        fitz.Rect(72, 650, 200, 665), "Supported by grant", fontsize=8.5
    )
    p.insert_text((176, 660), "2010", fontsize=6)
    pdf = tmp_path / "notesup.pdf"
    _save(doc, pdf)
    out = tmp_path / "notesup.epub"
    m.convert(pdf, out)
    assert lint_epub(out) == []
    notes = doc_html(out)["notes.xhtml"]
    assert "#fn2010" not in notes
    assert "<sup>2010</sup>" in notes


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
