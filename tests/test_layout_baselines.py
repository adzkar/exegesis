"""Independent extraction, EPUB validity, and owned semantic gates for issue #33."""
from __future__ import annotations

import re
from collections import Counter

import fitz
import pytest
from helpers import chapter_body_text, doc_html, lint_epub
from layout_assertions import (
    SemanticMismatch,
    assert_retained_once,
    require,
    require_absent,
    require_order,
)
from layout_fixtures import GENERATORS

from exegesis import convert
from exegesis.analyze import (
    _assign_column,
    _boilerplate_signatures,
    _column_edges,
    _doc_column_edges,
    _is_furniture,
    _is_signature_furniture,
    _is_spanning,
    _reading_columns,
)
from exegesis.text import _block_text

REQUIRED_CONTENT = {
    "mixed_1col_plus_2col": [
        *(f"Single page {number} {part}" for number in range(4) for part in ("opening", "closing")),
        *(f"{side} {part} minority" for side in ("Left", "Right") for part in ("upper", "lower")),
    ],
    "mixed_parity_2col": [
        *(f"Parity {'A' if number % 2 == 0 else 'B'}{number} {side} {part}"
          for number in range(6) for side in ("left", "right")
          for part in ("upper", "lower")),
        *(f"Tail prose {number} {part}" for number in range(5)
          for part in ("upper", "lower")),
    ],
    "mixed_2col_plus_3col": [
        *(f"Doc {number} {side} {part}" for number in range(6)
          for side in ("left", "right") for part in ("upper", "lower")),
        *(f"Supplement {number} col{side} {part}" for number in range(2)
          for side in ("one", "two", "three") for part in ("upper", "lower")),
    ],
    "magazine_wrap": ["Wrap opening", "Near upper", "Near lower", "Far upper", "Far lower", "Resumed body"],
    "scan_2up": [f"{side} spread {part}" for side in ("Left", "Right")
                 for part in ("opening", "middle", "closing")],
    "vector_figure": ["Vector body", "Figure 1. Vector trend.", "Vector continuation"],
    "raster_figure": ["Raster body", "Figure 2. Raster panels.", "Raster continuation"],
    "outdented_heads": [
        *(f"Reference span {number} {part}" for number in range(3) for part in ("first", "last")),
        *(f"Reference body {number} {part}" for number in range(3) for part in ("upper", "lower")),
        *(f"Section {number} {part}" for number in range(3, 6) for part in ("alpha", "beta")),
        *(f"Outdent body {number} {part}" for number in range(3, 6)
          for part in ("opening", "middle", "closing")),
    ],
    "outdented_two_spans": [f"{part} {number}" for number in range(4)
                            for part in ("First span", "Between spans", "Between body", "Last span", "Final body")],
    "outdented_consumers": [
        *(f"Consumer span {number} at {y}" for number in range(2) for y in (190, 450)),
        *(f"Consumer span-body {number} at {y}" for number in range(2) for y in (290, 550)),
        *(f"Head {number} {part}" for number in range(2, 6)
          for part in ("alpha", "beta")),
        *(f"Consumer body {number} {part}" for number in range(2, 6)
          for part in ("alpha", "beta")),
        *(f"Figure {number}. View" for number in range(2, 6)),
        *(f"Quoted consumer passage {number} one" for number in range(2, 6)),
        *(f"quoted consumer passage {number} two" for number in range(2, 6)),
    ],
    "outdented_chain": [
        *(f"Pt {number}{part}" for number in range(6) for part in ("a", "b")),
        *(f"Sec {number}{part}" for number in range(6) for part in ("a", "b")),
        *(f"Chain body {number} {part}" for number in range(6)
          for part in ("alpha", "beta", "gamma")),
    ],
    "outdented_note_zone": [
        *(f"Head {number} {part}" for number in range(4) for part in ("alpha", "beta")),
        *(f"Zone body {number} {part}" for number in range(4) for part in ("alpha", "beta")),
        *(f"Zone body {number} gamma" for number in range(2)),
        *(f"Late label {number}" for number in range(2, 4)),
    ],
    "two_col_heading_run": [
        *(f"Left head {number}" for number in range(4)),
        *(f"Left prose {number} {part}" for number in range(4)
          for part in ("upper", "middle", "lower", "closing")),
        *(f"Right prose {number} {part}" for number in range(4)
          for part in ("upper", "lower")),
    ],
    "two_col_label_list": [
        *(f"Label {number} {i}" for number in range(4) for i in range(7) if i not in (2, 5)),
        *(f"Wrapped label {number} {i} line one" for number in range(4) for i in (2, 5)),
        *(f"wrapped label {number} {i} line two" for number in range(4) for i in (2, 5)),
        *(f"wrapped label {number} {i} line three" for number in range(4) for i in (2, 5)),
        *(f"List body {number} {part}" for number in range(4) for part in ("upper", "lower")),
    ],
    "span_only_edge": [
        *(f"Wide band {number} {part}" for number in range(4) for part in ("upper", "lower")),
        *(f"Span-only body {number} {part}" for number in range(4) for part in ("first", "second")),
    ],
    "signature_outdent_margin": [
        *(f"Manuscript body {number} {part}" for number in range(29)
          for part in ("opening", "middle", "closing")),
        *(f"Aim {number:02d}" for number in range(17)),
        *(f"Data {number:02d}" for number in range(13)),
    ],
    "heads_only_phantom": [
        *(f"Latent body {number} {part}" for number in range(6)
          for part in ("opening", "middle", "closing")),
        *(f"{part} {number:02d}" for number in range(3) for part in ("Aim", "Data")),
    ],
    "retained_content": ["Negative opening", "Normal margin prose", "continues as real content.",
                         "Full-width content", "Negative closing"],
}


@pytest.fixture(scope="module")
def source(request, tmp_path_factory):
    """Build every source from scratch without converting or reading a local corpus."""
    name = request.param
    path = tmp_path_factory.mktemp(name) / f"{name}.pdf"
    return name, GENERATORS[name](path)


@pytest.fixture(scope="module")
def converted(source):
    """Run ordinary setup outside semantic assertions so operational failures stay red."""
    name, path = source
    out = path.with_suffix(".epub")
    stats = convert(path, out)
    body = chapter_body_text(out)
    assert_retained_once(body, *REQUIRED_CONTENT[name])
    return name, out, stats, body, doc_html(out)


def _texts(pdict):
    """Retain the original extracted blocks so identity and fusion can be checked."""
    return [block for block in pdict["blocks"] if block["type"] == 0]


def _find(pdict, needle):
    """Require one physical block, not a substring assembled from several blocks."""
    found = [block for block in _texts(pdict) if needle in _block_text(block)]
    assert len(found) == 1, (needle, [_block_text(block) for block in found])
    return found[0]


def _upright(block):
    """Exclude accidental rotation as a source of these horizontal-layout failures."""
    assert all(tuple(line["dir"]) == (1, 0) for line in block["lines"])


def _eligible(page, pdict, boilerplate=frozenset()):
    """Count votes using the same furniture eligibility as document column analysis."""
    return [
        block for block in _texts(pdict)
        if not _is_furniture(block, page, _block_text(block))
        and not _is_signature_furniture(block, page.rect.height, boilerplate)
    ]


def _votes(pages, boilerplate=frozenset()):
    """Expose page-level quorum support without pinning a future reading-column fix."""
    return Counter(
        round(edge) for page, pdict in pages
        for edge in _column_edges(_eligible(page, pdict, boilerplate))
    )


def _contract_mixed(pages):
    """Four one-column pages outvote one genuinely separated two-column page."""
    assert len(pages) == 5
    assert [_column_edges(_texts(pdict)) for _, pdict in pages[:4]] == [[72]] * 4
    minority = pages[-1][1]
    assert _column_edges(_texts(minority)) == [72, 340]
    blocks = [_find(minority, f"{side} {part} minority")
              for side in ("Left", "Right") for part in ("upper", "lower")]
    assert len({id(block) for block in blocks}) == 4
    for block in blocks:
        _upright(block)
        assert len(block["lines"]) >= 2
        assert block["bbox"][0] in (72, 340)
    assert max(block["bbox"][2] for block in blocks[:2]) < 330
    assert _votes(pages)[340] == 1 < len(pages) // 2


def _contract_parity(pages):
    """Alternating mirrored parities split the vote; only the wide tail reaches quorum."""
    assert len(pages) == 11
    votes = _votes(pages)
    for edge in (50, 90, 300, 340):
        assert votes[edge] == 3 < len(pages) // 2
    assert votes[200] == 5 == len(pages) // 2
    assert _doc_column_edges(pages, _boilerplate_signatures(pages)) == [200]
    for pi, (_, pdict) in enumerate(pages[:6]):
        assert _column_edges(_texts(pdict)) == (
            [50, 300] if pi % 2 == 0 else [90, 340]
        )
        for block in _texts(pdict):
            _upright(block)
            assert len(block["lines"]) >= 2
    for _, pdict in pages[6:]:
        assert _column_edges(_texts(pdict)) == [200]


def _contract_three_col(pages):
    """The majority two-column vote commits edges the minority supplement splits."""
    assert len(pages) == 8
    votes = _votes(pages)
    assert votes[72] == len(pages)
    assert votes[310] == 6 >= len(pages) // 2
    assert votes[240] == votes[410] == 2 < len(pages) // 2
    assert _doc_column_edges(pages, _boilerplate_signatures(pages)) == [72, 310]
    for _, pdict in pages[6:]:
        assert _column_edges(_texts(pdict)) == [72, 240, 410]


def _contract_wrap(pages):
    """The same image band has separate near/far lines and resumed full-width prose."""
    assert len(pages) == 1
    page, pdict = pages[0]
    images = [block for block in pdict["blocks"] if block["type"] == 1]
    assert len(images) == 1
    region = fitz.Rect(images[0]["bbox"])
    assert tuple(region) == pytest.approx((230, 290, 340, 440))
    for label in ("upper", "lower"):
        near = _find(pdict, f"Near {label}")
        far = _find(pdict, f"Far {label}")
        assert near is not far
        assert near["bbox"][2] < region.x0 < region.x1 < far["bbox"][0]
        assert region.y0 < near["bbox"][1] < far["bbox"][3] < region.y1
        for block in (near, far):
            assert len(block["lines"]) == 1
            _upright(block)
            assert not _is_furniture(block, page, _block_text(block))
    resumed = _find(pdict, "Resumed body")
    assert resumed["bbox"][1] > region.y1
    edges = _column_edges(_texts(pdict))
    assert edges == [72, 365]
    assert _is_spanning(resumed, edges)
    assert _assign_column(resumed, edges) == 0
    assert _assign_column(_find(pdict, "Far upper"), edges) == 1


def _contract_twoup(pages):
    """Both half-pages span the page height while leaving a clean symmetric seam."""
    assert len(pages) == 1
    page, pdict = pages[0]
    assert page.rect.width == 1000
    assert _column_edges(_texts(pdict)) == [60, 580]
    assert len(_texts(pdict)) == 6
    for block in _texts(pdict):
        _upright(block)
        assert block["bbox"][2] < 500 or block["bbox"][0] > 500
    assert max(block["bbox"][3] for block in _texts(pdict)) > page.rect.height * 0.8


def _contract_vector(pages):
    """Short upright labels occupy drawn axes, with no raster block to explain them."""
    assert len(pages) == 1
    page, pdict = pages[0]
    assert not any(block["type"] == 1 for block in pdict["blocks"])
    drawings = page.get_drawings()
    assert len(drawings) >= 6
    region = fitz.Rect(180, 290, 440, 480)
    labels = [_find(pdict, label) for label in ("VECTORONE", "VECTORTWO", "VECTORTHREE")]
    assert len({id(block) for block in labels}) == 3
    for block in labels:
        assert len(_block_text(block)) <= 12
        assert len(block["lines"]) == 1
        assert region.contains(fitz.Rect(block["bbox"]))
        _upright(block)
    assert _find(pdict, "Figure 1.")["bbox"][1] > region.y1
    assert 0.2 * page.rect.height < region.y0 < region.y1 < 0.8 * page.rect.height


def _contract_raster(pages):
    """Overlay labels lie inside a real image; panel letters lie just outside it."""
    assert len(pages) == 1
    page, pdict = pages[0]
    images = [block for block in pdict["blocks"] if block["type"] == 1]
    assert len(images) == 1 and len(images[0]["image"]) > 2048
    region = fitz.Rect(images[0]["bbox"])
    assert tuple(region) == pytest.approx((180, 290, 440, 470))
    labels = [_find(pdict, label) for label in ("RASTERONE", "RASTERTWO")]
    for block in labels:
        assert region.contains(fitz.Rect(block["bbox"]))
        assert len(block["lines"]) == 1 and len(_block_text(block)) <= 12
        _upright(block)
    panels = [block for block in _texts(pdict) if _block_text(block) in {"A", "B"}]
    assert len(panels) == 2
    assert all(not region.intersects(fitz.Rect(block["bbox"])) for block in panels)
    assert all(min(abs(block["bbox"][2] - region.x0),
                   abs(block["bbox"][0] - region.x1)) < 15 for block in panels)
    assert _find(pdict, "Figure 2.")["bbox"][1] > region.y1
    assert region.y0 > page.rect.height * 0.2


def _contract_outdent(pages):
    """Full-width x36 votes reach quorum independently of distinct x42 heads."""
    assert len(pages) == 6
    votes = _votes(pages)
    assert votes[36] == 3 == len(pages) // 2
    assert votes[42] == 3 and votes[166] == 6
    for page, pdict in pages[:3]:
        spans = [block for block in _texts(pdict) if "Reference span" in _block_text(block)]
        assert len(spans) == 2
        assert all(block["bbox"][0] == 36 and _is_spanning(block, [36, 166])
                   for block in spans)
    for page, pdict in pages[3:]:
        heads = [block for block in _texts(pdict) if _block_text(block).startswith("Section ")]
        assert len(heads) == 2
        for block in heads:
            assert block["bbox"][0] == 42 and len(block["lines"]) == 1
            assert not _is_spanning(block, [36, 166])
            assert not _is_furniture(block, page, _block_text(block))
            assert not _is_signature_furniture(block, page.rect.height, _boilerplate_signatures(pages))
            _upright(block)


def _contract_two_spans(pages):
    """A distinct short head and body are physically between two genuine spanning blocks."""
    assert len(pages) == 4
    assert _votes(pages)[36] == len(pages)
    assert _votes(pages, _boilerplate_signatures(pages))[166] == len(pages)
    for number, (page, pdict) in enumerate(pages):
        first = _find(pdict, f"First span {number}")
        head = _find(pdict, f"Between spans {number}")
        body = _find(pdict, f"Between body {number}")
        last = _find(pdict, f"Last span {number}")
        assert len({id(block) for block in (first, head, body, last)}) == 4
        assert first["bbox"][3] < head["bbox"][1] < head["bbox"][3] < body["bbox"][1]
        assert body["bbox"][3] < last["bbox"][1]
        assert all(_is_spanning(block, [36, 166]) for block in (first, last))
        assert not _is_spanning(head, [36, 166])
        assert head["bbox"][0] == 42 and body["bbox"][0] == 166
        assert not _is_furniture(head, page, _block_text(head))
        assert len(head["lines"]) == 1


def _contract_consumers(pages):
    """x36-span and x42-head votes merge; consumer shapes stay physically distinct."""
    assert len(pages) == 6
    votes = _votes(pages)
    assert votes[36] == 2 and votes[42] == 4 and votes[166] == 6
    edges = _doc_column_edges(pages)
    assert edges == [36, 166]
    for number, (page, pdict) in enumerate(pages[2:], start=2):
        heads = [b for b in _texts(pdict)
                 if _block_text(b).startswith(f"Head {number}")]
        assert len(heads) == 2
        for head in heads:
            assert head["bbox"][0] == 42 and len(head["lines"]) == 1
            assert not _is_spanning(head, edges)
            assert not _is_furniture(head, page, _block_text(head))
        caption = _find(pdict, f"Figure {number}. View")
        assert caption["bbox"][0] == 42 and caption["bbox"][2] < edges[1] - 12
        image_blocks = [b for b in pdict["blocks"] if b["type"] == 1]
        assert len(image_blocks) == 1
        assert _assign_column(image_blocks[0], edges) == 1
        quote = _find(pdict, f"Quoted consumer passage {number} one")
        assert quote["bbox"][0] == 200 and _assign_column(quote, edges) == 1
        assert len(quote["lines"]) == 2 and not _is_spanning(quote, edges)
        note = _find(pdict, f"Consumer footnote {number}")
        assert note["bbox"][0] == 166
        assert note["bbox"][1] > page.rect.height * 0.6


def _contract_chain(pages):
    """Two short-fragment strips flank a wrapped body column on all six pages."""
    assert len(pages) == 6
    edges = _doc_column_edges(pages)
    assert edges == [42, 96, 166]
    for page, pdict in pages:
        for x0, prefix in ((42, "Pt"), (96, "Sec")):
            heads = [b for b in _texts(pdict)
                     if _block_text(b).startswith(prefix)]
            assert len(heads) == 2
            for head in heads:
                assert head["bbox"][0] == x0 and len(head["lines"]) == 1
                assert not _is_spanning(head, edges)
                assert not _is_furniture(head, page, _block_text(head))
                _upright(head)
        assert all(
            _assign_column(b, edges) == 2
            for b in _texts(pdict)
            if _block_text(b).startswith("Chain body")
        )


def _contract_note_zone(pages):
    """Low small blocks open each column's note zone; heads stay strip-shaped."""
    assert len(pages) == 4
    edges = _doc_column_edges(pages)
    assert edges == [42, 166]
    for number, (page, pdict) in enumerate(pages):
        heads = [b for b in _texts(pdict)
                 if _block_text(b).startswith(f"Head {number}")]
        assert len(heads) == 2
        for head in heads:
            assert head["bbox"][0] == 42 and len(head["lines"]) == 1
            assert not _is_spanning(head, edges)
            assert not _is_furniture(head, page, _block_text(head))
        strip_notes = [b for b in _texts(pdict)
                       if _block_text(b).startswith("Margin note")]
        assert len(strip_notes) == 1
        assert strip_notes[0]["bbox"][0] == 42
        assert not _is_furniture(
            strip_notes[0], page, _block_text(strip_notes[0])
        )
        body_notes = [b for b in _texts(pdict)
                      if _block_text(b).startswith("Zone footnote")]
        assert len(body_notes) == 1
        assert body_notes[0]["bbox"][0] == 166
        if number < 2:
            victim = _find(pdict, f"Zone body {number} gamma")
            assert victim["bbox"][0] == 166 and victim["bbox"][1] > 613
        else:
            label = _find(pdict, f"Late label {number}")
            assert label["bbox"][0] == 42 and label["bbox"][1] > 613


def _contract_heading_run(pages):
    """Four wrapped paragraphs under one head keep the left column's share low."""
    assert len(pages) == 4
    edges = _doc_column_edges(pages)
    assert edges == [72, 360]
    for page, pdict in pages:
        left = [b for b in _texts(pdict) if _assign_column(b, edges) == 0]
        assert len(left) == 5
        assert sum(len(b["lines"]) == 1 for b in left) == 1
        assert all(not _is_spanning(b, edges) for b in left)


def _contract_label_list(pages):
    """Five heading-like labels in seven blocks keeps the edge under the share."""
    assert len(pages) == 4
    edges = _doc_column_edges(pages)
    assert edges == [72, 360]
    for page, pdict in pages:
        left = [b for b in _texts(pdict) if _assign_column(b, edges) == 0]
        assert len(left) == 7
        assert sum(len(b["lines"]) == 1 for b in left) == 5
        assert sum(len(b["lines"]) >= 3 for b in left) == 2


def _contract_span_only(pages):
    """Spans alone vote the left edge: no non-spanning evidence exists for it."""
    assert len(pages) == 4
    edges = _doc_column_edges(pages)
    assert edges == [36, 166]
    for page, pdict in pages:
        col0 = [b for b in _texts(pdict)
                if not _is_spanning(b, edges) and _assign_column(b, edges) == 0]
        assert col0 == []
        spans = [b for b in _texts(pdict) if _is_spanning(b, edges)]
        assert len(spans) == 2


def _contract_signature_margin(pages):
    """Boilerplate exclusion alone changes 17 phantom-edge votes to 13, below 14."""
    assert len(pages) == 29 and len(pages) // 2 == 14
    boilerplate = _boilerplate_signatures(pages)
    raw_votes, clean_votes = _votes(pages), _votes(pages, boilerplate)
    assert raw_votes[90] + raw_votes[84] == 17
    assert clean_votes[90] == 13
    assert _doc_column_edges(pages, boilerplate) == [150]
    assert _doc_column_edges(pages, frozenset()) == [90, 150]
    for number, (page, pdict) in enumerate(pages):
        running = _find(pdict, f"Author et al. Page {number + 1}")
        assert running["bbox"][0] == 78
        assert _is_signature_furniture(running, page.rect.height, boilerplate)
        assert not _is_furniture(running, page, _block_text(running))
        heads = [block for block in _texts(pdict)
                 if _block_text(block).startswith(("Aim ", "Data "))]
        assert len(heads) == (2 if number < 13 else 1 if number < 17 else 0)
        for head in heads:
            assert head["bbox"][0] == 90 and len(head["lines"]) == 1
            assert not _is_spanning(head, [90, 150])
            assert head["bbox"][2] < 138
            assert not _is_furniture(head, page, _block_text(head))
            assert not _is_signature_furniture(head, page.rect.height, boilerplate)


def _contract_heads_only(pages):
    """No running head exists; two distinct non-furniture heads supply each x90 vote."""
    assert len(pages) == 6
    assert _boilerplate_signatures(pages) == set()
    assert _votes(pages)[90] == 3 == len(pages) // 2
    for number, (page, pdict) in enumerate(pages):
        heads = [block for block in _texts(pdict)
                 if _block_text(block).startswith(("Aim ", "Data "))]
        assert len(heads) == (2 if number < 3 else 0)
        for head in heads:
            assert head["bbox"][0] == 90 and len(head["lines"]) == 1
            assert not _is_spanning(head, [90, 150])
            assert head["bbox"][2] < 138
            assert not _is_furniture(head, page, _block_text(head))
            _upright(head)


def _contract_retained(pages):
    """The negative margin block is horizontal multiline prose, not a rotated strip."""
    assert len(pages) == 1
    page, pdict = pages[0]
    margin = _find(pdict, "Normal margin prose")
    assert margin["bbox"][0] == 18 and len(margin["lines"]) == 2
    _upright(margin)
    assert not _is_furniture(margin, page, _block_text(margin))
    span = _find(pdict, "Full-width content")
    assert span["bbox"][0] == 36 and span["bbox"][2] > 500
    assert not _is_furniture(span, page, _block_text(span))


CONTRACTS = {
    "mixed_1col_plus_2col": _contract_mixed,
    "mixed_parity_2col": _contract_parity,
    "mixed_2col_plus_3col": _contract_three_col,
    "magazine_wrap": _contract_wrap,
    "scan_2up": _contract_twoup,
    "vector_figure": _contract_vector,
    "raster_figure": _contract_raster,
    "outdented_heads": _contract_outdent,
    "outdented_two_spans": _contract_two_spans,
    "outdented_consumers": _contract_consumers,
    "outdented_chain": _contract_chain,
    "outdented_note_zone": _contract_note_zone,
    "two_col_heading_run": _contract_heading_run,
    "two_col_label_list": _contract_label_list,
    "span_only_edge": _contract_span_only,
    "signature_outdent_margin": _contract_signature_margin,
    "heads_only_phantom": _contract_heads_only,
    "retained_content": _contract_retained,
}


@pytest.mark.parametrize("source", GENERATORS, indirect=True)
def test_layout_extraction_contract(source):
    """Fixture geometry/extraction mistakes fail normally, never as expected regressions."""
    name, path = source
    with fitz.open(path) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        CONTRACTS[name](pages)


@pytest.mark.parametrize("source", GENERATORS, indirect=True)
def test_layout_conversion_and_lint(converted):
    """Every baseline must pass structural/link lint independently of its semantic xfail."""
    _, out, stats, _, htmls = converted
    assert lint_epub(out) == []
    assert stats["pages"] >= 1
    assert stats["chapters"] >= 1
    assert any(name.startswith("chap_") for name in htmls)


@pytest.mark.parametrize("source", ("outdented_heads", "outdented_two_spans"), indirect=True)
def test_full_width_span_content_retained(converted):
    """A reading-column repair must not solve ordering by dropping legitimate spans."""
    name, _, _, body, _ = converted
    labels = (
        [f"Reference span {number} {part}" for number in range(3) for part in ("first", "last")]
        if name == "outdented_heads"
        else [f"{part} span {number}" for number in range(4) for part in ("First", "Last")]
    )
    assert all(body.count(label) == 1 for label in labels)


@pytest.mark.parametrize("source", ("vector_figure", "raster_figure"), indirect=True)
def test_figure_surrounding_prose_and_caption_retained(converted):
    """Figure claims may remove labels from flow, never their neighboring body or caption."""
    name, _, _, body, _ = converted
    side, number = ("Vector", 1) if name == "vector_figure" else ("Raster", 2)
    labels = (f"{side} body", f"Figure {number}.", f"{side} continuation")
    assert all(body.count(label) == 1 for label in labels)
    assert [body.index(label) for label in labels] == sorted(body.index(label) for label in labels)


def _semantic_mixed(result):
    """#37: the minority page's own grid orders its two columns left then right."""
    require_order(result[3], "Left upper minority", "Left lower minority",
                  "Right upper minority", "Right lower minority", issue="#37 mixed grid")


def _semantic_parity(result):
    """#37: each parity page orders its own mirrored columns; stats report the wider grid."""
    for number in range(6):
        tag = "A" if number % 2 == 0 else "B"
        require_order(result[3], f"Parity {tag}{number} left upper",
                      f"Parity {tag}{number} left lower",
                      f"Parity {tag}{number} right upper",
                      f"Parity {tag}{number} right lower",
                      issue="#37 mirrored-margin parity")
    for number in range(5):
        require_order(result[3], f"Tail prose {number} upper",
                      f"Tail prose {number} lower", issue="#37 tail section")
    require(result[2]["columns"] == 2,
            "#37: columns stat should report the parity pages' real two columns")


def _semantic_three_col(result):
    """#37: the minority supplement orders three columns left to right."""
    for number in range(2):
        require_order(result[3],
                      f"Supplement {number} colone upper",
                      f"Supplement {number} colone lower",
                      f"Supplement {number} coltwo upper",
                      f"Supplement {number} coltwo lower",
                      f"Supplement {number} colthree upper",
                      f"Supplement {number} colthree lower",
                      issue="#37 three-column supplement")
    require(result[2]["columns"] == 3,
            "#37: columns stat should report the supplement's wider grid")


def _semantic_wrap(result):
    """#38: far-side lines currently follow the already-resumed body."""
    require_order(result[3], "Near upper", "Far upper", "Near lower", "Far lower",
                  "Resumed body", issue="#38 wrapped float")


def _semantic_twoup(result):
    """#39: the symmetric spread is a passing control, not a trigger for new work."""
    require_order(result[3], "Left spread opening", "Left spread middle", "Left spread closing",
                  "Right spread opening", "Right spread middle", "Right spread closing",
                  issue="#39 symmetric 2-up")


def _require_figure_output(result, caption):
    """Claiming labels must preserve the artwork once with the unclaimed caption as alt."""
    images = [image for name, html in result[4].items() if name.startswith("chap_")
              for image in re.findall(r"<img\b[^>]*>", html)]
    require(len(images) == 1 and f'alt="{caption}"' in images[0],
            f"#35: expected one intact figure with caption alt {caption!r}; observed {images}")


def _semantic_vector(result):
    """#35: vector labels currently appear as ordinary chapter text."""
    require_absent(result[3], "VECTORONE", "VECTORTWO", "VECTORTHREE", issue="#35 vector figure")
    require_order(result[3], "Vector body", "Figure 1.", "Vector continuation", issue="#35 vector prose")
    _require_figure_output(result, "Figure 1. Vector trend.")


def _semantic_raster(result):
    """#35: image overlays and nearby panel letters currently leak into the body."""
    labels = ("RASTERONE", "RASTERTWO", "A", "B")
    leaked = [label for label in labels if re.search(rf"\b{label}\b", result[3])]
    require(not leaked, f"#35 raster figure: overlay/panel labels in body flow: {leaked}")
    require_order(result[3], "Raster body", "Figure 2.", "Raster continuation", issue="#35 raster prose")
    _require_figure_output(result, "Figure 2. Raster panels.")


def _semantic_outdent(result):
    """#43: heads currently emit ahead of their page's opening body paragraph."""
    for number in range(3, 6):
        require_order(result[3], f"Outdent body {number} opening", f"Section {number} alpha",
                      f"Outdent body {number} middle", f"Section {number} beta",
                      f"Outdent body {number} closing", issue="#43 quorum-reaching outdent")


def _semantic_two_spans(result):
    """#43: the last span currently precedes the intervening body, even if heads remap."""
    for number in range(4):
        require_order(result[3], f"First span {number}", f"Between spans {number}",
                      f"Between body {number}", f"Last span {number}", f"Final body {number}",
                      issue="#43 two-span outdent")
        for label in (f"First span {number}", f"Last span {number}"):
            require(result[3].count(label) == 1, f"#43 duplicate/dropped full-width span: {label}")


def _semantic_consumers(result):
    """#43: audited consumers resolve on the shared reading column."""
    _, _, stats, body, htmls = result
    require(stats["columns"] == 1,
            f"outdent consumer doc must fold to one reading column: {stats['columns']}")
    require(stats["footnotes"] == 4,
            f"body-column notes must stay notes: {stats['footnotes']}")
    for number in range(2, 6):
        require_order(body, f"Head {number} alpha", f"Consumer body {number} alpha",
                      f"Figure {number}. View", f"Head {number} beta",
                      f"Consumer body {number} beta", f"Quoted consumer passage {number} one",
                      issue="#43 shared reading column")
    require_absent(body, "Consumer footnote", issue="#43 note swept to notes")
    notes = htmls.get("notes.xhtml", "")
    require("Consumer footnote 2" in notes and "Consumer footnote 5" in notes,
            "#43 body-column note must reach notes.xhtml")
    chaps = "".join(h for n, h in htmls.items() if n.startswith("chap_"))
    require(chaps.count("<h2") == 8,
            "#43 bold outdented heads must emit as headings through the shared column")
    require(chaps.count("<blockquote>") == 4,
            "#43 only the genuinely indented isolated block may quote — "
            "edge-flush prose must not see a collapsed-index offset")
    require(chaps.count('alt="Figure') == 4,
            "#43 outdented caption must adopt through the shared column band")
    require("<p>Consumer body 2 beta" in chaps,
            "#43 edge-flush body prose must not become a quotation")


def _semantic_chain(result):
    """#43: the transitive fold orders both strips inside the one body column."""
    require(result[2]["columns"] == 1,
            f"chained outdent edges fold to one reading column: {result[2]['columns']}")
    for number in range(6):
        require_order(result[3], f"Pt {number}a", f"Sec {number}a",
                      f"Chain body {number} alpha", f"Pt {number}b",
                      f"Sec {number}b", f"Chain body {number} beta",
                      f"Chain body {number} gamma",
                      issue="#43 chained outdent fold")


def _semantic_note_zone(result):
    """#43: note zones stay physical — neither side's top sweeps the other."""
    _, _, stats, body, htmls = result
    require(stats["columns"] == 1,
            f"note-zone fixture must fold to one reading column: {stats['columns']}")
    require(stats["footnotes"] == 8,
            f"merged tops would sweep two gammas and two labels: {stats['footnotes']}")
    for number in range(4):
        require_order(body, f"Head {number} alpha", f"Zone body {number} alpha",
                      f"Head {number} beta", f"Zone body {number} beta",
                      issue="#43 physical note zones")
    notes = htmls.get("notes.xhtml", "")
    for number in range(2):
        require(body.count(f"Zone body {number} gamma") == 1,
                "#43 the strip's note-top must not claim body blocks below it")
        require(f"Margin note {number}" in notes,
                "#43 a strip note must still file through its own zone")
    for number in range(2, 4):
        require(body.count(f"Late label {number}") == 1,
                "#43 the body column's note-top must not claim strip blocks")
        require(f"Margin note {number}" in notes,
                "#43 a strip note must file through its own zone")
    for number in range(4):
        require(f"Zone footnote {number}" in notes,
                "#43 body-column notes must reach notes.xhtml")


def _semantic_heading_run(result):
    """A genuine 2-col page keeps its columns when the left share stays low."""
    require(result[2]["columns"] == 2,
            f"a heading among real prose is not an outdent edge: {result[2]['columns']}")
    for number in range(4):
        require_order(result[3], f"Left head {number}", f"Left prose {number} upper",
                      f"Left prose {number} middle", f"Left prose {number} lower",
                      f"Left prose {number} closing", f"Right prose {number} upper",
                      f"Right prose {number} lower", issue="#43 two-column negative")


def _semantic_label_list(result):
    """A label column under the outdent share keeps its own column."""
    require(result[2]["columns"] == 2,
            f"labels under the outdent share keep their column: {result[2]['columns']}")
    for number in range(4):
        require_order(result[3], f"Label {number} 0", f"Label {number} 1",
                      f"Wrapped label {number} 2", f"Label {number} 3",
                      f"Label {number} 4", f"wrapped label {number} 5 line three",
                      f"Label {number} 6", f"List body {number} upper",
                      f"List body {number} lower", issue="#43 label-list negative")


def _semantic_span_only(result):
    """A span-voted edge without non-spanning evidence keeps its own sequence."""
    require(result[2]["columns"] == 2,
            f"spans alone cannot mark an outdent edge: {result[2]['columns']}")
    for number in range(4):
        require_order(result[3], f"Wide band {number} upper", f"Wide band {number} lower",
                      f"Span-only body {number} first", f"Span-only body {number} second",
                      issue="#43 span-only edge")


def _semantic_signature_margin(result):
    """The one-vote exclusion margin must preserve single-column order on all 29 pages."""
    require(result[2]["columns"] == 1, "signature exclusion must retain one reading column")
    require_absent(result[3], "Author et al.", issue="signature exclusion")
    for number in range(29):
        ordered = [f"Manuscript body {number} opening"]
        if number < 17:
            ordered.append(f"Aim {number:02d}")
        ordered.append(f"Manuscript body {number} middle")
        if number < 13:
            ordered.append(f"Data {number:02d}")
        ordered.append(f"Manuscript body {number} closing")
        require_order(result[3], *ordered, issue="signature exclusion one-vote margin")


def _semantic_heads_only(result):
    """#43: head-only quorum currently creates a phantom column and front-loads headings."""
    for number in range(3):
        require_order(result[3], f"Latent body {number} opening", f"Aim {number:02d}",
                      f"Latent body {number} middle", f"Data {number:02d}",
                      f"Latent body {number} closing", issue="#43 heads-only phantom edge")
    require(result[2]["columns"] == 1, "#43 head-only quorum must not create a reading column")


def _semantic_retained(result):
    """Negative controls retain margin prose and wide content once, in body reading order."""
    require_order(result[3], "Negative opening", "Normal margin prose", "continues as real content.",
                  "Full-width content", "Negative closing", issue="retained body negatives")
    for label in ("Normal margin prose", "Full-width content", "Negative closing"):
        require(result[3].count(label) == 1, f"negative control dropped/duplicated: {label}")


SEMANTICS = {
    "mixed_1col_plus_2col": _semantic_mixed,
    "mixed_parity_2col": _semantic_parity,
    "mixed_2col_plus_3col": _semantic_three_col,
    "magazine_wrap": _semantic_wrap,
    "scan_2up": _semantic_twoup,
    "vector_figure": _semantic_vector,
    "raster_figure": _semantic_raster,
    "outdented_heads": _semantic_outdent,
    "outdented_two_spans": _semantic_two_spans,
    "outdented_consumers": _semantic_consumers,
    "outdented_chain": _semantic_chain,
    "outdented_note_zone": _semantic_note_zone,
    "two_col_heading_run": _semantic_heading_run,
    "two_col_label_list": _semantic_label_list,
    "span_only_edge": _semantic_span_only,
    "signature_outdent_margin": _semantic_signature_margin,
    "heads_only_phantom": _semantic_heads_only,
    "retained_content": _semantic_retained,
}
SYMPTOMS = {
    "magazine_wrap": "#38 far-side wrap lines follow resumed full-width body",
}
SEMANTIC_CASES = [
    pytest.param(name, marks=pytest.mark.xfail(
        strict=True, raises=SemanticMismatch, reason=SYMPTOMS[name],
    )) if name in SYMPTOMS else name
    for name in GENERATORS
]

# Expected physical edge index -> reading-column index per fixture: outdent
# edges fold into the reading column on their right ([0, 0]); genuine
# columns and evidence-free edges keep their own identity.
READING_MAPS = {
    "outdented_heads": [0, 0],
    "outdented_two_spans": [0, 0],
    "outdented_consumers": [0, 0],
    "outdented_chain": [0, 0, 0],
    "outdented_note_zone": [0, 0],
    "heads_only_phantom": [0, 0],
    "signature_outdent_margin": [0],
    "two_col_heading_run": [0, 1],
    "two_col_label_list": [0, 1],
    "span_only_edge": [0, 1],
}


@pytest.mark.parametrize("source", READING_MAPS, indirect=True)
def test_reading_column_mapping(source):
    """The edge->reading-column map matches each fixture's intended fold."""
    name, path = source
    with fitz.open(path) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        boilerplate = _boilerplate_signatures(pages)
        edges = _doc_column_edges(pages, boilerplate)
        assert _reading_columns(pages, edges, boilerplate) == READING_MAPS[name]


@pytest.mark.parametrize("source", SEMANTIC_CASES, indirect=True)
def test_layout_semantic_baseline(converted):
    """Only an owned semantic check may xfail; lint and extraction are independent tests."""
    SEMANTICS[converted[0]](converted)
