"""Round-trip tests for scripts/eval_epub.py: build a synthetic PDF with
fitz, convert it, and run the eval CLI as a subprocess — pinning the
exit-code contract (0 clean / 1 High findings / 2 operational) so the
CI gate can't drift silently."""
import re
import subprocess
import sys
import zipfile
from pathlib import Path

import fitz
from builders import image, text
from furniture_fixtures import (
    NORMAL_MARGIN_TEXT,
    dual_margin_strips,
    fused_running_head_title,
    long_repeated_running_head,
    repeated_bottom_foot,
    rotated_margin_stamp,
    rotated_page_upright_margin,
    rotated_table_margin_cells,
    rotated_table_staggered_cells,
    stacked_fused_heads,
)

from exegesis import convert

EVAL = Path(__file__).resolve().parent.parent / "scripts" / "eval_epub.py"
W, H, BODY = 612, 792, 10


def _page(doc):
    return doc.new_page(width=W, height=H)


def _clean_pdf(path):
    """An h1 title plus body paragraphs — nothing the eval should flag."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 90, 540, 130),
                     "A Clean Synthetic Study", fontsize=18)
    p.insert_textbox(fitz.Rect(72, 180, 540, 420),
                     "Body text of a single column document with several "
                     "sentences. More sentences follow so the page carries "
                     "enough words for the coverage check.", fontsize=BODY)
    doc.save(str(path))
    doc.close()


def _de_legend_pdf(path):
    """A standalone `Tabelle 1.` legend block mid-flow — German caption
    vocabulary the en profile can't see."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 90, 540, 130),
                     "Eine Synthetische Studie", fontsize=18)
    p.insert_textbox(fitz.Rect(72, 180, 540, 300),
                     "Fliesstext mit mehreren Saetzen. Noch mehr Text "
                     "folgt hier fuer die Wortabdeckung.", fontsize=BODY)
    p.insert_textbox(fitz.Rect(72, 320, 540, 350),
                     "Tabelle 1.", fontsize=BODY)
    p.insert_textbox(fitz.Rect(72, 370, 540, 470),
                     "Weiterer Fliesstext nach der Legende, damit der "
                     "Tabellenblock mitten im Textfluss bleibt.",
                     fontsize=BODY)
    doc.save(str(path))
    doc.close()


def _delete_epub_text(epub: Path, phrase: str):
    """Remove `phrase` from the chapter XHTML — simulates a conversion
    that silently lost edge prose the evaluator should have expected."""
    with zipfile.ZipFile(epub) as zin:
        members = {name: zin.read(name) for name in zin.namelist()}
    for name, data in members.items():
        if name.rsplit("/", 1)[-1].startswith("chap_"):
            members[name] = data.replace(phrase.encode(), b"")
    with zipfile.ZipFile(epub, "w") as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def _set_language(epub: Path, code: str):
    """Rewrite `<dc:language>` inside content.opf — simulates an EPUB
    whose language `get_lang` doesn't know (the converter itself
    refuses unknown codes)."""
    with zipfile.ZipFile(epub) as z:
        members = {n: z.read(n) for n in z.namelist()}
    opf_name = next(n for n in members if n.endswith("content.opf"))
    members[opf_name] = re.sub(
        r"<dc:language[^>]*>[^<]*</dc:language>",
        f"<dc:language>{code}</dc:language>",
        members[opf_name].decode()).encode()
    with zipfile.ZipFile(epub, "w") as z:
        for n, data in members.items():
            z.writestr(n, data)


def _raster_margin_legend_pdf(path):
    """A rotated raster table beside a margin `Table 1.` strip — the
    legend binds into the raster's `alt`, so the eval's bound-legend
    arm must grade it 'rasterized' and excuse its words, not flag a
    'missing' legend or genuine loss on correct output."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(200, 200, 540, 300),
        "Body prose fills the column and keeps ordinary reading order "
        "through the page so the margin strip beside it stays "
        "secondary.", fontsize=BODY)
    p.insert_textbox(
        fitz.Rect(200, 400, 540, 500),
        "More body prose keeps the column reading so the margin "
        "strip beside it stays secondary matter.", fontsize=BODY)
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(200, 120, 540, 185),
        "Body prose fills the column and keeps ordinary reading order "
        "above the table.", fontsize=BODY)
    for i, x in enumerate((200, 260, 320)):
        p.insert_textbox(fitz.Rect(x, 380, x + 15, 520),
                         f"r1c{i} r2c{i} r3c{i}", fontsize=10)
    text(p, 36, 370, "Table 1. Rotated margin legend.", size=9)
    text(p, 36, 420, "Note: *p<0.1;", size=9)
    p.insert_textbox(
        fitz.Rect(200, 560, 540, 625),
        "Body prose fills the column and keeps ordinary reading order "
        "below the table.", fontsize=BODY)
    doc.save(str(path))
    doc.close()


def _fig_pdf(path):
    """An embedded raster with two panel labels and a caption — the
    figure claim rasterizes the labels to pixels and absorbs the
    source image inside `images/fig_*`."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 90, 540, 130),
                     "A Figure Region Study", fontsize=18)
    p.insert_textbox(fitz.Rect(72, 560, 540, 620),
                     "Body prose follows the captioned figure region "
                     "with enough words for coverage.", fontsize=BODY)
    image(p, (180, 290, 440, 470))
    text(p, 210, 350, "ZZPANELA")
    text(p, 210, 400, "ZZPANELB")
    text(p, 72, 520, "Figure 1. Paired panels.")
    doc.save(str(path))
    doc.close()


def _delete_epub_fig_refs(epub: Path):
    """Remove every `images/fig_*` <img> reference from chapter XHTML —
    the PNG stays packaged but unreferenced, so the eval replays a
    committed region whose raster evidence is missing."""
    with zipfile.ZipFile(epub) as zin:
        members = {name: zin.read(name) for name in zin.namelist()}
    for name, data in members.items():
        if name.rsplit("/", 1)[-1].startswith("chap_"):
            members[name] = re.sub(
                rb'<img[^>]*src="images/fig_[^"]*"[^>]*/?>', b"", data)
    with zipfile.ZipFile(epub, "w") as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def _run(pdf, epub, *args):
    return subprocess.run(
        [sys.executable, str(EVAL), str(pdf), str(epub), *args],
        check=False, capture_output=True, text=True)


def test_eval_clean_conversion_exits_0(tmp_path):
    pdf = tmp_path / "clean.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "clean.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_missing_sections_exits_1(tmp_path):
    """Body-size non-bold `N. Title` lines demote to plain paragraphs —
    two of them trip the >=2 High gate on missing TOC entries."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 90, 540, 130),
                     "A Paper With Demoted Headings", fontsize=18)
    p.insert_textbox(fitz.Rect(72, 180, 540, 500),
                     "1. Introduction\nWe wrote some body text here for "
                     "realism.\n2. Methods\nMore body text follows in the "
                     "methods section.", fontsize=BODY)
    pdf = tmp_path / "demoted.pdf"
    doc.save(str(pdf))
    doc.close()
    epub = tmp_path / "demoted.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "missing" in r.stdout.lower()


def test_eval_localized_legends_de(tmp_path):
    """The `Tabelle 1.` legend grades under the dc:language-resolved de
    profile — a disposition row names it — while `--lang en` leaves the
    German label uncollected and sees no table at all."""
    pdf = tmp_path / "de.pdf"
    _de_legend_pdf(pdf)
    epub = tmp_path / "de.epub"
    convert(pdf, epub, lang="de")
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Table 1:" in r.stdout
    r_en = _run(pdf, epub, "--lang", "en")
    assert r_en.returncode == 0, r_en.stdout + r_en.stderr
    assert "Table 1:" not in r_en.stdout


def test_eval_unsupported_language_bails_2(tmp_path):
    """dc:language=ja has no LangProfile — the eval must bail as
    operational rather than grade against English patterns."""
    pdf = tmp_path / "clean.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "ja.epub"
    convert(pdf, epub)
    _set_language(epub, "ja")
    r = _run(pdf, epub)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unsupported language" in r.stdout


def test_eval_lang_flag_bad_code_bails_2(tmp_path):
    """`--lang tlh` resolves no profile either — same operational bail."""
    pdf = tmp_path / "clean.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "clean.epub"
    convert(pdf, epub)
    r = _run(pdf, epub, "--lang", "tlh")
    assert r.returncode == 2, r.stdout + r.stderr
    assert "unsupported language" in r.stdout


def test_eval_non_zip_epub_exits_2(tmp_path):
    pdf = tmp_path / "clean.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "bad.epub"
    epub.write_bytes(b"definitely not a zip archive")
    r = _run(pdf, epub)
    assert r.returncode == 2, r.stdout + r.stderr


def test_eval_rotated_stamp_is_excused(tmp_path):
    """The rotated margin stamp is furniture by design — the eval must
    excuse it rather than report the paragraph's intactness as loss
    (#34)."""
    pdf = tmp_path / "stamp.pdf"
    rotated_margin_stamp(pdf)
    epub = tmp_path / "stamp.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_long_repeated_head_is_excused(tmp_path):
    """The five-line repeated head rides the widened signature excuse —
    the converter and evaluator must agree it is furniture (#34)."""
    pdf = tmp_path / "head.pdf"
    long_repeated_running_head(pdf)
    epub = tmp_path / "head.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_upright_edge_text_counts_as_expected(tmp_path):
    """Upright margin prose on /Rotate pages is kept content — the eval
    mirrors the veto, so it lands on the expected side and still counts
    toward coverage (#34)."""
    pdf = tmp_path / "upright.pdf"
    rotated_page_upright_margin(pdf)
    epub = tmp_path / "upright.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_omitted_edge_prose_still_reported(tmp_path):
    """Edge prose the converter keeps is expected in the EPUB — deleting
    it must surface as genuine loss, never ride the furniture excuse
    (#34)."""
    pdf = tmp_path / "edge.pdf"
    rotated_margin_stamp(pdf)
    epub = tmp_path / "edge.epub"
    convert(pdf, epub)
    _delete_epub_text(epub, NORMAL_MARGIN_TEXT)
    r = _run(pdf, epub)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "margin" in r.stdout


def test_eval_margin_cells_rasterize_excused(tmp_path):
    """Margin-adjacent rotated-table cells are excused only through the
    raster evidence — the mirrored clip keeps them out of the strip arm
    (#34)."""
    pdf = tmp_path / "cells.pdf"
    rotated_table_margin_cells(pdf)
    epub = tmp_path / "cells.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_fused_head_is_excused(tmp_path):
    """The split-off running head is furniture by design — the eval
    mirrors the prefix split so its words excuse while the freed title
    and body still count toward coverage (#40)."""
    pdf = tmp_path / "fused.pdf"
    fused_running_head_title(pdf)
    epub = tmp_path / "fused.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_stacked_fused_heads_are_excused(tmp_path):
    """Two independently-recurring heads fused ahead of the title split
    one at a time — the second head's remainder is itself signature
    furniture, so convert drops it and the eval must excuse its words
    rather than report genuine loss on a correct conversion (#46)."""
    pdf = tmp_path / "stacked.pdf"
    stacked_fused_heads(pdf)
    epub = tmp_path / "stacked.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def _stub_page(blocks):
    """A stand-in `page` for `_page_ctx`: dict extraction plus the
    rect/mediabox/rotation attributes the clip and strip arms read."""
    class _Page:
        rect = fitz.Rect(0, 0, W, H)
        mediabox = fitz.Rect(0, 0, W, H)
        rotation = 0

        def get_text(self, _kind):
            return {"blocks": blocks}

    return _Page()


def test_page_ctx_strip_clip_reads_post_split_blocks():
    """`_strip_clip` must see the post-split block stream
    `order._ordered_blocks` does — a fused block's head lines can't
    inflate the clip's region union eval-side (#46)."""
    import importlib.util

    from exegesis.analyze import _signature
    from exegesis.lang import EN

    spec = importlib.util.spec_from_file_location("eval_epub", EVAL)
    eval_epub = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(eval_epub)

    # a fused head line rides atop a tall rotated column: pre-split the
    # whole block is a tall seed reaching up to y=95; post-split the
    # head detaches as furniture and the remainder's region starts at
    # the rotated cells' y=300
    head = {"spans": [{"text": "Strip Journal", "size": 9}],
            "bbox": (300, 95, 380, 104), "dir": (1, 0)}
    cells = [{"spans": [{"text": f"cell {i}", "size": 8}],
              "bbox": (300, 300 + i * 20, 320, 318 + i * 20),
              "dir": (0, -1)} for i in range(10)]
    fused = {"type": 0, "bbox": (300, 95, 380, 520),
             "lines": [head, *cells]}

    def tall(x0, x1):
        lines = [{"spans": [{"text": "cell", "size": 8}],
                  "bbox": (x0, 320 + i * 20, x1, 338 + i * 20),
                  "dir": (0, -1)} for i in range(10)]
        return {"type": 0, "bbox": (x0, 320, x1, 520), "lines": lines}

    page = _stub_page([fused, tall(20, 40), tall(560, 580)])
    ctx = eval_epub._page_ctx(page, {_signature("Strip Journal")}, EN)
    assert ctx.rtbl == fitz.Rect(20, 300, 580, 520)


def test_eval_bottom_footer_is_excused(tmp_path):
    """A repeated bottom running foot and a varying © line drop as
    furniture — the signature arm's `y0 > 0.80` and the pattern arm's
    `y0 > 0.93` bottom halves must excuse them, not grade them loss
    (#45)."""
    pdf = tmp_path / "foot.pdf"
    repeated_bottom_foot(pdf)
    epub = tmp_path / "foot.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_dual_margin_strips_are_dropped(tmp_path):
    """The all-margin band drops as furniture on both sides — the
    mirrored clip must refuse it the same way `_strip_clip` does, so
    the strips excuse through the furniture arm and no raster excuse
    is invented (#45)."""
    pdf = tmp_path / "dual.pdf"
    dual_margin_strips(pdf)
    epub = tmp_path / "dual.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_staggered_cells_rasterize_excused(tmp_path):
    """The staggered band — margin cells opening, body cells joining
    late — rasterizes whole; the mirrored clip legitimates it through
    band membership across ranges, and every cell excuses through the
    raster arm rather than furniture or loss (#45)."""
    pdf = tmp_path / "stag.pdf"
    rotated_table_staggered_cells(pdf)
    epub = tmp_path / "stag.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr


def test_eval_raster_margin_legend_grades_rasterized(tmp_path):
    """A margin legend bound into the raster's `alt` emits no visible
    text — the replayed `_bind_legend` arm must excuse its words and
    grade the `Table 1.` disposition 'rasterized', not 'missing'
    (#54)."""
    pdf = tmp_path / "ralt.pdf"
    _raster_margin_legend_pdf(pdf)
    epub = tmp_path / "ralt.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Table 1: rasterized" in r.stdout
    assert "legend absent" not in r.stdout
    assert "genuine loss 0" in r.stdout


def test_eval_figure_region_labels_are_excused(tmp_path):
    """Committed figure members emit only inside the `images/fig_`
    raster — the replayed `_page_claims` must excuse their words and
    the absorbed source image's digest, or a correct conversion flags
    genuine loss and a dropped raster (#35)."""
    pdf = tmp_path / "fig.pdf"
    _fig_pdf(pdf)
    epub = tmp_path / "fig.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "raster images" not in r.stdout


def test_eval_figure_region_missing_raster_withholds_excuse(tmp_path):
    """With the emitted fig reference deleted the replay still sees the
    region, so the excuse is withheld — the evidence-row Med and the
    label words' genuine loss must both surface (#35)."""
    pdf = tmp_path / "figgone.pdf"
    _fig_pdf(pdf)
    epub = tmp_path / "figgone.epub"
    convert(pdf, epub)
    _delete_epub_fig_refs(epub)
    r = _run(pdf, epub)
    assert r.returncode == 1, r.stdout + r.stderr
    assert "figure regions" in r.stdout
    assert "zzpanel" in r.stdout.lower()


def test_eval_committed_figure_page_is_capturable(tmp_path):
    """A >30-drawing page whose vectors commit to a figure region
    leaves the 'not capturable' list — the drawings emit inside the
    `images/fig_` raster (#35)."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(fitz.Rect(72, 90, 540, 130),
                     "A Vector Figure Study", fontsize=18)
    # a crossing grid — >30 connected vector ops in one region
    for i in range(20):
        y = 290 + i * 9
        p.draw_line((180, y), (440, y), color=(0.7, 0.7, 0.7))
    for i in range(20):
        x = 180 + i * 13
        p.draw_line((x, 290), (x, 470), color=(0.7, 0.7, 0.7))
    text(p, 210, 350, "ZZVECA")
    text(p, 210, 400, "ZZVECB")
    text(p, 72, 520, "Figure 1. Dense grid.")
    p.insert_textbox(fitz.Rect(72, 560, 540, 620),
                     "Body prose follows the vector figure region.",
                     fontsize=BODY)
    pdf = tmp_path / "vecfig.pdf"
    doc.save(str(pdf))
    doc.close()
    epub = tmp_path / "vecfig.epub"
    convert(pdf, epub)
    r = _run(pdf, epub)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "not capturable" not in r.stdout
