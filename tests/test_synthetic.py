"""Synthetic-PDF integration tests: pages are built with PyMuPDF at
controlled positions/sizes, so assertions hold without any corpus files."""
import zipfile

import fitz
import pytest
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out)
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out, drop_edges=False)
    # the table still rasterizes, but the kept page number must emit
    # verbatim rather than being consumed into the PNG
    assert 'alt="table"' in "".join(doc_html(out).values())
    assert "42" in chapter_text(out)


def test_keep_headers_carried_paragraph_not_split(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 30, 300, 48), "www.journal.org", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 200, 540, 600),
        " ".join(["This sentence runs to the page break without ending"] * 6),
        fontsize=BODY,
    )
    p2 = _page(doc)
    p2.insert_textbox(
        fitz.Rect(72, 30, 300, 48), "www.journal.org", fontsize=BODY
    )
    p2.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        "here on page two the sentence concludes.",
        fontsize=BODY,
    )
    pdf = tmp_path / "carried.pdf"
    _save(doc, pdf)
    out = tmp_path / "carried.epub"
    convert(pdf, out, drop_edges=False)
    # the kept head must not split the paragraph carried across the page:
    # the continuation lands inside the same <p> as the page-1 opening
    html = "".join(
        h for n, h in doc_html(out).items() if n.startswith("chap_")
    )
    i = html.index("without ending")
    seg = html[i : i + 600]
    assert "here on page two" in seg.split("</p>")[0]


def test_signature_furniture_no_edge_vote(tmp_path):
    doc = fitz.open()
    for _ in range(6):
        p = _page(doc)
        # repeated head+footer at a fixed x0 — signature furniture that
        # must not vote for column edges
        p.insert_textbox(
            fitz.Rect(290, 40, 430, 65), "Journal of Examples", fontsize=9
        )
        p.insert_textbox(
            fitz.Rect(290, 740, 460, 765), "Journal of Examples", fontsize=9
        )
        p.insert_textbox(
            fitz.Rect(72, 150, 540, 200), "Alpha text fills column.", fontsize=BODY
        )
        p.insert_textbox(
            fitz.Rect(310, 250, 540, 300), "Indent text sits inset.", fontsize=BODY
        )
        p.insert_textbox(
            fitz.Rect(72, 350, 540, 400), "Beta text closes column.", fontsize=BODY
        )
    pdf = tmp_path / "sigedge.pdf"
    _save(doc, pdf)
    out = tmp_path / "sigedge.epub"
    convert(pdf, out)
    text = chapter_text(out)
    # single column: indented block must not jump behind all col-0 text
    assert text.index("Alpha") < text.index("Indent") < text.index("Beta")


def test_demoted_block_size_signaled_line_still_emits(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # all-bold mixed-size trailing block: the block-level demotion
    # silences the bold-band lines, but the 14pt line carries its own
    # size signal (≥1.3×) and still emits (median stays <1.15× so the
    # block survives byline absorption)
    p.insert_text(
        fitz.Point(72, 510), "Small tail", fontsize=10.6, fontname="hebo"
    )
    p.insert_text(
        fitz.Point(72, 524), "Small tail two", fontsize=10.6, fontname="hebo"
    )
    p.insert_text(
        fitz.Point(72, 542), "Big tail", fontsize=14, fontname="hebo"
    )
    pdf = tmp_path / "sizetail.pdf"
    _save(doc, pdf)
    out = tmp_path / "sizetail.epub"
    convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2>Small tail</h2>" in h for h in chaps)
    assert any(">Big tail</h" in h for h in chaps)


def test_bold_tail_heading_follower_demoted(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        " ".join(["Body text fills the column above."] * 10),
        fontsize=BODY,
    )
    # a bold tail followed only by a real heading still has no *body*
    # follower — the heading doesn't count
    p.insert_textbox(
        fitz.Rect(72, 520, 300, 540),
        "Tail remark",
        fontsize=10.6,
        fontname="hebo",
    )
    p.insert_textbox(
        fitz.Rect(72, 560, 540, 585),
        "Discussion",
        fontsize=14,
        fontname="hebo",
    )
    p.insert_textbox(
        fitz.Rect(72, 600, 540, 700),
        " ".join(["Discussion body text."] * 8),
        fontsize=BODY,
    )
    pdf = tmp_path / "headtail.pdf"
    _save(doc, pdf)
    out = tmp_path / "headtail.epub"
    convert(pdf, out)
    chaps = [h for n, h in doc_html(out).items() if n.startswith("chap_")]
    assert not any("<h2>Tail remark</h2>" in h for h in chaps)
    assert any(">Discussion</h" in h for h in chaps)


def test_keep_headers_head_no_degenerate_chapter(tmp_path):
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 30, 300, 48), "www.journal.org", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 120, 540, 160), "A Paper Title", fontsize=17,
        fontname="hebo",
    )
    p.insert_textbox(
        fitz.Rect(72, 200, 540, 400),
        " ".join(["Body text fills the column."] * 10),
        fontsize=BODY,
    )
    pdf = tmp_path / "headchap.pdf"
    _save(doc, pdf)
    out = tmp_path / "headchap.epub"
    convert(pdf, out, drop_edges=False)
    chaps = {
        n: h for n, h in doc_html(out).items() if n.startswith("chap_")
    }
    # the kept head folds into the title chapter rather than forming a
    # degenerate "Section 1" containing only the furniture paragraph
    assert len(chaps) == 1
    chap = next(iter(chaps.values()))
    assert "www.journal.org" in chap and "A Paper Title" in chap


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
    convert(pdf, out, drop_edges=False)
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
    convert(pdf, out, drop_edges=False)
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


def test_rotated_table_alt_from_caption_above(tmp_path):
    """Journals set 'Table N.' legends above the table body — a caption
    preceding the figure in reading order must still be found."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 300, 540, 340), "Table 3. Legend above.", fontsize=BODY
    )
    for i, x in enumerate((180, 220, 260, 300, 340)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 520), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    p.insert_textbox(
        fitz.Rect(72, 560, 540, 620), "Body text resumes below.", fontsize=BODY
    )
    pdf = tmp_path / "rtableabove.pdf"
    _save(doc, pdf)
    out = tmp_path / "rtableabove.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 3. Legend above."' in h for h in htmls.values())


def test_table_alt_skips_above_region_foreign_caption(tmp_path):
    """Column-major order emits a later column's top-of-column blocks
    after the member cells — a caption lying wholly above the region
    must be skipped, not adopted."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    # cells on both sides of the gutter: the region covers both bands
    for i, x in enumerate((200, 300, 400)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 500), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    # right column: a figure caption at the top (~290pt above the region)…
    p.insert_textbox(
        fitz.Rect(340, 90, 560, 130), "Figure 4. A foreign caption.", fontsize=BODY
    )
    # …and the table's own legend below the region's bottom (~60pt)
    p.insert_textbox(
        fitz.Rect(340, 560, 560, 600), "Table 9. Real results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 640, 560, 720), "Right column body text.", fontsize=BODY
    )
    pdf = tmp_path / "spantheft.pdf"
    _save(doc, pdf)
    out = tmp_path / "spantheft.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 9. Real results."' in h for h in htmls.values())
    assert not any('alt="Figure 4' in h for h in htmls.values())


def test_table_alt_skips_beside_region_caption(tmp_path):
    """A caption block beside the region — inside its y-band in a covered
    later column — is not a candidate: candidates must start beyond the
    facing edge."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    # cells on both sides of the gutter: the region covers both bands
    for i, x in enumerate((200, 300, 400)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 500), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    # right column: a figure caption *inside* the region's y-band but
    # outside its x-range — beside the table, not its legend
    p.insert_textbox(
        fitz.Rect(430, 420, 560, 460), "Figure 4. A foreign caption.", fontsize=BODY
    )
    # the table's own legend below the region's bottom edge (~90pt)
    p.insert_textbox(
        fitz.Rect(340, 560, 560, 600), "Table 9. Real results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 640, 560, 720), "Right column body text.", fontsize=BODY
    )
    pdf = tmp_path / "besidetheft.pdf"
    _save(doc, pdf)
    out = tmp_path / "besidetheft.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 9. Real results."' in h for h in htmls.values())
    assert not any('alt="Figure 4' in h for h in htmls.values())


def test_table_alt_ignores_figure_caption_below(tmp_path):
    """A 'Figure N.' block below a rasterized table belongs to a following
    figure — the table call's below-scan accepts table labels only."""
    doc = fitz.open()
    p = _page(doc)
    for i, x in enumerate((180, 220, 260, 300, 340)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 520), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    p.insert_textbox(
        fitz.Rect(72, 545, 540, 585), "Figure 4. Not a table legend.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 620, 540, 700), "Body text resumes below.", fontsize=BODY
    )
    pdf = tmp_path / "tfigcap.pdf"
    _save(doc, pdf)
    out = tmp_path / "tfigcap.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="table"' in figs and 'alt="Figure 4' not in figs


def test_table_alt_finds_later_column_legend_above(tmp_path):
    """A legend seated in a later column above the region emits after the
    first member cell in column-major order — the forward scan must still
    rescue it rather than fall back to 'table'."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    # cells on both sides of the gutter: the region covers both bands;
    # none may cross the edge or the legend would hoist into top_zone
    for i, x in enumerate((200, 260, 380, 420)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 500), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    # the legend sits at the top of the right column, above the region —
    # column-major order emits it only after col 0's member cells
    p.insert_textbox(
        fitz.Rect(340, 300, 560, 340), "Table 9. Late-column legend.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 560, 560, 720), "Right column body text.", fontsize=BODY
    )
    pdf = tmp_path / "lateleg.pdf"
    _save(doc, pdf)
    out = tmp_path / "lateleg.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 9. Late-column legend."' in h for h in htmls.values())


def test_table_alt_ignores_later_column_figure_above(tmp_path):
    """A 'Figure N.' seated above the region in a later column is that
    column's own figure caption — the stray rescue must not adopt it
    ahead of the table's 'Table N.' legend below."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 180, 290, 260), "More left column body text.", fontsize=BODY
    )
    # cells stay clear of the caption's x0 so no phantom edge forms
    for i, x in enumerate((200, 260, 340, 380)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 500), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    # foreign figure caption: later column, above the region's y-band
    p.insert_textbox(
        fitz.Rect(430, 300, 560, 340), "Figure 4. A foreign caption.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 560, 560, 600), "Table 9. Real results.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 620, 560, 720), "Right column body text.", fontsize=BODY
    )
    pdf = tmp_path / "latefig.pdf"
    _save(doc, pdf)
    out = tmp_path / "latefig.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Table 9. Real results."' in figs
    assert 'alt="Figure 4.' not in figs


def test_table_alt_does_not_reclaim_figure_caption(tmp_path):
    """A captioned figure directly above a rotated table: the 'Figure N.'
    block is the figure's own caption — the table must not adopt it a
    second time via the above scan."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_image(fitz.Rect(72, 150, 300, 250), stream=_noise_png())
    p.insert_textbox(
        fitz.Rect(72, 265, 560, 290), "Figure 4. A panel.", fontsize=BODY
    )
    for i, x in enumerate((200, 260, 380)):
        p.insert_textbox(
            fitz.Rect(x, 320, x + 15, 435), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    p.insert_textbox(
        fitz.Rect(72, 500, 300, 560), "Body text resumes below.", fontsize=BODY
    )
    pdf = tmp_path / "figtbl.pdf"
    _save(doc, pdf)
    out = tmp_path / "figtbl.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Figure 4. A panel."' in figs
    assert 'alt="table"' in figs
    assert figs.count('alt="Figure 4.') == 1


def test_table_alt_finds_spanning_legend_below(tmp_path):
    """A legend below the region that spans the gutter emits into every
    covered column's sequence, so the forward scan reaches it even when
    all member cells live in the later column."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 90, 290, 160), "Left column opens the page.", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(340, 150, 560, 250), "Right column opens too.", fontsize=BODY
    )
    # all cells in col 1 — a col-0-homed legend below would correctly
    # fail the shared-band check, but a gutter-spanning legend is fair
    for i, x in enumerate((380, 420, 460)):
        p.insert_textbox(
            fitz.Rect(x, 380, x + 15, 500), f"r1c{i} r2c{i} r3c{i}", fontsize=BODY
        )
    # long enough that the rendered text physically crosses the gutter
    p.insert_textbox(
        fitz.Rect(72, 560, 560, 600),
        "Table 5. Legend with a genuinely long line that crosses the gutter.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(340, 620, 560, 720), "Right column body text.", fontsize=BODY
    )
    pdf = tmp_path / "spanleg.pdf"
    _save(doc, pdf)
    out = tmp_path / "spanleg.epub"
    convert(pdf, out)
    htmls = doc_html(out)
    assert any('alt="Table 5. Legend' in h for h in htmls.values())


def test_figure_alt_accepts_subfigure_and_dash_labels(tmp_path):
    """Journal caption openers with a subfigure letter, a parenthesized
    panel, or a dash-set legend still count as captions."""
    # Base-14 fonts can't encode em/en dashes, so the dash-set case uses
    # a plain hyphen — the regex accepts the unicode dashes too
    for k, cap in enumerate(
        ("Fig. 2b", "Figure 1(a)", "Figure 3 - Wide panel.")
    ):
        doc = fitz.open()
        p = _page(doc)
        p.insert_image(fitz.Rect(72, 150, 300, 250), stream=_noise_png())
        p.insert_textbox(fitz.Rect(72, 265, 560, 290), cap, fontsize=BODY)
        pdf = tmp_path / f"cap{k}.pdf"
        _save(doc, pdf)
        out = tmp_path / f"cap{k}.epub"
        convert(pdf, out)
        htmls = doc_html(out)
        assert any(f'alt="{cap}"' in h for h in htmls.values()), cap


def test_figure_alt_never_borrows_table_caption(tmp_path):
    """A 'Table N.' block after an image belongs to a text-flowed table
    (legend above), not to the figure — images match figure labels only."""
    data = _noise_png()
    doc = fitz.open()
    p = _page(doc)
    p.insert_image(fitz.Rect(80, 300, 260, 420), stream=data)
    p.insert_textbox(
        fitz.Rect(72, 440, 540, 480), "Table 4. Paired results.", fontsize=BODY
    )
    pdf = tmp_path / "figtable.pdf"
    _save(doc, pdf)
    out = tmp_path / "figtable.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Table 4' not in figs and 'alt=""' in figs


def test_figure_alt_ignores_prose_citation(tmp_path):
    """'Figure N shows…' is a citation, not a caption — the digits must be
    followed by '.', ':', ')' or end-of-text."""
    data = _noise_png()
    doc = fitz.open()
    p = _page(doc)
    p.insert_image(fitz.Rect(80, 300, 260, 420), stream=data)
    p.insert_textbox(
        fitz.Rect(72, 440, 540, 500),
        "Figure 9 shows the outcome clearly.",
        fontsize=BODY,
    )
    pdf = tmp_path / "prosecap.pdf"
    _save(doc, pdf)
    out = tmp_path / "prosecap.epub"
    convert(pdf, out)
    figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
    assert 'alt="Figure 9' not in figs and 'alt=""' in figs


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


def test_headingless_doc_still_gets_a_toc_entry(tmp_path):
    """No headings and no notes must not leave an empty nav <ol/> or
    navMap — one entry is seeded pointing at the first chapter."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 100, 540, 300),
        "Plain body text with no headings or notes at all.",
        fontsize=BODY,
    )
    pdf = tmp_path / "plain.pdf"
    _save(doc, pdf)
    out = tmp_path / "plain.epub"
    convert(pdf, out)
    nav = doc_html(out)["nav.xhtml"]
    assert "<ol/>" not in nav and "<a " in nav
    with zipfile.ZipFile(out) as zf:
        ncx = next(n for n in zf.namelist() if n.endswith(".ncx"))
        assert "<navPoint" in zf.read(ncx).decode("utf8")


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
    convert(pdf, out)
    assert lint_epub(out) == []
    notes = doc_html(out)["notes.xhtml"]
    assert "#fn2010" not in notes
    assert "<sup>2010</sup>" in notes


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
    convert(pdf, out)
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
    convert(pdf, out)
    text = chapter_text(out)
    assert "page bottom and the sentence continues at the top" in text
