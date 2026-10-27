"""Unit tests for the exegesis package's pure helpers — fabricated
block/line/span dicts, no PDF files needed."""
import fitz
import pytest

from exegesis import analyze, emit, render, text


def span(s, size=10.0, flags=0, font="helv"):
    return {"text": s, "size": size, "flags": flags, "font": font}


def line(s, size=10.0, flags=0, font="helv", x0=0, y0=0, x1=100, y1=12):
    return {"spans": [span(s, size, flags, font)], "bbox": (x0, y0, x1, y1)}


def block(lines, x0=0, y0=0, x1=100, y1=12):
    return {"type": 0, "lines": lines, "bbox": (x0, y0, x1, y1)}


# --- _signature / boilerplate normalization ---------------------------

def test_signature_ignores_digits_and_case():
    assert analyze._signature("VOL. 330, No. 12") == analyze._signature("vol 340 no 5")


def test_signature_strips_punctuation():
    assert analyze._signature("Science — Dec. 2010!") == "sciencedec"


# --- _join_lines --------------------------------------------------------

def test_join_lines_merges_soft_hyphen():
    assert text._join_lines(["a multi-", "column layout"]) == "a multicolumn layout"


def test_join_lines_merges_drop_cap():
    assert text._join_lines(["T", "he cat"]) == "The cat"


@pytest.mark.parametrize("letter", ["I", "A"])
def test_join_lines_keeps_real_single_letters(letter):
    assert text._join_lines([letter, "cat"]) == f"{letter} cat"


def test_join_lines_merges_lowercase_hyphen_tail():
    # only [a-z]-$ merges; this documents that "Pre-" joins too (the 'e' is
    # lowercase), so a deliberately-hyphenated prefix loses its hyphen
    assert text._join_lines(["Pre-", "existing"]) == "Preexisting"
    assert text._join_lines(["Section A-", "1"]) == "Section A- 1"


# --- heading shape ------------------------------------------------------

def test_heading_rejects_short_long_and_sentences():
    assert not render._looks_like_heading("abc")
    assert not render._looks_like_heading("x" * 130)
    assert not render._looks_like_heading("This is a sentence.")
    assert not render._looks_like_heading("Watson et al. reported")
    assert render._looks_like_heading("Materials and Methods")


def test_heading_rejects_footnote_marker_openers():
    assert not render._looks_like_heading("*indicates the marked entries")
    assert not render._looks_like_heading("†equal contribution")


def test_heading_level_thresholds():
    assert render._heading_level(line("Big Title", size=18), 10) == 1
    assert render._heading_level(line("Section", size=14), 10) == 2
    assert render._heading_level(line("body text", size=10.5), 10) is None
    assert render._heading_level(
        line("Bold bit", size=10.6, font="Helvetica-Bold"), 10
    ) == 2


def test_standalone_heading_levels():
    assert render._standalone_heading_level(block([line("Title", size=17)]), 10) == 1
    assert render._standalone_heading_level(block([line("Section", size=11)]), 10) == 2
    assert render._standalone_heading_level(
        block([line("Bold intro", size=10, flags=16)]), 10
    ) == 2
    assert render._standalone_heading_level(block([line("plain", size=10)]), 10) is None
    multi_big = block([line("Part one of", size=17), line("the title", size=17)])
    assert render._standalone_heading_level(multi_big, 10) == 1
    multi_bold = block([line("A bold", size=10, flags=16), line("heading", size=10, flags=16)])
    assert render._standalone_heading_level(multi_bold, 10) == 2
    mixed = block([line("big", size=17), line("small", size=10)])
    assert render._standalone_heading_level(mixed, 10) is None


# --- section labels / front matter -------------------------------------

def test_section_label_split():
    assert render._section_label("References and Notes 1. A. Author") == (
        "References and Notes",
        "1. A. Author",
    )
    assert render._section_label("References cited below") is None
    assert render._section_label("Appendix") == ("Appendix", "")
    assert render._section_label("No such heading here") is None


def test_authorish():
    assert render._authorish("M. A. Killingsworth and D. T. Gilbert")
    assert render._authorish("A. One, B. Two, C. Three")
    assert not render._authorish("The results were significant.")
    assert not render._authorish("ab")


def test_valid_title_rejects_junk():
    assert emit._valid_title("A Wandering Mind")
    assert not emit._valid_title("932 932..932")
    assert not emit._valid_title("")


# --- columns ------------------------------------------------------------

def _xb(x0, x1=None, y0=0, y1=10):
    return {"type": 0, "bbox": (x0, y0, x1 if x1 is not None else x0 + 10, y1), "lines": []}


def test_column_edges_cluster_min_two():
    items = [_xb(72), _xb(73), _xb(340), _xb(341), _xb(200)]
    assert analyze._column_edges(items) == [72.5, 340.5]


def test_column_edges_falls_back_to_zero():
    assert analyze._column_edges([_xb(72)]) == [0.0]


def test_is_spanning():
    edges = [72, 340]
    assert analyze._is_spanning(_xb(72, 560), edges)
    assert not analyze._is_spanning(_xb(72, 300), edges)


def test_covered_cols():
    edges = [72, 340]
    assert analyze._covered_cols(_xb(72, 560), edges, 612) == [0, 1]
    assert analyze._covered_cols(_xb(72, 300), edges, 612) == [0]
    assert analyze._covered_cols(_xb(345, 560), edges, 612) == [1]


# --- rotated tables / clips --------------------------------------------

def _tall(x, y0=100, y1=400, w=10):
    return {"type": 0, "bbox": (x, y0, x + w, y1), "lines": []}


def test_rotated_table_region_needs_three_shared():
    three = [_tall(50), _tall(80), _tall(110)]
    r = analyze._rotated_table_region(three)
    assert r == fitz.Rect(50, 100, 120, 400)
    assert analyze._rotated_table_region(three[:2]) is None
    wide = [{"type": 0, "bbox": (0, 0, 400, 30), "lines": []} for _ in range(3)]
    assert analyze._rotated_table_region(wide) is None


def test_rotated_table_region_disjoint_y_rejected():
    far = [_tall(50), _tall(80), _tall(110, y0=500, y1=800)]
    assert analyze._rotated_table_region(far) is None


def test_rotate_clip():
    rect = fitz.Rect(100, 50, 500, 300)
    mb = fitz.Rect(0, 0, 612, 792)
    assert analyze._rotate_clip(rect, 0, mb) == rect
    assert analyze._rotate_clip(rect, 90, mb) == fitz.Rect(50, 112, 300, 512)
    assert analyze._rotate_clip(rect, 270, mb) == fitz.Rect(492, 100, 742, 500)
    assert analyze._rotate_clip(rect, 180, mb) == fitz.Rect(112, 492, 512, 742)


# --- footnotes ----------------------------------------------------------

def test_sup_marker():
    mixed = {
        "type": 0,
        "lines": [
            {"spans": [span("3", 7), span("note text", 8.5)], "bbox": (0, 0, 1, 1)}
        ],
        "bbox": (0, 0, 1, 1),
    }
    assert analyze._sup_marker(mixed, 10) == 3
    b2 = block([line("12'", size=7)])
    assert analyze._sup_marker(b2, 10) == 12
    b3 = block([line("3", size=10)])
    assert analyze._sup_marker(b3, 10) is None
    b4 = block([line("text", size=7)])
    assert analyze._sup_marker(b4, 10) is None


def test_footnote_tops_gap_detection():
    body = block([line("body", size=10)], x0=72, y0=100, y1=112)
    note = block([line("small note", size=8.5)], x0=72, y0=600, y1=612)
    tops = analyze._footnote_tops([body, note], [72], 10, 800, 12)
    assert tops == {0: 600}


# --- style helpers ------------------------------------------------------

def test_span_bold_flags_and_names():
    assert text._span_is_bold(span("x", flags=16))
    assert text._span_is_bold(span("x", font="Helvetica-Bold"))
    assert text._span_is_bold(span("x", font="ABCDEE+TimesNewRoman,Bold"))
    assert text._span_is_bold(span("x", font="FancyFont.B"))
    assert not text._span_is_bold(span("x"))


def test_span_italic_names():
    assert text._span_is_italic(span("x", font="Helvetica-Oblique"))
    assert text._span_is_italic(span("x", font="TimesNewRoman,Italic"))
    assert not text._span_is_italic(span("x"))


def test_first_line_indent():
    b = block([line("indented", x0=80)], x0=72)
    assert text._first_line_indent(b) == 8


def test_block_median_size():
    b = block([line("a", size=10), line("bb", size=12), line("ccc", size=8)])
    assert text._block_median_size(b) == 10
