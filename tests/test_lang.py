"""Language-profile coverage: ``convert(lang=...)`` drives furniture,
caption, and section-label detection plus EPUB language metadata.

Each non-English profile gets a synthetic paper exercising the same
signals the English suite uses — a captioned figure, a named section
heading, pattern furniture, and the language stamp on the output.
"""
import zipfile

import fitz
import pytest
from helpers import doc_html, lint_epub

from exegesis import convert
from exegesis.lang import EN, LANGS, LangProfile, get_lang

BODY = 10


def _save(doc, path):
    doc.save(path)
    doc.close()


def _page(doc):
    return doc.new_page(width=612, height=792)


def _noise_png(size=120):
    """A noisy pixmap whose PNG encoding exceeds MIN_IMAGE_BYTES."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, size, size))
    for x in range(size):
        for y in range(size):
            pix.set_pixel(
                x, y, ((x * y) % 256, (x * 3 + y) % 256, (x + y * 7) % 256)
            )
    return pix.tobytes("png")


def _figure_doc(caption):
    """One-page doc: intro prose, a figure, then its caption below."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Body text that introduces the figure and gives it context.",
        fontsize=BODY,
    )
    p.insert_image(fitz.Rect(72, 300, 300, 420), stream=_noise_png())
    p.insert_textbox(fitz.Rect(72, 430, 540, 460), caption, fontsize=BODY)
    return doc


def _convert_epub(doc, tmp_path, name, **kwargs):
    pdf = tmp_path / f"{name}.pdf"
    _save(doc, pdf)
    out = tmp_path / f"{name}.epub"
    convert(pdf, out, **kwargs)
    html = " ".join(doc_html(out).values())
    return out, html


def test_german_figure_caption(tmp_path):
    """'Abb. 1' is a figure label in German — and only there."""
    for lang, want in (("de", "Abb. 1"), ("en", ""), ("fr", ""), ("es", "")):
        doc = _figure_doc("Abb. 1  Ergebnisse der Messung.")
        _save(doc, tmp_path / f"fig_{lang}.pdf")
        out = tmp_path / f"fig_{lang}.epub"
        convert(tmp_path / f"fig_{lang}.pdf", out, lang=lang)
        figs = "\n".join(h for h in doc_html(out).values() if "<img" in h)
        if want:
            assert 'alt="Abb. 1' in figs, f"{lang}: caption not adopted"
        else:
            assert 'alt=""' in figs, f"{lang}: foreign caption adopted"


def test_french_section_and_furniture(tmp_path):
    """'Bibliographie' heads a section; 'Année' running head drops."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 30, 540, 50), "Année 2024 · Tome 9", fontsize=8
    )
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Texte français introduisant la bibliographie de cet article.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 500, 540, 520), "Bibliographie", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 530, 540, 560),
        "Dupont, A. et Martin, B. Titre de l'ouvrage.",
        fontsize=BODY,
    )
    out, html = _convert_epub(doc, tmp_path, "fr", lang="fr")
    assert lint_epub(out) == []
    assert 'xml:lang="fr"' in html
    assert "<h2" in html and "Bibliographie" in html
    assert "Année 2024" not in html  # pattern furniture dropped


def test_spanish_caption_and_abstract_title(tmp_path):
    """'Fig.' works in Spanish too; the injected abstract is localized."""
    doc = fitz.open()
    p = _page(doc)
    # small-font inset block in the top half → injected abstract heading
    p.insert_textbox(
        fitz.Rect(110, 90, 500, 220),
        "Este es el resumen del artículo, un bloque con sangría y "
        "letra más pequeña que el cuerpo principal del texto.",
        fontsize=8,
    )
    p.insert_textbox(
        fitz.Rect(72, 400, 540, 640),
        "El cuerpo del artículo continúa aquí con texto normal y "
        "suficientes líneas para fijar el tamaño modal del cuerpo. " * 3,
        fontsize=BODY,
    )
    _out, html = _convert_epub(doc, tmp_path, "es_abs", lang="es")
    assert "<h2" in html and "Resumen" in html


def test_german_notes_chapter_title(tmp_path):
    """A 'Danksagung' opener lands in a notes chapter titled 'Anmerkungen'."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Körpertext der Arbeit mit normalem Inhalt vor der Notiz.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 600, 540, 660),
        "Danksagung. Die Autoren danken der Fördergemeinschaft.",
        fontsize=9,
    )
    out, _html = _convert_epub(doc, tmp_path, "de_notes", lang="de")
    z = zipfile.ZipFile(out)
    assert "EPUB/notes.xhtml" in z.namelist()
    notes = z.read("EPUB/notes.xhtml").decode()
    assert "Anmerkungen" in notes
    assert 'lang="de"' in notes


def test_lang_metadata_stamp(tmp_path):
    """Every chapter and the OPF carry the selected language."""
    doc = _figure_doc("Abb. 2  Weitere Messung.")
    out, html = _convert_epub(doc, tmp_path, "de_meta", lang="de")
    z = zipfile.ZipFile(out)
    opf = z.read("EPUB/content.opf").decode()
    assert '<dc:language>de</dc:language>' in opf
    assert 'xml:lang="de"' in html


def test_get_lang_resolution(tmp_path):
    """Code strings and profiles resolve; unknown codes raise."""
    assert get_lang("fr") is LANGS["fr"]
    assert get_lang(EN) is EN
    assert get_lang("de").abstract_title == "Zusammenfassung"
    with pytest.raises(ValueError, match="unsupported language"):
        get_lang("ja")
    with pytest.raises(ValueError, match="unsupported language"):
        convert(tmp_path / "fig_en.pdf", tmp_path / "x.epub", lang="ja")


def test_custom_profile_accepted(tmp_path):
    """A hand-built LangProfile flows through convert end to end."""
    doc = _figure_doc("Abb. 1  Ergebnisse der Messung.")
    _out, html = _convert_epub(doc, tmp_path, "custom", lang=LANGS["de"])
    assert isinstance(EN, LangProfile)
    assert 'xml:lang="de"' in html


def test_english_default_unchanged(tmp_path):
    """Omitting lang keeps the English profile and 'en' metadata."""
    doc = _figure_doc("Figure 1. A caption.")
    _out, html = _convert_epub(doc, tmp_path, "en_def")
    assert 'xml:lang="en"' in html
    assert 'alt="Figure 1. A caption."' in html
