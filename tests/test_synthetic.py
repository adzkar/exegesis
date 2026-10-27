"""Synthetic-PDF integration tests: pages are built with PyMuPDF at
controlled positions/sizes, so assertions hold without any corpus files."""
import zipfile

import fitz
from helpers import chapter_text, doc_html, doc_texts, lint_epub, snapshot_epub

from exegesis import convert

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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out)
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
    stats = convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
    with zipfile.ZipFile(out) as zf:
        tables = [n for n in zf.namelist() if "table_" in n]
    assert tables, "expected a rasterized table image"
    assert "r1c0" not in chapter_text(out)


def test_rotated_table_alt_from_caption(tmp_path):
    """A caption below a wide rotated table is found no matter how many
    cell blocks intervene — the alt scan steps over member cells."""
    doc = fitz.open()
    p = _page(doc)
    for i, x in enumerate((180, 220, 260, 300, 340)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 520), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    p.insert_textbox(
        fitz.Rect(72, 545, 540, 585), "Table 1. Landscape results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 620, 540, 700), "Body text resumes below.", fontsize=BODY
    )
    pdf = tmp_path / "rtablecap.pdf"
    _save(doc, pdf)
    out = tmp_path / "rtablecap.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 1. Landscape results."' in h for h in htmls.values())


def test_rotated_table_alt_gap_from_region_bottom(tmp_path):
    """The caption gap measures from the table region's bottom, not the
    first cell's — a short leading cell must not push the caption away."""
    doc = fitz.open()
    p = _page(doc)
    # earliest cell in reading order (min y0, min x0) renders to y1~=428…
    p.insert_textbox(
        fitz.Rect(180, 340, 195, 470), "r1c0 r2c0 r3c0", fontsize=BODY
    )
    # …while the rest of the region renders down to y1~=497
    for i, x in enumerate((220, 260, 300, 340)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 620),
            f"r1c{i} r2c{i} r3c{i} r4c{i}",
            fontsize=BODY,
        )
    # caption top is ~140pt below the first cell's bottom but only ~73pt
    # below the region's true bottom
    p.insert_textbox(
        fitz.Rect(72, 570, 540, 610), "Table 2. Deep landscape.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 640, 540, 720), "Body text resumes below.", fontsize=BODY
    )
    pdf = tmp_path / "rtabledeep.pdf"
    _save(doc, pdf)
    out = tmp_path / "rtabledeep.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 2. Deep landscape."' in h for h in htmls.values())


def test_nested_toc_h2_under_h1(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 60, 540, 100), "The Paper Title", fontsize=18)
    p.insert_textbox(fitz.Rect(72, 140, 540, 170), "First Section", fontsize=13)
    p.insert_textbox(
        fitz.Rect(72, 200, 540, 400), "Body text under the section.", fontsize=BODY
    )
    pdf = tmp_path / "nestedtoc.pdf"
    _save(doc, pdf)
    out = tmp_path / "nestedtoc.epub"
    convert(pdf, out)
    snap = snapshot_epub(out)
    texts = [t for _lv, t in snap["toc"]]
    levels = [lv for lv, _t in snap["toc"]]
    assert "The Paper Title" in texts and "First Section" in texts
    assert levels[texts.index("First Section")] == 2


def test_childless_h1_toc_has_no_empty_ol(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 60, 540, 100), "Title With No Sections", fontsize=18
    )
    p.insert_textbox(
        fitz.Rect(72, 140, 540, 300), "Body text, no headings.", fontsize=BODY
    )
    pdf = tmp_path / "titleonly.pdf"
    _save(doc, pdf)
    out = tmp_path / "titleonly.epub"
    convert(pdf, out)
    # a childless h1 must be a bare <li><a/></li> — epubcheck rejects <ol/>
    nav = doc_html(out)["nav.xhtml"]
    assert "<ol/>" not in nav and "<ol></ol>" not in nav
    snap = snapshot_epub(out)
    assert any("Title With No Sections" in t for _lv, t in snap["toc"])


def _noise_png(size=120):
    """A noisy pixmap whose PNG encoding exceeds MIN_IMAGE_BYTES."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size, size))
    for x in range(size):
        for y in range(size):
            pix.set_pixel(x, y, ((x * y) % 256, (x * 3 + y) % 256, (x + y * 7) % 256))
    return pix.tobytes("png")


def test_figure_alt_from_caption(tmp_path):
    data = _noise_png()
    assert len(data) > 2048, "noise fixture must clear MIN_IMAGE_BYTES"
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 160), "The findings are shown below.", fontsize=BODY
    )
    p.insert_image(fitz.Rect(200, 200, 400, 400), stream=data)
    p.insert_textbox(
        fitz.Rect(72, 420, 540, 470), "Figure 1. A noise gradient.", fontsize=BODY
    )
    pdf = tmp_path / "figcap.pdf"
    _save(doc, pdf)
    out = tmp_path / "figcap.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Figure 1. A noise gradient."' in h for h in htmls.values())


def test_figure_alt_never_from_a_foreign_caption(tmp_path):
    """Neither an intervening paragraph's later caption nor the adjacent
    column's 'Table N.' caption may be borrowed as alt text."""
    data = _noise_png()
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    # mid-column image: a paragraph then a caption inside its own column —
    # the caption belongs to a later figure, not this one
    p.insert_image(fitz.Rect(80, 380, 260, 470), stream=data)
    p.insert_textbox(
        fitz.Rect(72, 485, 290, 540), "Body resumes below the image.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 545, 290, 575), "Figure 7. Not this image.", fontsize=BODY
    )
    # column-ending image: the next text in reading order is the right
    # column's 'Table 3.' caption — a different column's caption entirely
    p.insert_image(fitz.Rect(80, 600, 260, 690), stream=data)
    p.insert_textbox(
        fitz.Rect(340, 90, 560, 130), "Table 3. Foreign results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 160, 560, 240),
        "Right column body text follows.",
        fontsize=BODY,
    )
    pdf = tmp_path / "foreigncap.pdf"
    _save(doc, pdf)
    out = tmp_path / "foreigncap.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Figure 7' not in figs and 'alt="Table 3' not in figs
    assert figs.count('alt=""') == 2


def test_figure_alt_ignores_gutter_bleed_caption(tmp_path):
    """A foreign caption bleeding a few points across the column edge must
    not satisfy the shared-column check — jitter can't claim both bands."""
    data = _noise_png()
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    p.insert_image(fitz.Rect(80, 400, 260, 520), stream=data)
    # right-column caption starting 5pt left of the gutter edge: any-pixel
    # coverage counts both columns, genuine occupancy counts only its home
    p.insert_textbox(
        fitz.Rect(335, 90, 560, 130), "Table 3. Foreign results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 160, 560, 240),
        "Right column body text follows.",
        fontsize=BODY,
    )
    pdf = tmp_path / "bleedcap.pdf"
    _save(doc, pdf)
    out = tmp_path / "bleedcap.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Table 3' not in figs and 'alt=""' in figs


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
    convert(pdf, out)
    text = chapter_text(out)
    assert "page bottom and the sentence continues at the top" in text
