"""Independent extraction, EPUB validity, and owned furniture-failure baselines."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import fitz
import pytest
from builders import BODY, H, W
from furniture_fixtures import (
    AXIS_LABEL,
    BOTTOM_BODY,
    BOTTOM_COPY_TAGS,
    BOTTOM_FOOT,
    FUSED_HEAD_LINES,
    FUSED_TITLE,
    FUSED_TITLE_LINES,
    INFO_BODY_PROSE,
    INFO_FIELDS,
    INFO_PARAGRAPH,
    INFO_PREFIX,
    INFO_SUFFIX,
    INFO_TITLE,
    INLINE_RECEIVED_PROSE,
    LONG_HEAD_LINES,
    NORMAL_MARGIN_LINES,
    NORMAL_MARGIN_TEXT,
    OVER_BOUND_BODY,
    OVER_CHAR_HEAD,
    OVER_LINE_HEAD,
    ROTATED_PARAGRAPH,
    ROTATED_PREFIX,
    ROTATED_STAMP,
    ROTATED_SUFFIX,
    TABLE_BODY,
    TABLE_CELLS,
    UPRIGHT_BODY,
    UPRIGHT_EDGE_LINE,
    UPRIGHT_EDGE_LINES,
    UPRIGHT_TAGS,
    front_matter_info_box,
    fused_running_head_title,
    long_repeated_running_head,
    over_bound_running_head,
    repeated_bottom_foot,
    rotated_margin_stamp,
    rotated_page_upright_margin,
    rotated_table_margin_cells,
)
from helpers import chapter_body_text, doc_html, lint_epub
from layout_assertions import (
    SemanticMismatch,
    assert_retained_once,
    require,
    require_absent,
)

from exegesis import convert
from exegesis.analyze import (
    _body_font_size,
    _boilerplate_signatures,
    _doc_column_edges,
    _is_furniture,
    _is_margin_strip,
    _is_signature_furniture,
    _rotated_table_region,
    _signature,
    _table_regions,
)
from exegesis.order import _ordered_blocks
from exegesis.text import _block_text, _line_text


@dataclass
class FurnitureCase:
    """Share one generated PDF and conversion without marking operational errors expected."""

    pdf: Path
    epub: Path
    stats: dict
    text: str
    paragraphs: tuple[str, ...]
    h1s: tuple[str, ...]
    bylines: tuple[str, ...]


def _converted_case(pdf_path, drop_edges=True):
    """Convert an existing source PDF and parse XHTML outside semantic assertions."""
    epub = pdf_path.with_suffix(".epub")
    stats = convert(pdf_path, epub, drop_edges=drop_edges)
    chapters = [
        ET.fromstring(html)
        for name, html in doc_html(epub).items()
        if name.startswith("chap_")
    ]
    paragraphs = tuple(
        " ".join("".join(element.itertext()).split())
        for chapter in chapters
        for element in chapter.iter("{http://www.w3.org/1999/xhtml}p")
    )
    h1s = tuple(
        " ".join("".join(element.itertext()).split())
        for chapter in chapters
        for element in chapter.iter("{http://www.w3.org/1999/xhtml}h1")
    )
    bylines = tuple(
        " ".join("".join(element.itertext()).split())
        for chapter in chapters
        for element in chapter.iter("{http://www.w3.org/1999/xhtml}p")
        if element.get("class") == "byline"
    )
    return FurnitureCase(
        pdf_path, epub, stats, " ".join(chapter_body_text(epub).split()),
        paragraphs, h1s, bylines,
    )


@pytest.fixture(scope="module")
def rotated_pdf(tmp_path_factory):
    """Generate the stamp source once without requiring a successful conversion."""
    directory = tmp_path_factory.mktemp("rotated_margin_stamp")
    return rotated_margin_stamp(directory / "input.pdf")


@pytest.fixture(scope="module")
def long_head_pdf(tmp_path_factory):
    """Generate the repeated-head source independently of EPUB setup."""
    directory = tmp_path_factory.mktemp("long_repeated_running_head")
    return long_repeated_running_head(directory / "input.pdf")


@pytest.fixture(scope="module")
def fused_pdf(tmp_path_factory):
    """Generate the fused-head source so its raw contract never invokes conversion."""
    directory = tmp_path_factory.mktemp("fused_running_head_title")
    return fused_running_head_title(directory / "input.pdf")


@pytest.fixture(scope="module")
def info_box_pdf(tmp_path_factory):
    """Generate the two-field source before and independently of conversion."""
    directory = tmp_path_factory.mktemp("front_matter_info_box")
    return front_matter_info_box(directory / "input.pdf")


@pytest.fixture(scope="module")
def rotated_case(rotated_pdf):
    """Convert the stamp once for separate lint and semantic gates."""
    case = _converted_case(rotated_pdf)
    assert_retained_once(case.text, "A margin stamp threshold control", NORMAL_MARGIN_TEXT,
                         *ROTATED_PREFIX[:2], *ROTATED_SUFFIX)
    assert_retained_once(case.text, "before the calculation identifies", "table that closes the central argument")
    return case


@pytest.fixture(scope="module")
def long_head_case(long_head_pdf):
    """Reuse one long-head conversion while keeping lint independent of its xfail."""
    case = _converted_case(long_head_pdf)
    assert_retained_once(case.text, "Running head threshold control", *(
        f"Long-head body page {number} {part}" for number in range(1, 4)
        for part in ("opens", "closes")
    ))
    return case


@pytest.fixture(scope="module")
def fused_case(fused_pdf):
    """Convert the already-generated fused source once for output assertions."""
    case = _converted_case(fused_pdf)
    assert_retained_once(case.text, FUSED_TITLE, *(
        f"Fused-head body page {number} {part}" for number in range(1, 4)
        for part in ("retains", "preserves")
    ))
    return case


@pytest.fixture(scope="module")
def info_box_case(info_box_pdf):
    """Convert the two-field box once before any expected semantic assertion runs."""
    case = _converted_case(info_box_pdf)
    assert_retained_once(case.text, INFO_TITLE, *INFO_FIELDS, *INFO_PREFIX, *INFO_SUFFIX,
                         INFO_BODY_PROSE, *(
        f"Info-box body page {number} {part}" for number in range(2, 5)
        for part in ("continues", "closes")
    ))
    assert case.text.count(INLINE_RECEIVED_PROSE) == 3
    return case


@pytest.fixture(scope="module")
def upright_pdf(tmp_path_factory):
    """Generate the rotated-pages source once without requiring a conversion."""
    directory = tmp_path_factory.mktemp("rotated_page_upright_margin")
    return rotated_page_upright_margin(directory / "input.pdf")


@pytest.fixture(scope="module")
def table_cells_pdf(tmp_path_factory):
    """Generate the margin-cells table source independently of conversion."""
    directory = tmp_path_factory.mktemp("rotated_table_margin_cells")
    return rotated_table_margin_cells(directory / "input.pdf")


@pytest.fixture(scope="module")
def over_bound_pdf(tmp_path_factory):
    """Generate the over-bound-heads source before any conversion runs."""
    directory = tmp_path_factory.mktemp("over_bound_running_head")
    return over_bound_running_head(directory / "input.pdf")


@pytest.fixture(scope="module")
def bottom_foot_pdf(tmp_path_factory):
    """Generate the repeated-bottom-foot source before any conversion runs."""
    directory = tmp_path_factory.mktemp("repeated_bottom_foot")
    return repeated_bottom_foot(directory / "input.pdf")


@pytest.fixture(scope="module")
def rotated_keep_case(rotated_pdf):
    """--keep-headers conversion of the stamp source: furniture stays inert."""
    case = _converted_case(rotated_pdf, drop_edges=False)
    assert_retained_once(case.text, "A margin stamp threshold control",
                         NORMAL_MARGIN_TEXT, *ROTATED_PREFIX[:2], *ROTATED_SUFFIX)
    return case


@pytest.fixture(scope="module")
def long_head_keep_case(long_head_pdf):
    """--keep-headers conversion of the repeated-head source."""
    case = _converted_case(long_head_pdf, drop_edges=False)
    assert_retained_once(case.text, "Running head threshold control", *(
        f"Long-head body page {number} {part}" for number in range(1, 4)
        for part in ("opens", "closes")
    ))
    return case


@pytest.fixture(scope="module")
def upright_case(upright_pdf):
    """Convert the rotated-pages source once for the semantic gates."""
    case = _converted_case(upright_pdf)
    assert_retained_once(case.text, *(
        f"{line} {rotation}" for rotation in (90, 270) for line in UPRIGHT_BODY
    ))
    return case


@pytest.fixture(scope="module")
def table_cells_case(table_cells_pdf):
    """Convert the margin-cells table source once; cells rasterize only."""
    case = _converted_case(table_cells_pdf)
    assert_retained_once(case.text, AXIS_LABEL, *TABLE_BODY)
    return case


@pytest.fixture(scope="module")
def table_cells_keep_case(table_cells_pdf):
    """--keep-headers conversion of the same table source."""
    case = _converted_case(table_cells_pdf, drop_edges=False)
    assert_retained_once(case.text, AXIS_LABEL, *TABLE_BODY)
    return case


@pytest.fixture(scope="module")
def over_bound_case(over_bound_pdf):
    """Convert the over-bound-heads source once."""
    case = _converted_case(over_bound_pdf)
    assert_retained_once(case.text, *(
        part.format(number) for number in (1, 2)
        for part in OVER_BOUND_BODY
    ))
    return case


@pytest.fixture(scope="module")
def bottom_foot_case(bottom_foot_pdf):
    """Convert the repeated-bottom-foot source once."""
    case = _converted_case(bottom_foot_pdf)
    assert_retained_once(case.text, "A Bottom Footer Study", *(
        part.format(number) for number in (1, 2, 3)
        for part in (BOTTOM_BODY[0], BOTTOM_BODY[3])
    ))
    return case


def _text_blocks(pdict):
    """Keep the raw extraction contract separate from reordered or rendered blocks."""
    return [block for block in pdict["blocks"] if block["type"] == 0]


def _containing(blocks, needle):
    """Require a unique raw block so accidental fusion cannot satisfy a shape contract."""
    matches = [block for block in blocks if needle in _block_text(block)]
    assert len(matches) == 1, (needle, [_block_text(block) for block in matches])
    return matches[0]


def _assert_no_tables(pages, edges):
    """Prove the fixture is a furniture/front-matter case rather than a table defect."""
    body_size, line_h = _body_font_size(pages)
    assert body_size == pytest.approx(BODY)
    for page, pdict in pages:
        assert all(block["type"] == 0 for block in pdict["blocks"])
        assert _rotated_table_region(pdict["blocks"]) is None
        assert _table_regions(pdict["blocks"], line_h, edges, page.rect.width) == []


def _assert_conversion(case, pages):
    """Make packaging and classifier-health assertions ordinary, never expected failures."""
    assert case.epub.is_file()
    assert case.stats["pages"] == pages
    assert case.stats["columns"] == 1
    assert case.stats["chapters"] >= 1
    assert case.stats["footnotes"] == 0
    assert case.stats["grid_tables"] == 0
    assert case.stats["raster_tables"] == 0
    assert case.stats["images"] == 0
    assert lint_epub(case.epub) == []


def test_rotated_margin_stamp_extraction_contract(rotated_pdf):
    """Pin the one rotated 27pt strip and distinct flanking paragraph blocks (#34)."""
    with fitz.open(rotated_pdf) as doc:
        assert len(doc) == 1
        page = doc[0]
        pdict = page.get_text("dict")
        blocks = _text_blocks(pdict)
        stamp = _containing(blocks, ROTATED_STAMP)
        prefix = _containing(blocks, ROTATED_PREFIX[0])
        suffix = _containing(blocks, ROTATED_SUFFIX[0])
        assert len({id(stamp), id(prefix), id(suffix)}) == 3
        assert [_line_text(line) for line in prefix["lines"]] == list(ROTATED_PREFIX)
        assert [_line_text(line) for line in suffix["lines"]] == list(ROTATED_SUFFIX)
        assert len(stamp["lines"]) == 1
        assert stamp["lines"][0]["dir"] == pytest.approx((0, -1))
        x0, y0, x1, y1 = stamp["bbox"]
        assert (x0, x1) == pytest.approx((11.43, 38.36), abs=0.05)
        assert x1 - x0 == pytest.approx(26.93, abs=0.05)
        assert 4 * (x1 - x0) < y1 - y0 < 380
        assert x1 < W * 0.1
        assert H * 0.2 < y0 < y1 < H * 0.9
        assert prefix["bbox"][3] < y0 < suffix["bbox"][1]
        assert prefix["bbox"][0] == suffix["bbox"][0] == 72
        assert sum(
            line["dir"] == (0, -1)
            for block in blocks for line in block["lines"]
        ) == 1
        edges = _doc_column_edges([(page, pdict)], _boilerplate_signatures([(page, pdict)]))
        assert edges == pytest.approx([72])
        _assert_no_tables([(page, pdict)], edges)


def test_rotated_margin_stamp_conversion_and_lint(rotated_case):
    """The stamp's documented semantic failure must not hide extraction or EPUB errors."""
    _assert_conversion(rotated_case, 1)
    assert ROTATED_PREFIX[0] in rotated_case.text
    assert ROTATED_SUFFIX[0] in rotated_case.text
    assert ROTATED_SUFFIX[-1] in rotated_case.text


def test_issue34_rotated_stamp_does_not_interrupt_paragraph(rotated_case):
    """The 27pt rotated arXiv stamp drops as furniture; the hyphenated
    paragraph around it rejoins intact."""
    require(
        ROTATED_STAMP not in rotated_case.text
        and ROTATED_PARAGRAPH in rotated_case.paragraphs,
        "#34: expected one intact 'character table' paragraph without the rotated stamp; "
        f"observed body {rotated_case.text!r}",
    )


def test_normal_horizontal_margin_prose_is_retained(rotated_case):
    """An ordinary narrow, upright margin block is article content, not a stamp."""
    with fitz.open(rotated_case.pdf) as doc:
        page = doc[0]
        margin = _containing(_text_blocks(page.get_text("dict")), NORMAL_MARGIN_TEXT)
        assert [_line_text(line) for line in margin["lines"]] == list(NORMAL_MARGIN_LINES)
        assert all(line["dir"] == (1, 0) for line in margin["lines"])
        x0, y0, x1, y1 = margin["bbox"]
        assert x0 > W * 0.9
        assert x1 - x0 < 25
        assert y1 - y0 < 4 * (x1 - x0)
        assert not _is_furniture(margin, page, _block_text(margin))
    assert NORMAL_MARGIN_TEXT in rotated_case.text


def test_long_repeated_running_head_extraction_contract(long_head_pdf):
    """Pin identical five-line top-edge blocks with a >80-letter signature (#34)."""
    with fitz.open(long_head_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 3
        heads = [
            _containing(_text_blocks(pdict), LONG_HEAD_LINES[0])
            for _page, pdict in pages
        ]
        signature = _signature(" ".join(LONG_HEAD_LINES))
        assert 80 < len(signature) <= 300
        assert len(signature) == 265
        assert Counter(_signature(_block_text(head)) for head in heads) == {signature: 3}
        for (page, pdict), head in zip(pages, heads):
            assert [_line_text(line) for line in head["lines"]] == list(LONG_HEAD_LINES)
            assert all(line["dir"] == (1, 0) for line in head["lines"])
            assert head["bbox"] == pytest.approx((72, 5.55, 244.73, 37.79), abs=0.05)
            assert head["bbox"][3] < H * 0.07
            assert not _is_furniture(head, page, _block_text(head))
            assert all(
                head["bbox"][3] < block["bbox"][1]
                for block in _text_blocks(pdict) if block is not head
            )
        edges = _doc_column_edges(pages, _boilerplate_signatures(pages))
        assert edges == pytest.approx([72])
        _assert_no_tables(pages, edges)


def test_long_repeated_running_head_conversion_and_lint(long_head_case):
    """A leaked long head still has to produce valid EPUB with every body page retained."""
    _assert_conversion(long_head_case, 3)
    for number in range(1, 4):
        assert f"Long-head body page {number} opens" in long_head_case.text
        assert f"Long-head body page {number} closes" in long_head_case.text


def test_issue34_long_repeated_head_is_absent_from_body(long_head_case):
    """The five-line running head's 265-letter signature recurs on every
    page and is dropped as boilerplate."""
    require_absent(
        long_head_case.text,
        *LONG_HEAD_LINES,
        issue="#34: five-line running-head license currently leaks into article body",
    )


def test_fused_running_head_title_extraction_contract(fused_pdf):
    """Pin both leading head lines and title in one block crossing the edge zone (#40)."""
    with fitz.open(fused_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 3
        assert doc.metadata["title"] == FUSED_TITLE
        fused = _containing(_text_blocks(pages[0][1]), FUSED_TITLE_LINES[0])
        assert [_line_text(line) for line in fused["lines"]] == [
            *FUSED_HEAD_LINES, *FUSED_TITLE_LINES,
        ]
        assert fused is _containing(_text_blocks(pages[0][1]), FUSED_HEAD_LINES[0])
        assert fused["bbox"] == pytest.approx((40, 92.1, 575.1, 165.38), abs=0.1)
        assert all(line["bbox"][3] < H * 0.2 for line in fused["lines"][:2])
        assert fused["bbox"][3] > H * 0.2
        assert fused["lines"][0]["bbox"][0] == 40
        assert fused["lines"][1]["bbox"][0] == 398
        assert all(line["dir"] == (1, 0) for line in fused["lines"])
        assert all(
            span["size"] == 18
            for line in fused["lines"][2:] for span in line["spans"]
        )
        head_signature = _signature(" ".join(FUSED_HEAD_LINES))
        later_heads = [
            _containing(_text_blocks(pdict), FUSED_HEAD_LINES[0])
            for _page, pdict in pages[1:]
        ]
        assert Counter(_signature(_block_text(head)) for head in later_heads) == {
            head_signature: 2,
        }
        for head in later_heads:
            assert [_line_text(line) for line in head["lines"]] == list(FUSED_HEAD_LINES)
            assert head["bbox"][3] < H * 0.2
        boilerplate = _boilerplate_signatures(pages)
        assert head_signature in boilerplate
        assert all(_signature(line) not in boilerplate for line in FUSED_HEAD_LINES)
        assert _signature(_block_text(fused)) != head_signature
        edges = _doc_column_edges(pages, boilerplate)
        assert edges == pytest.approx([72])
        _assert_no_tables(pages, edges)


def test_fused_running_head_title_conversion_and_lint(fused_case):
    """The missing h1 symptom cannot excuse lost title/body content or invalid packaging."""
    _assert_conversion(fused_case, 3)
    assert FUSED_TITLE in fused_case.text
    for number in range(1, 4):
        assert f"Fused-head body page {number} retains" in fused_case.text
        assert f"Fused-head body page {number} preserves" in fused_case.text


@pytest.mark.xfail(
    strict=True,
    raises=SemanticMismatch,
    reason="#40: the fused 'Article https://doi.org' head makes the title a byline, losing its h1",
)
def test_issue40_fused_head_leaves_title_as_h1(fused_case):
    """The known head/title fusion must be the only expected classification mismatch."""
    require(
        FUSED_TITLE in fused_case.h1s
        and all(line not in fused_case.text for line in FUSED_HEAD_LINES),
        "#40: expected the article title as h1 without 'Article https://doi.org' in body; "
        f"observed h1s {fused_case.h1s!r} and bylines {fused_case.bylines!r}",
    )


def test_front_matter_info_box_extraction_contract(info_box_pdf):
    """Pin two distinct stacked field blocks beside, not fused with, the abstract (#41)."""
    with fitz.open(info_box_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 4
        blocks = _text_blocks(pages[0][1])
        fields = [_containing(blocks, field) for field in INFO_FIELDS]
        prefix = _containing(blocks, INFO_PREFIX[0])
        suffix = _containing(blocks, INFO_SUFFIX[0])
        title = _containing(blocks, INFO_TITLE)
        assert len({id(block) for block in [*fields, prefix, suffix, title]}) == 5
        assert [_block_text(field).split(":", 1)[0] for field in fields] == [
            "Received", "Accepted",
        ]
        assert [_line_text(line) for line in prefix["lines"]] == list(INFO_PREFIX)
        assert [_line_text(line) for line in suffix["lines"]] == list(INFO_SUFFIX)
        assert fields[0]["bbox"] == pytest.approx((40, 232.33, 137.54, 244.69), abs=0.1)
        assert fields[1]["bbox"] == pytest.approx((40, 256.33, 130.55, 268.69), abs=0.1)
        _, line_h = _body_font_size(pages)
        assert 0 < fields[1]["bbox"][1] - fields[0]["bbox"][3] <= 2 * line_h
        assert min(field["bbox"][2] for field in fields) > max(
            field["bbox"][0] for field in fields
        )
        assert title["bbox"][0] == prefix["bbox"][0] == suffix["bbox"][0] == 180
        assert title["bbox"][3] < prefix["bbox"][1]
        assert prefix["bbox"][3] < fields[0]["bbox"][1]
        assert fields[1]["bbox"][3] < suffix["bbox"][1] < H * 0.5
        for field in fields:
            assert len(field["lines"]) == 1
            assert field["lines"][0]["dir"] == (1, 0)
            assert field["bbox"][2] < prefix["bbox"][0]
            assert not _is_furniture(field, pages[0][0], _block_text(field))
            assert _signature(_block_text(field)) not in _boilerplate_signatures(pages)
        edges = _doc_column_edges(pages, _boilerplate_signatures(pages))
        assert edges == pytest.approx([180])
        _assert_no_tables(pages, edges)


def test_front_matter_info_box_conversion_and_lint(info_box_case):
    """Keep all field text and surrounding prose even while the fields splice the abstract."""
    _assert_conversion(info_box_case, 4)
    assert INFO_TITLE in info_box_case.text
    assert all(field in info_box_case.text for field in INFO_FIELDS)
    assert INFO_PREFIX[0] in info_box_case.text
    assert INFO_SUFFIX[0] in info_box_case.text
    for number in range(2, 5):
        assert f"Info-box body page {number} continues" in info_box_case.text
        assert f"Info-box body page {number} closes" in info_box_case.text


@pytest.mark.xfail(
    strict=True,
    raises=SemanticMismatch,
    reason="#41: stacked Received:/Accepted: fields splice the abstract between 'final' and 'observation'",
)
def test_issue41_info_box_preserves_uninterrupted_abstract(info_box_case):
    """Expect only the documented field intrusion, never missing fields or operational errors."""
    require(
        INFO_PARAGRAPH in info_box_case.paragraphs
        and all(field in info_box_case.text for field in INFO_FIELDS),
        "#41: expected one intact 'final observation' abstract paragraph with both fields retained; "
        f"observed paragraphs {info_box_case.paragraphs!r}",
    )


def test_info_box_neighbor_and_inline_label_prose_are_retained(info_box_case):
    """Ordinary neighboring text and a mid-sentence Received: mention stay article content."""
    assert INFO_BODY_PROSE in info_box_case.text
    assert info_box_case.text.count(INLINE_RECEIVED_PROSE) == 3


def test_issue34_rotated_stamp_kept_verbatim_under_keep_headers(rotated_keep_case):
    """--keep-headers preserves the stamp as inert furniture at the page
    edge while the hyphenated paragraph around it rejoins intact (#34)."""
    case = rotated_keep_case
    _assert_conversion(case, 1)
    assert ROTATED_STAMP in case.text
    assert ROTATED_PARAGRAPH in case.paragraphs


def test_issue34_long_head_kept_verbatim_under_keep_headers(long_head_keep_case):
    """The five-line head emits verbatim once per page at the top edge (#34)."""
    case = long_head_keep_case
    _assert_conversion(case, 3)
    for line in LONG_HEAD_LINES:
        assert case.text.count(line) == 3


def test_rotated_page_upright_extraction_contract(upright_pdf):
    """Pin the /Rotate pages' upright margin blocks (#34): each reports
    the mediabox dir that *displays* upright ((0,-1) on /Rotate 90,
    (0,1) on /Rotate 270), still sits tall+narrow in the outer 10%
    mediabox margin, and survives because a reliably upright line vetoes
    the strip classification."""
    with fitz.open(upright_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 2
        for page, pdict in pages:
            rotation = page.rotation
            assert rotation in (90, 270)
            upright = (0.0, -1.0) if rotation == 90 else (0.0, 1.0)
            tag = UPRIGHT_TAGS[rotation]
            single = _containing(
                _text_blocks(pdict), f"{UPRIGHT_EDGE_LINE} {tag}")
            multi = _containing(
                _text_blocks(pdict), f"{UPRIGHT_EDGE_LINES[0]} {tag}")
            assert [_line_text(line) for line in multi["lines"]] == [
                f"{line} {tag}" for line in UPRIGHT_EDGE_LINES]
            for block in (single, multi):
                assert all(
                    tuple(line["dir"]) == upright for line in block["lines"])
                x0, y0, x1, y1 = block["bbox"]
                assert x0 > W * 0.9 or x1 < W * 0.1
                assert y1 - y0 > 4 * (x1 - x0)
                assert not _is_margin_strip(block, page)
                assert not _is_furniture(block, page, _block_text(block))
            # the <25pt-wide case passes the legacy strip geometry
            assert single["bbox"][2] - single["bbox"][0] < 25
            body = _containing(
                _text_blocks(pdict), f"{UPRIGHT_BODY[0]} {rotation}")
            assert all(
                tuple(line["dir"]) == upright for line in body["lines"])
            assert not _is_furniture(body, page, _block_text(body))
        edges = _doc_column_edges(pages, _boilerplate_signatures(pages))
        assert edges == pytest.approx([0.0])
        _assert_no_tables(pages, edges)


def test_rotated_page_upright_margin_is_retained(upright_case):
    """Upright-on-screen margin text on /Rotate pages stays in flow (#34)."""
    _assert_conversion(upright_case, 2)
    for rotation in (90, 270):
        tag = UPRIGHT_TAGS[rotation]
        assert upright_case.text.count(
            f"{UPRIGHT_EDGE_LINE} {tag}") == 1
        for line in UPRIGHT_EDGE_LINES:
            assert upright_case.text.count(f"{line} {tag}") == 1


def test_rotated_table_margin_cells_extraction_contract(table_cells_pdf):
    """Outer cells sit in the 10% margins while the detected region
    reaches the body band — ordering must not flag them as strips (#34)."""
    with fitz.open(table_cells_pdf) as doc:
        assert len(doc) == 1
        page = doc[0]
        pdict = page.get_text("dict")
        blocks = _text_blocks(pdict)
        cells = [_containing(blocks, cell) for cell in TABLE_CELLS]
        for cell in cells:
            assert len(cell["lines"]) == 1
            assert tuple(cell["lines"][0]["dir"]) == (0.0, -1.0)
        assert cells[0]["bbox"][2] < W * 0.1
        assert cells[3]["bbox"][0] > W * 0.9
        assert W * 0.1 < cells[1]["bbox"][0]
        assert cells[2]["bbox"][2] < W * 0.9
        region = _rotated_table_region(pdict["blocks"])
        assert region is not None
        assert region.x0 <= W * 0.9 and region.x1 >= W * 0.1
        # kept-mode ordering exposes flagging: exempted cells carry no
        # is_furniture mark — a dropped-as-strip cell would
        ordered = _ordered_blocks(
            page, pdict["blocks"], frozenset(), [0.0], drop_furniture=False)
        assert not any(b.get("is_furniture") for b in ordered)
        label = _containing(blocks, AXIS_LABEL)
        assert label["bbox"][3] < region.y0
        assert not _is_margin_strip(label, page)
        assert not _is_furniture(label, page, _block_text(label))


def test_rotated_table_margin_cells_rasterize(table_cells_case):
    """The grid still rasterizes — cells leave the flow through the
    rotated-table path, not through the margin-strip arm (#34)."""
    case = table_cells_case
    assert case.stats["raster_tables"] == 1
    assert lint_epub(case.epub) == []
    for cell in TABLE_CELLS:
        assert cell not in case.text
    html = "".join(doc_html(case.epub).values())
    assert "table_" in html


def test_rotated_table_margin_cells_keep_headers_rasterize(table_cells_keep_case):
    """Under --keep-headers the cells still rasterize: a cell flagged
    furniture would emit its text verbatim at the page edge (#34)."""
    case = table_cells_keep_case
    assert case.stats["raster_tables"] == 1
    assert lint_epub(case.epub) == []
    for cell in TABLE_CELLS:
        assert cell not in case.text


def test_over_bound_running_head_extraction_contract(over_bound_pdf):
    """Both repeated heads overflow a bound arm — a >300-letter signature
    and a 7-line block — so recurrence can't excuse them (#34)."""
    with fitz.open(over_bound_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 2
        char_sig = _signature(" ".join(OVER_CHAR_HEAD))
        line_sig = _signature(" ".join(OVER_LINE_HEAD))
        assert len(char_sig) > 300
        assert len(line_sig) < 300
        boilerplate = _boilerplate_signatures(pages)
        assert char_sig not in boilerplate
        assert line_sig not in boilerplate
        for page, pdict in pages:
            char_head = _containing(_text_blocks(pdict), OVER_CHAR_HEAD[0])
            line_head = _containing(_text_blocks(pdict), OVER_LINE_HEAD[0])
            assert len(char_head["lines"]) == 3
            assert len(line_head["lines"]) == 7
            for head in (char_head, line_head):
                assert head["bbox"][3] < H * 0.2
                assert _signature(_block_text(head)) not in boilerplate
                assert not _is_signature_furniture(
                    head, page.rect.height, boilerplate)
        edges = _doc_column_edges(pages, boilerplate)
        assert edges == pytest.approx([72])
        _assert_no_tables(pages, edges)


def test_over_bound_running_head_is_retained(over_bound_case):
    """The repeated heads stay in body flow — repetition cannot excuse a
    block that overflows either bound arm (#34)."""
    _assert_conversion(over_bound_case, 2)
    for line in (*OVER_CHAR_HEAD, *OVER_LINE_HEAD):
        assert over_bound_case.text.count(line) == 2


def _strip_block(dirs, bbox=(20.0, 300.0, 42.0, 500.0)):
    """A fabricated tall narrow left-margin block whose lines report dirs."""
    x0, y0, x1, _ = bbox
    lines = []
    for i, d in enumerate(dirs):
        line = {"spans": [{"text": "x", "size": 9}],
                "bbox": (x0, y0 + i * 12, x1, y0 + i * 12 + 12)}
        if d is not None:
            line["dir"] = d
        lines.append(line)
    return {"type": 0, "bbox": bbox, "lines": lines}


# wide enough to fail the legacy <25pt cap, still tall+margin
_WIDE_STRIP = (11.0, 250.0, 38.0, 620.0)


def test_margin_strip_direction_rules():
    """Pin the direction contract directly (#34): all-rotated qualifies
    at any width, a reliably upright line always vetoes, and missing or
    unusable direction data falls back to the legacy <25pt shape."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    # the arXiv shape — 27pt wide overflows the old cap; direction saves it
    assert _is_margin_strip(_strip_block([(0.0, -1.0)], bbox=_WIDE_STRIP), page)
    # upside-down text is rotated too — it differs from upright
    assert _is_margin_strip(_strip_block([(0.0, 1.0)]), page)
    # any reliably upright line vetoes — single or mixed with rotated
    for dirs in ([(1.0, 0.0)], [(0.0, -1.0), (1.0, 0.0)],
                 [(1.0, 0.0), (0.0, -1.0)]):
        assert not _is_margin_strip(_strip_block(dirs), page)
        assert not _is_margin_strip(
            _strip_block(dirs, bbox=_WIDE_STRIP), page)
    # missing or unusable direction data → legacy narrow-shape fallback
    for dirs in ([None], [(0.7, 0.7)], [(0.0, 0.0)],
                 [None, (0.0, -1.0)], [(0.0, -1.0), None]):
        assert _is_margin_strip(_strip_block(dirs), page)
        assert not _is_margin_strip(
            _strip_block(dirs, bbox=_WIDE_STRIP), page)
    # the fallback never overrides a known upright line
    assert not _is_margin_strip(_strip_block([None, (1.0, 0.0)]), page)
    # shape still gates: not tall enough or not in the margin → not a strip
    assert not _is_margin_strip(
        _strip_block([(0.0, -1.0)], bbox=(20.0, 300.0, 42.0, 360.0)), page)
    assert not _is_margin_strip(
        _strip_block([(0.0, -1.0)], bbox=(200.0, 300.0, 222.0, 500.0)), page)


def test_margin_strip_rotation_space():
    """On a /Rotate page the veto compares against the page's own upright
    direction — a mediabox dir that displays upright vetoes, one that
    displays rotated still qualifies (#34)."""
    doc = fitz.open()
    for rotation, displayed_upright, displayed_rotated in (
            (90, (0.0, -1.0), (1.0, 0.0)), (270, (0.0, 1.0), (1.0, 0.0))):
        page = doc.new_page(width=W, height=H)
        page.set_rotation(rotation)
        assert not _is_margin_strip(_strip_block([displayed_upright]), page)
        assert _is_margin_strip(_strip_block([displayed_rotated]), page)


def test_margin_strip_table_clip_exemption():
    """A strip-shaped block inside a body-reaching rotated-table region
    is exempted; a region contained in the margin exempts nothing (#34)."""
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    block = _strip_block([(0.0, -1.0)])
    assert not _is_margin_strip(
        block, page, fitz.Rect(15, 350, 560, 520))
    assert _is_margin_strip(
        block, page, fitz.Rect(10, 350, 50, 520))
    assert _is_margin_strip(
        block, page, fitz.Rect(100, 100, 200, 200))
    assert not _is_furniture(
        block, page, "x", clip=fitz.Rect(15, 350, 560, 520))
    assert _is_furniture(
        block, page, "x", clip=fitz.Rect(10, 350, 50, 520))


def test_repeated_bottom_foot_extraction_contract(bottom_foot_pdf):
    """Pin the bottom running foot and the varying © line (#45): the
    foot's signature recurs inside the bottom edge zone, while each
    © line differs and qualifies only through furniture content in the
    outer 7% — both must drop, through different arms."""
    with fitz.open(bottom_foot_pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        assert len(pages) == 3
        foot_sig = _signature(BOTTOM_FOOT)
        boilerplate = _boilerplate_signatures(pages)
        assert foot_sig in boilerplate
        feet = [
            _containing(_text_blocks(pdict), BOTTOM_FOOT)
            for _page, pdict in pages
        ]
        copies = [
            _containing(_text_blocks(pdict), f"page {tag}")
            for (_page, pdict), tag in zip(pages, BOTTOM_COPY_TAGS)
        ]
        for (page, _pdict), foot, copy in zip(pages, feet, copies):
            assert foot["bbox"][1] > H * 0.8
            assert _is_signature_furniture(
                foot, page.rect.height, boilerplate)
            assert copy["bbox"][1] > H * 0.93
            assert _signature(_block_text(copy)) not in boilerplate
            assert _is_furniture(copy, page, _block_text(copy))
        assert len({_signature(_block_text(c)) for c in copies}) == 3
        edges = _doc_column_edges(pages, boilerplate)
        assert edges == pytest.approx([72])
        _assert_no_tables(pages, edges)


def test_repeated_bottom_foot_is_dropped(bottom_foot_case):
    """Both bottom furniture forms drop; the whole article body stays
    (#45)."""
    case = bottom_foot_case
    _assert_conversion(case, 3)
    require_absent(
        case.text,
        BOTTOM_FOOT,
        *(f"page {tag}" for tag in BOTTOM_COPY_TAGS),
        issue="#45: bottom-edge furniture leaked into the article body",
    )
