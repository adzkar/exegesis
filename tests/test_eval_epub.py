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
