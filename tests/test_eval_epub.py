"""Round-trip tests for scripts/eval_epub.py: build a synthetic PDF with
fitz, convert it, and run the eval CLI as a subprocess — pinning the
exit-code contract (0 clean / 1 High findings / 2 operational) so the
CI gate can't drift silently."""
import subprocess
import sys
from pathlib import Path

import fitz

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


def _run(pdf, epub):
    return subprocess.run([sys.executable, str(EVAL), str(pdf), str(epub)],
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


def test_eval_non_english_bails_2(tmp_path):
    """A de conversion stamps dc:language=de — the eval must bail as
    operational rather than grade against English patterns."""
    pdf = tmp_path / "de.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "de.epub"
    convert(pdf, epub, lang="de")
    r = _run(pdf, epub)
    assert r.returncode == 2, r.stdout + r.stderr
    assert "English-only" in r.stdout


def test_eval_non_zip_epub_exits_2(tmp_path):
    pdf = tmp_path / "clean.pdf"
    _clean_pdf(pdf)
    epub = tmp_path / "bad.epub"
    epub.write_bytes(b"definitely not a zip archive")
    r = _run(pdf, epub)
    assert r.returncode == 2, r.stdout + r.stderr
