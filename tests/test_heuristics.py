"""Unit tests for the exegesis package's pure helpers — fabricated
block/line/span dicts, no PDF files needed."""
import fitz
import pytest

from exegesis import analyze, emit, render, text
from exegesis.lang import EN


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


# --- _sanitize_text -------------------------------------------------------

def test_sanitize_text_strips_full_xml_invalid_set():
    """Not just C0: lone surrogates and U+FFFE/U+FFFF are outside the
    XML 1.0 Char production too (broken ToUnicode maps can emit them),
    while XML-legal DEL and \\t\\n\\r survive."""
    assert (
        text._sanitize_text("x\x07\ud800\udfff\ufffe\uffffy") == "xy"
    )
    assert text._sanitize_text("keep\x7f me\t\n\r") == "keep\x7f me\t\n\r"


def test_span_is_near_white_channel_floor():
    """The invisible-text trigger is per-channel: saturated colors whose
    numeric value exceeds the floor (a dark blue channel) still count as
    visible, and the 1.25+ alpha-packed int normalizes the same."""
    assert text._span_is_near_white(0xFFFFFF)
    assert text._span_is_near_white(-1)  # signed RGBA white
    assert text._span_is_near_white(0xF2F5F7)
    assert not text._span_is_near_white(0xFEFF00)
    assert not text._span_is_near_white(0xEFEFEF)
    assert not text._span_is_near_white(0)


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
    """The clip map is ``page.rotation_matrix``: mediabox -> displayed space.

    Oracles are where a rect painted in mediabox coordinates (612x792)
    actually renders on the rotated page — verified against get_pixmap:
    the pixmap only contains the paint when clipped at these coordinates.
    """
    rect = fitz.Rect(100, 50, 500, 300)
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for rot, want in (
        (0, rect),
        (90, fitz.Rect(492, 100, 742, 500)),
        (180, fitz.Rect(112, 492, 512, 742)),
        (270, fitz.Rect(50, 112, 300, 512)),
    ):
        page.set_rotation(rot)
        assert analyze._rotate_clip(rect, page.rotation_matrix) == want


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


def test_strip_dead_fn_links():
    live = '<sup><a href="notes.xhtml#fn3">3</a></sup>'
    dead = '<sup><a href="notes.xhtml#fn2010">2010</a></sup>'
    assert render._strip_dead_fn_links(live, {3}) == live
    assert render._strip_dead_fn_links(dead, {3}) == "<sup>2010</sup>"
    frag = f"see {live} and also {dead} done"
    assert (
        render._strip_dead_fn_links(frag, {3})
        == f"see {live} and also <sup>2010</sup> done"
    )
    # a 4-digit marker keeps linking only when its note exists
    assert render._strip_dead_fn_links(dead, {2010}) == dead


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


# --- grid tables --------------------------------------------------------


def _band(pairs, y, h=11):
    """A row band: ``[(line, block_id)]`` entries plus the y-extent."""
    return [[(line(s, size=8, x0=a, y0=y, x1=b, y1=y + h), bid)
             for (s, a, b), bid in pairs], y, y + h]


def test_grid_runs_pending_band_bridges_the_gap():
    """A pending wide band keeps the run's gap window alive — measuring
    max_gap only from the last committed band would close the run early
    and sever the trailing rows into a second run."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    runs = analyze._grid_runs(
        [
            _band(cells, 0),
            _band(cells, 16),
            _band([(("section", 10, 230), 3)], 34),   # pending (wide)
            _band(cells, 66),
        ],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 1 and len(runs[0]) == 4


def test_grid_runs_second_pending_closes_run():
    """One wide band is a colspan candidate; a second consecutive one
    means prose is filling the gap between two grid fragments — the
    run closes so the paragraph lines stay text instead of committing
    as a run of colspan rows."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    wide1 = _band([(("line one", 10, 230), 3)], 34)
    wide2 = _band([(("line two", 10, 230), 4)], 50)
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), wide1, wide2,
         _band(cells, 68)],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 2
    assert [len(r) for r in runs] == [2, 1]
    assert all(
        b is not wide1 and b is not wide2 for run in runs for b in run
    )


def test_grid_runs_emitted_bands_keep_y_order():
    """A pending divider flushed by a resuming band lands after stubs
    committed while it was held — without sorting, its colspan row
    would emit after the wrapped-cell text that follows it visually."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    pending = _band([(("divider", 10, 230), 3)], 32)
    stub = _band([(("cont", 150, 200), 4)], 48)
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), pending, stub,
         _band(cells, 64)],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 1
    assert [b[1] for b in runs[0]] == [0, 16, 32, 48, 64]


def test_grid_runs_pending_beyond_union_not_held():
    """A wide band covering no recovered column can never emit a colspan
    cell — committing it would claim its block for a row that produces
    nothing, silently dropping the text; it stays prose."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    stray = _band([(("far right", 400, 600), 3)], 32)
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), stray, _band(cells, 48)],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 1
    assert all(b is not stray for b in runs[0])
    assert len(runs[0]) == 3


def test_grid_runs_gap_from_latest_kept_y():
    """The gap measures from the later of the last committed band and
    the pending one — a stub committed after a pending extends the
    kept-band bottom, and measuring from the pending overstates the
    gap by up to a row's height and severs the run."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    pending = _band([(("divider", 10, 230), 3)], 32)   # y1 = 43
    stub = _band([(("cont", 150, 200), 4)], 48)        # y1 = 59
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), pending, stub,
         _band(cells, 90)],
        min_gutter=6,
        max_gap=36,
    )
    # true gap from the stub's y1 is 31 ≤ 36; the pending's y1 says 47
    assert len(runs) == 1


def test_cells_cols_merges_overlapping_assigns():
    """A wide internal gap splits '3.2   (0.4–0.9)' into two band cells
    that both cover the same column — they must merge into one assign
    or the row emits more <td>s than the table has columns."""
    cols = [(0, 100), (140, 240)]
    band = _band(
        [
            (("3.2", 10, 30), 1),
            (("(0.4-0.9)", 60, 95), 1),
            (("b", 140, 240), 2),
        ],
        0,
    )
    assigns = analyze._cells_cols(band, cols, 6)
    assert [(col, span) for col, span, _c in assigns] == [(0, 1), (1, 1)]
    assert len(assigns[0][2][2]) == 2


def test_table_caption_below_legend_belongs_to_next_run():
    """A 'Table N' band between two stacked grids is the lower table's
    legend (legends sit above their table) — the upper run must not
    adopt it, while a lone run may still take a below-run caption."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    cap = _band([(("Table 5  Results", 0, 100), 3)], 48)
    run_a = [_band(cells, 0), _band(cells, 16), _band(cells, 32)]
    run_b = [_band(cells, 64), _band(cells, 80), _band(cells, 96)]
    sbands = run_a + [cap] + run_b
    runs = [run_a, run_b]
    assert analyze._table_caption(sbands, run_a, runs, 12) is None
    assert analyze._table_caption(sbands, run_b, runs, 12) is cap
    # with no following run, the below legend is still a caption
    assert analyze._table_caption(sbands, run_a, [run_a], 12) is cap


def test_upright_lines_keeps_narrow_cells():
    """A 1-char line is taller than wide but reports dir (1, 0) — the
    direction check keeps it where an aspect-ratio proxy dropped it."""
    upright = line("7", size=8, x0=0, y0=0, x1=5, y1=11)
    upright["dir"] = (1.0, 0.0)
    rotated = line("v", size=8, x0=5, y0=0, x1=9, y1=30)
    rotated["dir"] = (0.0, 1.0)
    out = analyze._upright_lines([block([upright, rotated], 0, 0, 9, 30)])
    assert [l["spans"][0]["text"] for l, _ in out] == ["7"]


def test_grid_runs_caption_band_never_joins():
    """A 'Table N …' band — even a multi-cell one sharing text with a
    foreign line — is the caption machinery's to claim: it must neither
    seed nor join a run, or the legend and its band-mate mangle into
    <td> cells while _table_caption stays blind to it. A caption band
    is a boundary, so the open run closes at it."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    cap = _band(
        [
            (("Table 3  Results", 0, 100), 1),
            (("a sidenote", 140, 240), 3),
        ],
        32,
    )
    runs = analyze._grid_runs(
        [
            _band(cells, 0),
            _band(cells, 16),
            cap,
            _band(cells, 50),
        ],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 2
    assert all(b is not cap for run in runs for b in run)
    assert [len(r) for r in runs] == [2, 1]


def test_grid_runs_table_legend_not_folded():
    """A 'Table N' legend under the grid starting in a later column
    would pass the wrapped-cell fold (col 0 empty, strict subset) and
    merge into the last row's cell — it must stay a caption
    candidate instead."""
    cells = [
        (("a", 0, 60), 1),
        (("b", 100, 160), 2),
        (("c", 200, 260), 3),
    ]
    legend = _band([(("Table 3  Summary", 100, 160), 4)], 48)
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), _band(cells, 32), legend],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 1
    assert all(b is not legend for b in runs[0])


def test_grid_runs_stub_does_not_commit_pending():
    """A pending wide band is a colspan candidate only a resuming
    multi-cell band may commit — a narrow continuation stub must not
    flush it into the run, or intervening prose becomes a merged row."""
    cells = [(("a", 0, 100), 1), (("b", 140, 240), 2)]
    pending = _band([(("section", 10, 230), 3)], 32)   # wide → pending
    stub = _band([(("cont", 150, 200), 4)], 48)       # narrow stub
    runs = analyze._grid_runs(
        [_band(cells, 0), _band(cells, 16), pending, stub],
        min_gutter=6,
        max_gap=36,
    )
    assert len(runs) == 1
    assert stub in runs[0]
    assert all(b is not pending for b in runs[0])


def _edge_grid():
    """Blocks + run bands for a 3-col grid straddling a document edge —
    cell columns at 60-120/200-260/340-460 with the page edge at 300."""
    blocks, bands = [], []
    for ri, tag in enumerate(["head", "r1", "r2", "r3"]):
        y = 140 + ri * 14
        pairs = []
        for tag2, a, b in [(tag, 60, 120), ("m", 200, 260), ("v", 340, 460)]:
            l = line(tag2, size=8, x0=a, y0=y, x1=b, y1=y + 11)
            bl = block([l], a, y, b, y + 11)
            blocks.append(bl)
            pairs.append((l, id(bl)))
        bands.append([pairs, y, y + 11])
    return blocks, bands


def _region(run, blocks, sbands):
    return analyze._region_from_run(
        run, [run], blocks, sbands, [0.0, 300.0], 6.0, 12.0, True
    )


def test_edge_region_accepts_wrapped_claimable_caption():
    """A 2-line legend block claims whole; its neighbour stays out."""
    blocks, bands = _edge_grid()
    cap = block(
        [
            line("Table 3  Stats", size=10, x0=60, y0=118, x1=160, y1=129),
            line("and strata", size=10, x0=60, y0=129, x1=120, y1=140),
        ],
        60, 118, 160, 140,
    )
    fx = block(
        [line("foreign prose", size=10, x0=200, y0=150, x1=280, y1=161)],
        200, 150, 280, 161,
    )
    cap_l1, cap_l2 = cap["lines"]
    fx_l = fx["lines"][0]
    sbands = (
        [[[(cap_l1, id(cap))], 118, 129],
         [[(cap_l2, id(cap))], 129, 140],
         [[(fx_l, id(fx))], 150, 161]]
        + bands
    )
    region = _region(bands, blocks + [cap, fx], sbands)
    assert region is not None
    assert len(region.caption) == 2
    assert id(fx) not in region.claimed


def test_edge_region_rejects_mixed_caption_band():
    """A caption band sharing a foreign block is no valid legend — an
    edge-spanning grid must reject rather than emit uncaptioned."""
    blocks, bands = _edge_grid()
    cap = block(
        [line("Table 3  Stats", size=10, x0=60, y0=118, x1=160, y1=129)],
        60, 118, 160, 129,
    )
    fx = block(
        [line("foreign prose", size=10, x0=200, y0=120, x1=280, y1=131)],
        200, 120, 280, 131,
    )
    mixed = [[(cap["lines"][0], id(cap)), (fx["lines"][0], id(fx))], 118, 131]
    sbands = [mixed] + bands
    assert _region(bands, blocks + [cap, fx], sbands) is None
    # a claimable caption in the same position rescues it
    clean = [[(cap["lines"][0], id(cap))], 118, 129]
    sbands = [clean, [[(fx["lines"][0], id(fx))], 120, 131]] + bands
    assert _region(bands, blocks + [cap, fx], sbands) is not None


def test_caption_res_accept_double_space_legends():
    """Springer-style double-spaced legends match every caption
    pattern — the shared number/tail keeps the three regexes in step."""
    assert EN.table_caption.match("Table 9  Legend without colon")
    assert EN.caption.match("Table 9  Legend without colon")
    assert EN.figure_caption.match("Figure 3  A spaced caption")
    # single-spaced citations still don't match
    assert not EN.figure_caption.match("Figure 3 shows a trend")
    assert not EN.table_caption.match("Table 9 shows the legend")
