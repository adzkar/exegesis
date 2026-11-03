"""Language-profile coverage: ``convert(lang=...)`` drives furniture,
caption, and section-label detection plus EPUB language metadata.

Each non-English profile gets a synthetic paper exercising the same
signals the English suite uses — a captioned figure, a named section
heading, pattern furniture, and the language stamp on the output.
"""
import re
import zipfile
from dataclasses import replace

import fitz
import pytest
from helpers import doc_html, lint_epub

from exegesis import convert, text
from exegesis.lang import DE, EN, ES, FR, LANGS, LangProfile, get_lang

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


def test_french_longest_label_wins(tmp_path):
    """'Références bibliographiques' must reach the h2, not be eaten by the
    shorter 'Références' arm — ordered alternation, longest first."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Le texte de l'article précède les références ici.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 500, 540, 520),
        "Références bibliographiques",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 530, 540, 560),
        "Durand, P. et Moreau, S. Un titre.",
        fontsize=BODY,
    )
    _out, html = _convert_epub(doc, tmp_path, "fr_long", lang="fr")
    assert re.search(r"<h2[^>]*>Références bibliographiques<", html)
    # and the short form still heads its section
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200), "Même structure, étiquette courte.",
        fontsize=BODY,
    )
    p.insert_textbox(fitz.Rect(72, 500, 540, 520), "Références", fontsize=BODY)
    p.insert_textbox(
        fitz.Rect(72, 530, 540, 560), "Durand, P. Un autre titre.",
        fontsize=BODY,
    )
    _out, html = _convert_epub(doc, tmp_path, "fr_short", lang="fr")
    assert re.search(r"<h2[^>]*>Références<", html)


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


def test_german_note_start_plural_acknowledgment():
    """'Die Autoren danken …' is the canonical plural acknowledgment —
    the der/die/den x dankt/danken grid covers it like EN/FR/ES's
    plural arms do; article-less 'Autoren danken' stays unmatched."""
    assert DE.note_start.match("Die Autoren danken der DFG")
    assert DE.note_start.match("Der Autor dankt den Gutachtern")
    assert DE.note_start.match("Die Autorinnen danken der DFG")
    assert not DE.note_start.match("Autoren danken niemandem")


def test_german_furniture_jahrg_abbreviation():
    """Running heads abbreviate 'Jahrgang' as 'Jahrg.' — both forms
    flag edge furniture."""
    assert DE.furniture.search("Jahrg. 89")
    assert DE.furniture.search("Jahrgang 89")


def test_german_furniture_bd_s_abbreviations():
    """'Bd. N'/'S. N' are the standard abbreviated running-head forms —
    short docs can't accumulate the ≥40%-page signature recurrence, so
    both must flag edge furniture like the spelled-out arms do. The
    \b guards keep 'bd'/'s.' from matching mid-word — 'ebd. N' (ebenda,
    a footnote citation marker) and 'Figs. N' stay unfurniture."""
    assert DE.furniture.search("Bd. 89, S. 12–30")
    assert DE.furniture.search("S. 45")
    assert DE.furniture.search("Band 89")
    assert DE.furniture.search("Seite 45")
    assert not DE.furniture.search("Figs. 45")
    assert not DE.furniture.search("ebd. 45")


def test_table_label_abbreviations():
    """Non-English journals set table legends as 'Tab. N' as often as
    the spelled-out word — the table arm must carry the abbreviation
    like the figure arm already does ('Abb.'/'Fig.'), or those legends
    miss caption adoption, grid boundaries, and <caption>. The union
    picks them up; figure_caption stays blind (images never take
    tables)."""
    for profile in (DE, ES, FR):
        assert profile.table_caption.match("Tab. 3  Ergebnisse")
        assert profile.caption.match("Tab. 3  Ergebnisse")
        assert not profile.figure_caption.match("Tab. 3  Ergebnisse")
    assert DE.table_caption.match("Tabelle 3: Ergebnisse")
    assert ES.table_caption.match("Tabla 3: Resultados")
    assert FR.table_caption.match("Tableau 3: Résultats")
    # plural label forms stay unmatched, like EN's 'Tables N'
    assert not DE.table_caption.match("Tabellen 3: Werte")
    assert not FR.table_caption.match("Tableaux 3: Résultats")


def test_single_letter_word_sets():
    """The drop-cap exclusion rides the profile: EN 'I'/'A' are words
    and stay unfused; DE has no single-letter words so every lone
    letter fuses ('I'/'A' fragments were emitting 'I n…'/'A b…'
    splits); FR a/à/y and ES a/y/o are the sentence-initial singles —
    the 'e'/'u' pre-vocalic allomorphs never open a sentence, so those
    letters stay drop-caps rather than blocking common fusions."""
    assert EN.single_letter_words == "IA"
    assert DE.single_letter_words == ""
    assert FR.single_letter_words == "AÀY"
    assert ES.single_letter_words == "AYO"
    # DE fuses every lone letter
    assert text._join_lines(["I", "n den Jahren"], DE) == "In den Jahren"
    assert text._join_lines(["A", "uch"], DE) == "Auch"
    # FR/ES keep their word letters unfused, fuse the rest
    assert text._join_lines(["Y", "compris"], FR) == "Y compris"
    assert text._join_lines(["À", "partir"], FR) == "À partir"
    assert text._join_lines(["L", "a méthode"], FR) == "La méthode"
    assert text._join_lines(["O", "sea"], ES) == "O sea"
    assert text._join_lines(["U", "n estudio"], ES) == "Un estudio"
    # EN unchanged
    assert text._join_lines(["I", "went"]) == "I went"
    assert text._join_lines(["T", "he cat"]) == "The cat"


def test_singular_author_note_arms():
    """Singular-author acknowledgments divert like EN's 'the author'
    arm — DE Autorin, FR l'auteur (either apostrophe or gender), ES
    el/la autor(a) — so a lone-author note doesn't stay in body flow."""
    assert DE.note_start.match("Die Autorin dankt der DFG")
    assert DE.note_start.match("Der Autor dankt der DFG")
    assert FR.note_start.match("L'auteur remercie le CNRS")
    assert FR.note_start.match("L’auteur remercie le CNRS")
    assert ES.note_start.match("El autor agradece el apoyo")
    assert ES.note_start.match("La autora agradece el apoyo")


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


def test_profile_object_accepted(tmp_path):
    """A LangProfile instance (not just a code) flows through convert."""
    doc = _figure_doc("Abb. 1  Ergebnisse der Messung.")
    _out, html = _convert_epub(doc, tmp_path, "custom", lang=LANGS["de"])
    assert isinstance(EN, LangProfile)
    assert 'xml:lang="de"' in html


def test_groupless_section_label_falls_back(tmp_path):
    """A custom profile whose section_label has no capture group still
    heads its section — _section_label falls back to the whole match
    rather than IndexError-ing mid-conversion."""
    lang = replace(
        EN, section_label=re.compile(r"^Bibliografia\b")
    )
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 200),
        "Cuerpo del artículo que precede a la bibliografía.",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 500, 540, 520), "Bibliografia", fontsize=BODY
    )
    p.insert_textbox(
        fitz.Rect(72, 530, 540, 560), "Uno, D. Una entrada.",
        fontsize=BODY,
    )
    _out, html = _convert_epub(doc, tmp_path, "groupless", lang=lang)
    assert re.search(r"<h2[^>]*>Bibliografia<", html)


def test_english_default_unchanged(tmp_path):
    """Omitting lang keeps the English profile and 'en' metadata."""
    doc = _figure_doc("Figure 1. A caption.")
    _out, html = _convert_epub(doc, tmp_path, "en_def")
    assert 'xml:lang="en"' in html
    assert 'alt="Figure 1. A caption."' in html


def test_affiliation_tail_stripped_by_profile(tmp_path):
    """The byline→author metadata fallback splits the institution tail
    on profile vocabulary — 'Universidad …' under es, not just English
    'University …' — keeping dc:creator to the name list."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 120),
        "Un estudio sobre la lengua",
        fontsize=18,  # ≥1.6× body → h1
    )
    p.insert_textbox(
        fitz.Rect(72, 130, 540, 160),
        "M. García y J. López, Universidad de Granada",
        fontsize=BODY,
    )
    p.insert_textbox(
        fitz.Rect(72, 200, 540, 640),
        "Texto del artículo con contenido suficiente para el análisis. " * 4,
        fontsize=BODY,
    )
    out, _html = _convert_epub(doc, tmp_path, "es_affil", lang="es")
    opf = zipfile.ZipFile(out).read("EPUB/content.opf").decode()
    creator = re.search(r"<dc:creator[^>]*>([^<]+)<", opf).group(1)
    assert "Universidad" not in creator
    assert "García" in creator


def test_section_fallback_localized(tmp_path):
    """A headingless document's fallback chapter title comes from the
    profile — 'Abschnitt 1' under de, not English 'Section 1'."""
    doc = fitz.open()
    p = _page(doc)
    p.insert_textbox(
        fitz.Rect(72, 80, 540, 640),
        "Fließtext ohne jede Überschrift auf dieser Seite. " * 8,
        fontsize=BODY,
    )
    out, _html = _convert_epub(doc, tmp_path, "de_sec", lang="de")
    chap = zipfile.ZipFile(out).read("EPUB/chap_1.xhtml").decode()
    assert "<title>Abschnitt 1</title>" in chap


def test_fallback_literals_localized(tmp_path):
    """An uncaptioned rasterized table and a missing author take profile
    strings — 'alt="Tabelle"' and dc:creator 'Unbekannt' under de, not
    the English defaults."""
    doc = fitz.open()
    p = _page(doc)
    for i, x in enumerate((200, 260, 320)):
        p.insert_textbox(
            fitz.Rect(x, 400, x + 15, 520),
            f"r1c{i} r2c{i} r3c{i}",
            fontsize=BODY,
        )
    p.insert_textbox(
        fitz.Rect(72, 560, 540, 700),
        "Fließtext unterhalb der Tabelle.",
        fontsize=BODY,
    )
    out, _html = _convert_epub(doc, tmp_path, "de_talt", lang="de")
    htmls = " ".join(doc_html(out).values())
    assert 'alt="Tabelle"' in htmls
    opf = zipfile.ZipFile(out).read("EPUB/content.opf").decode()
    creator = re.search(r"<dc:creator[^>]*>([^<]+)<", opf).group(1)
    assert creator == "Unbekannt"
