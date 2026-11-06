"""Language-tagged pattern profiles: furniture, notes, section labels,
and caption regexes keyed by ISO 639-1 code.

The converter was English-only by construction — these profiles turn the
language assumptions into data. ``LANGS`` covers English (default),
German, French, and Spanish — the common Latin-script journal languages.
Non-Latin scripts are out of scope and degrade to the English defaults:
CJK needs word-shaping and vertical-text review, RTL additionally needs
column-order and direction attributes on the output.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# caption numbers accept letter suffixes ('Table 1a', 'Figure 2(b)') and
# dash-joined ranges ('Tables 3–4'); the label must open the text so
# mid-sentence 'see Table 1' citations never match
_CAP_NUM = r"s?\d+(?:[a-z]|\s*\([a-zA-Z]\))?(?:\s*[–-]\s*s?\d+)?"
# after the number: a dash, '.', ':', ')', a real layout gap (2+ spaces —
# Springer-style 'Table 1  Title'), or end-of-text
_CAP_TAIL = r"(?:\s+[—–-]|\s*[.:)]|\s{2,}|$)"


def _caption_re(labels: str) -> re.Pattern:
    """A ``LABEL N`` opener pattern built from a label alternation."""
    return re.compile(
        rf"(?i)^\s*(?:{labels})\s*\.?\s*{_CAP_NUM}{_CAP_TAIL}"
    )


@dataclass(frozen=True)
class LangProfile:
    """One language's pattern set plus its BCP-47 code.

    ``code`` is stamped into the EPUB's ``xml:lang`` and OPF metadata.
    ``furniture`` marks running heads/footers by content; ``note_start``
    marks acknowledgment/correspondence openers that divert a bottom-zone
    block into the notes chapter (and join split notes in the chapter);
    ``section_label`` promotes leading section names to h2 — its capture
    group 1 becomes the heading text (the whole match when the pattern
    has no groups, so a custom ``r"^Bibliografia\b"`` can't crash); the three
    caption patterns drive both figure/table alt-text scans (``caption``
    is the full label union, ``figure_caption`` excludes table labels —
    table legends sit above the body so a caption *below* an image
    belongs to a text-flowed table — and ``table_caption`` is the table
    labels alone, for the rotated-table scan and grid-table ``<caption>``).
    ``abstract_title`` is the injected heading for the detected abstract;
    ``notes_title`` names the notes chapter; ``name_joiner`` is the
    language's 'and' inside a byline name list. ``affiliation`` splits a
    byline's institution tail off for author metadata (case-sensitive —
    affiliation words keep their capitals, so a lowercase 'center' inside
    a name never splits); ``section_fallback`` is the untitled-chapter
    name, a ``str.format`` template taking ``n``. ``table_alt`` is the
    alt text for an uncaptioned rasterized table; ``unknown_author`` is
    the dc:creator when neither metadata nor a byline yields a name;
    ``start_title`` labels the seeded TOC entry of a headingless,
    noteless document — none should leak English into a localized EPUB.
    ``single_letter_words`` names the lone letters that are real words
    when sentence-initial, so the drop-cap merges in ``_join_lines``
    and the byline tail relocation don't fuse them — English 'I'/'A';
    German has none (every lone letter is a drop-cap); FR/ES carry
    their sentence-initial singles (a/à/y, a/y/o — the 'e'/'u'
    pre-vocalic allomorphs only occur inside a phrase, never sentence-
    initial, so those letters stay drop-caps).
    ``figure_labels``/``table_labels`` are the raw label alternations
    the caption patterns are compiled from — tooling that needs the
    same vocabulary inside its own anchor/capture/tail shapes
    (``scripts/eval_epub.py``'s legend scanners) builds on these rather
    than re-listing the labels; ``None`` on a hand-built profile means
    the language's caption vocabulary was never declared, and such
    tools must degrade rather than match against English labels.
    """

    code: str
    furniture: re.Pattern
    note_start: re.Pattern
    section_label: re.Pattern
    caption: re.Pattern
    figure_caption: re.Pattern
    table_caption: re.Pattern
    abstract_title: str
    notes_title: str
    name_joiner: str
    affiliation: re.Pattern
    section_fallback: str
    table_alt: str
    unknown_author: str
    start_title: str
    single_letter_words: str
    figure_labels: str | None = None
    table_labels: str | None = None


def _profile(
    code,
    *,
    furniture,
    notes,
    sections,
    fig_labels,
    table_label,
    abstract,
    notes_title,
    joiner,
    affils,
    section,
    table_alt,
    unknown_author,
    start_title,
    letters,
):
    """Assemble a frozen profile from per-language pattern strings.

    Furniture and note patterns compile case-insensitively (running heads
    and acknowledgments vary in case); section labels stay case-sensitive
    — they must match the capitalized canonical form or headings in the
    body font would over-trigger on lowercase prose.
    """
    return LangProfile(
        code=code,
        furniture=re.compile(furniture, re.IGNORECASE),
        note_start=re.compile(notes, re.IGNORECASE),
        section_label=re.compile(sections),
        caption=_caption_re(rf"{fig_labels}|{table_label}"),
        figure_caption=_caption_re(fig_labels),
        table_caption=_caption_re(table_label),
        abstract_title=abstract,
        notes_title=notes_title,
        name_joiner=joiner,
        affiliation=re.compile(rf"\s+(?:{affils})\b"),
        section_fallback=section,
        table_alt=table_alt,
        unknown_author=unknown_author,
        start_title=start_title,
        single_letter_words=letters,
        figure_labels=fig_labels,
        table_labels=table_label,
    )


EN = _profile(
    "en",
    furniture=(
        r"www\.|downloaded from|vol\.?\s*\d|issn|doi[:\s]|©|copyright|"
        r"\b\d{2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
    ),
    notes=(
        r"^[*\s]*(preparation of (?:this|the)|requests? for reprints|"
        r"correspondence (?:concerning|should be|addressed)|"
        r"(?:this (?:paper|article|work) was )?supported by|"
        r"the author[s]? (?:thank|acknowledge|wish)|to whom correspondence)"
    ),
    sections=(
        r"^(References and Notes|Notes and References|References|"
        r"Bibliography|Supporting Online Material|Supplementary Materials?|"
        r"Acknowledg?ements|Acknowledgments|Materials and Methods|Appendix|"
        r"Funding|Author Contributions|Data Availability)\b"
    ),
    fig_labels=r"fig\.?|figure|plate|scheme|chart",
    table_label=r"table",
    abstract="Abstract",
    notes_title="Notes",
    joiner=" and ",
    affils=r"Department|University|Institute|Center|College|School of",
    section="Section {n}",
    table_alt="table",
    unknown_author="Unknown",
    start_title="Start",
    letters="IA",
)

DE = _profile(
    "de",
    furniture=(
        r"www\.|issn|doi[:\s]|©|copyright|urheberrecht|"
        r"\b(?:band|bd)\s*\.?\s*\d|jahrg(?:ang)?\.?|"
        r"\b(?:seite|s\.)\s*\.?\s*\d|"
        r"\b\d{1,2}\.\s*(?:januar|februar|märz|april|mai|juni|juli|"
        r"august|september|oktober|november|dezember)"
    ),
    notes=(
        r"^[*\s]*(danksagung|wir danken|"
        r"(?:der|die|den) autor(?:innen|en|in)? dank(?:t|en)|"
        r"gefördert (?:durch|von)|unterstützt (?:durch|von)|"
        r"korrespondenz (?:an|richtet|bitte)|anfragen (?:an|richtet)|"
        r"diese (?:arbeit|studie) wurde(?:n)? (?:von|durch))"
    ),
    sections=(
        r"^(Literaturverzeichnis|Literatur|Quellenverzeichnis|"
        r"Bibliographie|Material und Methoden|Materialien und Methoden|"
        r"Methoden|Danksagung|Anhang|Zusammenfassung|Einleitung|"
        r"Ergebnisse|Diskussion|Förderung|Autorenbeiträge|"
        r"Datenverfügbarkeit)\b"
    ),
    fig_labels=r"abb\.?|abbildung|tafel|schema|diagramm|grafik",
    table_label=r"tab(?:elle|\.)?",
    abstract="Zusammenfassung",
    notes_title="Anmerkungen",
    joiner=" und ",
    affils=(
        r"Universität|Institut|Fakultät|Fachbereich|Abteilung|Hochschule"
    ),
    section="Abschnitt {n}",
    table_alt="Tabelle",
    unknown_author="Unbekannt",
    start_title="Anfang",
    letters="",
)

FR = _profile(
    "fr",
    furniture=(
        r"www\.|issn|doi[:\s]|©|copyright|tous droits réservés|"
        r"vol\.?\s*\d|année\s*\d|numéro\s*\d|reçu le|accepté le|"
        r"\b\d{1,2}\s+(?:janvier|février|mars|avril|mai|juin|juillet|"
        r"août|septembre|octobre|novembre|décembre)"
    ),
    notes=(
        r"^[*\s]*(remerciements|nous remercions|les auteurs remercient|"
        r"l['’]auteur(?:e)? remercie|"
        r"financ[ée]e? (?:par|grâce)|soutenue? (?:par|grâce)|"
        r"correspondance (?:à|adresser)|ce travail (?:a été|fut))"
    ),
    sections=(
        r"^(Bibliographie|Références bibliographiques|Références|"
        r"Méthodes|Matériels? et Méthodes|Remerciements|Annexe|Résumé|"
        r"Introduction|Résultats|Discussion|Supplément|Financement|"
        r"Contributions des auteurs|Disponibilité des données)\b"
    ),
    fig_labels=r"fig\.?|figure|planche|schéma|graphique",
    table_label=r"tab(?:le(?:au)?|\.)?",
    abstract="Résumé",
    notes_title="Notes",
    joiner=" et ",
    affils=(
        r"Université|Institut|Département|Laboratoire|École|Faculté|"
        r"Centre"
    ),
    section="Section {n}",
    table_alt="tableau",
    unknown_author="Inconnu",
    start_title="Début",
    letters="AÀY",
)

ES = _profile(
    "es",
    furniture=(
        r"www\.|issn|doi[:\s]|©|copyright|todos los derechos reservados|"
        r"vol\.?\s*\d|año\s*\d|número\s*\d|recibido el|aceptado el|"
        r"\b\d{1,2}\s+(?:enero|febrero|marzo|abril|mayo|junio|julio|"
        r"agosto|septiembre|octubre|noviembre|diciembre)"
    ),
    notes=(
        r"^[*\s]*(agradecimientos|agradecemos|los autores agradecen|"
        r"(?:el autor|la autora) agradece|"
        r"financiad[oa] (?:por|gracias)|apoyad[oa] por|"
        r"correspondencia (?:a|dirigir)|este trabajo (?:fue|ha sido))"
    ),
    sections=(
        r"^(Referencias|Bibliografía|Métodos|Materiales y Métodos|"
        r"Agradecimientos|Apéndice|Resumen|Introducción|Resultados|"
        r"Discusión|Material (?:Suplementario|Complementario)|"
        r"Financiación|Contribuciones de los autores|"
        r"Disponibilidad de datos)\b"
    ),
    fig_labels=r"fig\.?|figura|lámina|esquema|gráfico",
    table_label=r"tab(?:la|\.)?",
    abstract="Resumen",
    notes_title="Notas",
    joiner=" y ",
    affils=r"Universidad|Instituto|Departamento|Facultad|Escuela|Centro",
    section="Sección {n}",
    table_alt="tabla",
    unknown_author="Desconocido",
    start_title="Inicio",
    letters="AYO",
)

LANGS = {p.code: p for p in (EN, DE, FR, ES)}


def get_lang(lang: str | LangProfile) -> LangProfile:
    """Resolve a language code (or an existing profile) to a
    ``LangProfile``; unknown codes raise ``ValueError`` naming the
    supported set rather than silently degrading to English."""
    if isinstance(lang, LangProfile):
        return lang
    try:
        return LANGS[lang]
    except KeyError:
        raise ValueError(
            f"unsupported language {lang!r}; available: {sorted(LANGS)}"
        ) from None
