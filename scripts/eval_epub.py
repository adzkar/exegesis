#!/usr/bin/env python3
"""Evaluate a PDF -> EPUB conversion and print a severity-ranked report.

Usage: python3 scripts/eval_epub.py <input.pdf> <output.epub>

The report grades one conversion the same way every time so regressions
are visible: container validity, metadata sanity, classified word
coverage, TOC/headings, reading-order splices, table disposition,
figure coverage, notes integrity, and whether odd artifacts came from
the source text layer or the converter. Output is markdown; paste it
into an issue or read it inline. Exit code is `1` when any
High-severity finding exists (usable as a CI gate), `0` otherwise —
`2` marks operational failures (unreadable inputs), so a broken run
can't masquerade as a gate failure.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

WORD_RE = re.compile(r"[A-Za-z0-9]+")
TAG_RE = re.compile(r"<[^>]+>")
# Caption numbers mirror the pipeline's `_CAP_NUM` leading unit:
# `s?` supplementary prefix, letter suffix (`1a`) or parenthesized
# letter (`2(b)`); dash-joined ranges key on the first number.
CAP_NUM = r"s?\d+(?:[a-z]|\([a-zA-Z]\))?"
# Collection uses the same `Table N.` shape the disposition search
# requires — a bare citation `(see Table 5)` must not produce a
# 0-coverage "legend+data absent" High and fail the gate — and the same
# legend position: anchored at an extracted line start so a
# sentence-ending `…in Table 5.` citation can't collect either.
# Periodless caption styles (`Table 5 Descriptive`) go unchecked —
# documented limit.
TABLE_LEGEND_RE = re.compile(
    rf"(?:^|\n)\s*Table\b\s*\.?\s*({CAP_NUM})\.", re.IGNORECASE)
FIG_LEGEND_RE = re.compile(
    rf"(?:^|\n)\s*(?:Fig(?:ure)?|Plate|Scheme|Chart)\b\s*\.?\s*({CAP_NUM})",
    re.IGNORECASE)
# A caption label glued mid-paragraph: "25+ chars of sentence text, then
# a bold Table/Fig N. legend inside the same <p>" — a legend that belongs
# to a floating object was spliced into body flow. The run-up excludes
# `<` so it can never cross a tag boundary — a bold caption opening its
# own <p> has only `>` + tagname available before it, not 25 chars, while
# text sharing the paragraph still matches. `Fig(?:ure)?` keeps parity
# with FIG_LEGEND_RE — a spelled-out `Figure N.` splice counts too.
# A `,`/`;` right after `</b>` is sentence grammar — a bolded in-text
# citation (`…as shown in <b>Table 2.</b>, we…`), not a splice.
SPLICE_RE = re.compile(
    r"([^<]{25,}<b>(?:Table|Fig(?:ure)?)\.?\s*" + CAP_NUM + r"\.)"
    r"</b>(?!\s*[,;])", re.IGNORECASE)
# Copied from `exegesis.lang.EN.furniture` — the eval grades English
# conversions until the legend-pattern localization follow-up lands.
FURNITURE_RE = re.compile(
    r"www\.|downloaded from|vol\.?\s*\d|issn|doi[:\s]|©|copyright|"
    r"\b\d{2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.IGNORECASE,
)
AFFILIATION_HINTS = (
    "university", "department", "center", "centre", "institute",
    "hospital", "college", "laborator", "school of",
)


def epub_members(path: Path) -> list[str]:
    """List archive members; a BadZipFile means the EPUB isn't a zip at all."""
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def read_member(path: Path, name: str) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8", "replace")


def xhtml_text(raw: str) -> str:
    """Strip markup to plain text for word-level comparisons."""
    return html.unescape(TAG_RE.sub(" ", raw))


def opf_metadata(opf: str) -> dict[str, str]:
    """Pull dc:title/creator/language out of content.opf by tag name."""
    out = {}
    for tag in ("title", "creator", "language"):
        m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", opf, re.DOTALL)
        out[tag] = html.unescape(m.group(1)).strip() if m else ""
    return out


def looks_like_junk_title(title: str) -> bool:
    """Heuristic: real titles are mostly letters. Pagination/ID junk like
    'OP-SCAN150067 1758..1768' carries '..'-ranges, dominant digit runs,
    or outright digit dominance — a real title keeps digits in the
    minority ('Cancer trends 1990-2019', 'COVID-19 in 2020'), so a bare
    ratio flags honest work, and real titles carry accession IDs
    ('Association of rs1137101 with obesity', 'The 100000 Genomes
    Project') so a 5+-digit run only counts when digits dominate the
    whole title ('OP-SCAN150067': 6 digits vs 6 letters). The pipeline
    only checks for the presence of letters, which these strings still
    pass."""
    if not title:
        return True
    if re.search(r"\d+\s*\.\.\s*\d+", title):
        return True
    digits = sum(c.isdigit() for c in title)
    letters = sum(c.isalpha() for c in title)
    if re.search(r"\d{5,}", title) and digits >= letters:
        return True
    return digits > letters and digits >= 10


def furniture_words(pdf_pages) -> set[str]:
    """Words the converter *should* have stripped, mirroring the pipeline's
    own signals rather than approximating them — `order._order_page`
    drops a block when `analyze._is_signature_furniture` or
    `analyze._is_furniture` holds. Signatures: letters-only sigs of
    blocks inside the outer 20% edge zones recurring on >=
    max(2, 40%*pages) pages (running heads) — the zone restriction keeps
    body text repeated mid-page from being excused, and the 2-page floor
    stops a 1-page doc from calling every block 'repeated'. Positional
    furniture mirrors `_is_furniture` exactly: pattern furniture and
    pure page numbers only inside the outer 7% edge (a wider or
    unconditional zone would excuse real edge content as stripped), and
    narrow+tall blocks in the outer 10% horizontal margins (rotated
    sidebars)."""
    sig_re = re.compile(r"[^a-z]+")
    counts: Counter = Counter()
    edge_words: set[str] = set()
    zone_blocks: list[str] = []
    for page in pdf_pages:
        h, w = page.rect.height, page.rect.width
        seen = set()
        for b in page.get_text("blocks"):
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            joined = " ".join(text.split())
            if (y0 > h * 0.93 or y1 < h * 0.07) and (
                FURNITURE_RE.search(joined) or re.fullmatch(r"\d{1,4}", joined)
            ):
                edge_words.update(WORD_RE.findall(joined.lower()))
            if (x1 - x0 < 25 and y1 - y0 > 4 * (x1 - x0)
                    and (x0 > w * 0.9 or x1 < w * 0.1)):
                edge_words.update(WORD_RE.findall(joined.lower()))
            if y1 < h * 0.20 or y0 > h * 0.80:
                sig = sig_re.sub("", text.lower())
                if 3 <= len(sig) <= 80:
                    seen.add(sig)
                    zone_blocks.append(text.lower())
        counts.update(seen)
    boilerplate = {s for s, n in counts.items()
                   if n >= max(2, int(len(pdf_pages) * 0.4))}
    furn = set(edge_words)
    for text in zone_blocks:
        if sig_re.sub("", text) in boilerplate:
            furn.update(WORD_RE.findall(text))
    return furn


def classify_missing(missing: Counter, extra: Counter, furniture: set[str],
                     epub_words: set[str]):
    """Sort missing words into stripped furniture, rejoined-hyphenation
    (a fragment that is a prefix/suffix of an extra EPUB word), superscript
    splits (source token `Kral1` emits as `Kral` + superscript `1` — both
    halves present, so nothing was lost), or genuine loss. Furniture is
    checked first — a running-head word that happens to prefix an extra
    word is still furniture. Prefix/suffix matching is enough because
    dehyphenation only ever concatenates a fragment pair."""
    extra_words = list(extra)
    rejoined, furn, supers, genuine = Counter(), Counter(), Counter(), Counter()
    for w, n in missing.items():
        if w in furniture:
            furn[w] = n
        elif len(w) >= 3 and any(e != w and (e.startswith(w) or e.endswith(w)) for e in extra_words):
            rejoined[w] = n
        elif (m := re.match(r"^([a-z]+)(\d+)$|^(\d+)([a-z]+)$", w)) and \
                all(p in epub_words for p in m.groups() if p):
            supers[w] = n
        else:
            genuine[w] = n
    return rejoined, furn, supers, genuine


# Abbreviation arms (df/CI/SD/SE) match case-insensitively — real header
# rows print them lowercase — but the word forms stay case-sensitive:
# 'Mean and max heart rate' is a heading, 'Mean SD Min Max' is a header
# row pretending to be one.
STAT_HDR_RE = re.compile(r"\b(?:(?i:df|CI|SD|SE)|Min|Max|Mean|Median)\b")
# A numbered heading keeps number and title on ONE extracted line — a
# `\s+` separator would let a numeric table cell adopt the label on the
# next line (`6.14\nGender` is not a section). One sublevel max so
# `6.14` decimals can't pass as `6.1` + stray digit; `Surname, I` tails
# are excluded so numbered references can't pose as sections either.
# No optional trailing period: numbered list items (`1. Collect
# samples.`, `2. Wash twice with PBS.`) share the `N. Title` shape and
# never reach the TOC, but real numbered headings almost never end in a
# sentence period — the line-end anchor discriminates for free. The
# leading dot is mandatory: footnote-style numbering (`1 The authors
# thank`, `2 Corresponding author`) carries none — a periodless `2
# Methods` heading style goes unchecked (precision-first).
SECTION_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d)?)\.[ \t]+([A-Z][A-Za-z][A-Za-z &,\-]{2,60})\s*$", re.MULTILINE)
# Vancouver-style numbered references survive the numbered-heading shape
# (`14. Kessler RC, Berglund P, Demler`, `15. Wang PS,`): reject a title
# carrying a surname + periodless-initials fragment, any title ending
# mid-list on a comma, or the `Surname, I` tail of an unwrapped entry.
REF_TAIL_RE = re.compile(r"([A-Z][a-z]+\s+[A-Z]{1,3}\s*,|,\s*[A-Z]{1,3}$|,$)")
# …and belt-and-suspenders: nothing numbered follows the references
# heading in a real paper, so drop every SECTION_RE hit inside the zone.
REF_ZONE_RE = re.compile(
    r"^\s*(?:\d{1,2}\.?\s+)?"
    r"(?:References(?: and Notes)?|Notes and References|Bibliography)\s*$",
    re.MULTILINE,
)


def header_like_titles(entries: list[str]) -> list[str]:
    """Nav titles built from table column headers (`Group M SD Min Max`,
    `Right df SE CI`) — a header row promoted to h2 both pollutes the
    TOC and explodes the table into pseudo-chapters."""
    return [e for e in entries if len(STAT_HDR_RE.findall(e)) >= 3]


def missing_sections(pdf_text: str, entries: list[str],
                     notes: str) -> list[str]:
    """Numbered headings (`2. Methods`, `3.1. Participants`) present in the
    source but absent from the TOC — the converter emits them as plain
    paragraphs, so the reader gets no way into the body. Only the
    alphabetic core is matched, so `2. Methods` still finds a nav entry
    titled `Methods`. Two more discriminators keep numbered non-headings
    out: a candidate whose text survives in notes.xhtml was a numbered
    note, and a candidate followed by a lowercase-starting line is a
    wrapped list item/sentence — real headings are followed by a new
    sentence."""
    nav_blob = " ".join(entries).lower()
    notes_txt = " ".join(xhtml_text(notes).split()).lower()
    zone = REF_ZONE_RE.search(pdf_text)
    out = []
    for m in SECTION_RE.finditer(pdf_text):
        if zone and m.start() > zone.end():
            continue
        title = m.group(2).strip()
        nxt = next((ln.strip() for ln in pdf_text[m.end():].split("\n")
                    if ln.strip()), "")
        if title.lower() in notes_txt or (nxt and nxt[0].islower()):
            continue
        if title.lower() not in nav_blob and title not in out and not REF_TAIL_RE.search(title):
            out.append(title)
    return out


def nav_entries(nav: str) -> list[str]:
    return [html.unescape(t).strip() for t in re.findall(r'<a href="[^"]*">(.*?)</a>', nav, re.DOTALL)]


# Block-level openers a legend can start inside — a `>` emitted by an
# inline tag (<b>/<i>/<a>/<sup>…) is a mid-sentence position, so
# `…in <b>Table 3.</b>` citations stay citations while `<p><b>Table
# 3.</b>` still counts (inline tags *inside* the legend element are
# allowed by the tag-skipping group).
_BLOCK_OPEN = (r"<(?:p|td|th|caption|h[1-6]|blockquote|dt|dd|li|div|"
               r"section|thead|tbody)\b[^>]*>")
_LEGEND_AT = rf"(?:^|\n|{_BLOCK_OPEN})\s*(?:<[^>]+>\s*)*"


def _num_key(n: str) -> tuple:
    """Sort key for caption numbers: supplementary `S`-prefixed labels
    after regular ones, then numeric core, then suffix."""
    return (n.startswith("S"), int(re.sub(r"\D", "", n) or 0), n)


def _legend_present(raw: str, num: str) -> bool:
    """A `Table N.` only counts as *the legend* in legend position — the
    start of a block element or extracted line. `results appear in
    <b>Table 3.</b>` ends a sentence with the same shape and the same
    tags, so an unanchored search (or one that accepts any tag end)
    lets a citation fake the landing spot — a swept or dropped table
    reporting 'prose' instead of 'notes'/'missing'."""
    return bool(
        re.search(rf"{_LEGEND_AT}Table\b\s*\.?\s*{re.escape(num)}\.",
                  raw, re.IGNORECASE)
    )


def table_dispositions(pdf_text: str, chapters: dict[str, str],
                       notes: str, furniture: set[str]):
    """For each `Table N` legend in the source, decide where it landed:
    inside <table> markup, flattened into prose/blockquote, swept into
    notes.xhtml, or missing. Legends require a trailing period
    (`Table N.`) so in-text citations `(Table N)` don't fake a hit, and
    the EPUB side must match in legend position so a sentence-ending
    citation can't stand in for the legend. Content survival is scored
    with 4-grams — a lone digit or cell word coincidentally matching
    somewhere proves nothing, but a contiguous cell run can't fake its
    way in. Every `Table N.` occurrence gets its own window — iterated
    by position, not a greedy consume, so a citation <700 chars before
    the real legend can't swallow the grid's window — built from the
    first 55 *content* tokens (stripped-furniture words are discounted:
    they're correctly absent from the EPUB, so counting them would
    under-report survival); a window left with <8 tokens — an
    end-of-document citation or an all-furniture tail — carries no
    coverable content and is skipped. The worst surviving window
    decides."""
    epub_tokens = []
    for c in chapters.values():
        epub_tokens += WORD_RE.findall(xhtml_text(c).lower())
    epub_tokens += WORD_RE.findall(xhtml_text(notes).lower())
    grams = {tuple(epub_tokens[i:i + 4]) for i in range(len(epub_tokens) - 3)}
    out = []
    for num in sorted({n.upper() for n in TABLE_LEGEND_RE.findall(pdf_text)},
                      key=_num_key):
        legend_re = rf"(?:^|\n)\s*Table\b\s*\.?\s*{re.escape(num)}\."
        coverages = []
        for m in re.finditer(legend_re, pdf_text, re.IGNORECASE):
            toks = [t for t in WORD_RE.findall(
                pdf_text[m.end():m.end() + 700].lower())
                if t not in furniture][:55]
            if len(toks) < 8:
                continue
            win = {tuple(toks[i:i + 4]) for i in range(len(toks) - 3)}
            coverages.append(sum(g in grams for g in win) / max(len(win), 1))
        coverage = min(coverages) if coverages else None  # unassessed
        in_markup = any(_legend_present(seg, num)
                        for c in chapters.values()
                        for seg in re.findall(r"<table\b.*?</table>", c, re.DOTALL))
        in_notes = _legend_present(notes, num)
        in_chaps = any(_legend_present(c, num) for c in chapters.values())
        if in_markup:
            where = "table"
        elif in_notes and not in_chaps:
            where = "notes"
        elif in_chaps:
            where = "prose"
        else:
            where = "missing"
        out.append((num, where, coverage))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Evaluate a PDF->EPUB conversion.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("epub", type=Path)
    args = ap.parse_args()
    if not args.pdf.exists() or not args.epub.exists():
        print("error: input file not found", file=sys.stderr)
        return 2

    import fitz  # pymupdf — same dependency as the pipeline

    findings: list[tuple[str, str, str]] = []  # (severity, check, detail)

    # ---- A. container ----
    try:
        members = epub_members(args.epub)
    except zipfile.BadZipFile:
        print("FATAL: epub is not a zip archive", file=sys.stderr)
        return 2
    names = {Path(m).name for m in members}
    required = {"mimetype", "container.xml", "content.opf", "nav.xhtml", "toc.ncx"}
    absent = required - names
    chapters = {m: read_member(args.epub, m) for m in members
                if m.endswith(".xhtml") and "chap_" in Path(m).name}
    notes = next((read_member(args.epub, m) for m in members if m.endswith("notes.xhtml")), "")
    opf = next((read_member(args.epub, m) for m in members if m.endswith("content.opf")), "")
    nav = next((read_member(args.epub, m) for m in members if m.endswith("nav.xhtml")), "")
    if absent:
        findings.append(("High", "A", f"missing required members: {sorted(absent)}"))
    xml_bad = []
    for m in members:
        if m.endswith((".xhtml", ".opf", ".ncx")):
            try:
                ET.fromstring(read_member(args.epub, m))
            except ET.ParseError as e:
                xml_bad.append(f"{Path(m).name}: {e}")
    if xml_bad:
        findings.append(("High", "A", f"XML parse failures: {xml_bad[:3]}"))
    manifest_ids = set(re.findall(r'<item[^>]*id="([^"]+)"', opf))
    spine_refs = set(re.findall(r'<itemref[^>]*idref="([^"]+)"', opf))
    if not spine_refs <= manifest_ids:
        findings.append(("High", "A", f"spine refs not in manifest: {spine_refs - manifest_ids}"))

    # ---- B. metadata ----
    meta = opf_metadata(opf)
    if looks_like_junk_title(meta["title"]):
        findings.append(("High", "B", f"dc:title looks like junk: {meta['title']!r}"))
    creator = meta["creator"]
    # Contamination is about content, not length — a 10-author byline
    # clears 150 chars legitimately. Flag an affiliation tail
    # (`Smith J, Department of X`) or the title itself absorbed into the
    # byline (>=3 of its long words present — needs several so a surname
    # colliding with one title word can't fake it).
    title_words = {w for w in WORD_RE.findall(meta["title"].lower()) if len(w) > 3}
    absorbed = len(title_words & set(WORD_RE.findall(creator.lower()))) >= 3
    if any(h in creator.lower() for h in AFFILIATION_HINTS) or absorbed:
        findings.append(("High", "B", f"dc:creator contaminated (len={len(creator)}): {creator[:80]!r}…"))

    # ---- C. coverage ----
    try:
        pdf = fitz.open(str(args.pdf))
    except (RuntimeError, ValueError) as e:  # fitz.FileDataError et al.
        print(f"FATAL: cannot open pdf: {e}", file=sys.stderr)
        return 2
    # Pages join with \n so a legend/heading opening a page's text sits
    # at a line boundary — a space join would defeat the line-start
    # anchors used for legend collection below.
    pdf_text = "\n".join(p.get_text("text") for p in pdf)
    epub_text = " ".join(xhtml_text(c) for c in chapters.values()) + " " + xhtml_text(notes)
    pw, ew = Counter(WORD_RE.findall(pdf_text.lower())), Counter(WORD_RE.findall(epub_text.lower()))
    if not pw:
        findings.append(("High", "C", ("no extractable text in PDF — scanned/"
                                      "image-only input, eval is blind")))
    missing, extra = pw - ew, ew - pw
    furn_vocab = furniture_words(pdf)
    rejoined, furn, supers, genuine = classify_missing(missing, extra, furn_vocab, set(ew))
    verbatim = sum((pw - missing).values())
    coverage_pct = 100 * verbatim / max(sum(pw.values()), 1)
    n_genuine = sum(genuine.values())
    if n_genuine > sum(pw.values()) * 0.01:
        findings.append(("High", "C", (f"genuine word loss {n_genuine} "
                                    f"(>1%): {genuine.most_common(10)}")))
    elif n_genuine >= 10:
        findings.append(("Med", "C", f"genuine word loss {n_genuine}: {genuine.most_common(10)}"))
    elif n_genuine:
        findings.append(("Low", "C", f"genuine word loss {n_genuine}: {genuine.most_common(10)}"))

    # ---- D. TOC / headings ----
    entries = nav_entries(nav)
    dup = [t for t, n in Counter(entries).items() if n > 1]
    if dup:
        findings.append(("Low", "D", f"duplicate TOC titles: {dup}"))
    hdr = header_like_titles(entries)
    if hdr:
        findings.append(("High", "D", (f"table header rows promoted to TOC headings: "
                                      f"{hdr[:3]}{'…' if len(hdr) > 3 else ''}")))
    missing_secs = missing_sections(pdf_text, entries, notes)
    if len(missing_secs) >= 2:
        findings.append(("High", "D", (f"numbered section headings missing from TOC "
                                      f"({len(missing_secs)}): {missing_secs[:5]}{'…' if len(missing_secs) > 5 else ''}")))
    elif missing_secs:
        findings.append(("Low", "D", f"numbered section heading missing from TOC: {missing_secs}"))
    all_chap = "".join(chapters.values())
    n_h1 = len(re.findall(r"<h1[\s>]", all_chap))
    n_h2 = len(re.findall(r"<h2[\s>]", all_chap))
    if n_h1 == 0 and n_h2 > 3:
        findings.append(("Med", "D", "no <h1> emitted — title never promoted, flat TOC"))

    # ---- E. mid-paragraph splices ----
    for name, raw in sorted(chapters.items()):
        for m in SPLICE_RE.finditer(raw):
            frag = xhtml_text(m.group(1))[-70:].strip()
            findings.append(("Med", "E", f"{Path(name).name}: legend spliced mid-sentence: …{frag}"))

    # ---- F. tables ----
    for num, where, cov in table_dispositions(pdf_text, chapters, notes, furn_vocab):
        if where == "missing":
            findings.append(("High", "F", f"Table {num}: legend+data absent from EPUB"))
        elif where == "notes":
            cov_s = "n/a" if cov is None else f"{cov:.0%}"
            findings.append(("High", "F", f"Table {num}: swept into notes.xhtml (coverage {cov_s})"))
        elif where == "prose":
            findings.append(("Med", "F", f"Table {num}: flattened to prose, no grid markup"))
        if where != "missing" and cov is not None and cov < 0.6:
            findings.append(("High", "F", f"Table {num}: content largely absent ({cov:.0%} 4-gram coverage)"))

    # ---- G. figures ----
    # The pipeline embeds one `images/img_N.ext` per unique image
    # (render.py dedupes by sha1 of the bytes and skips <2048-byte
    # blocks) — so the PDF side counts unique extracted rasters the same
    # way, not per-page references: a running-head logo repeated on 10
    # pages is still one image.
    raster_digests = set()
    for x in {img[0] for p in pdf for img in p.get_images()}:
        try:
            data = pdf.extract_image(x)["image"]
        except (RuntimeError, ValueError):
            data = b""
        if len(data) >= 2048:
            raster_digests.add(hashlib.sha1(data).digest())
    pdf_rasters = len(raster_digests)
    # Figure embeds only — `images/table_<digest>.png` members are
    # pipeline-rendered tables, not source rasters; counting them here
    # would let a dropped figure hide behind a rasterized grid.
    epub_imgs = [m for m in members
                 if "/images/" in m and Path(m).name.startswith("img_")]
    vec_pages = [i for i, p in enumerate(pdf) if len(p.get_drawings()) > 30]
    for num in sorted({n.upper() for n in FIG_LEGEND_RE.findall(pdf_text)},
                      key=_num_key):
        if not re.search(
                rf"(?:Fig(?:ure)?|Plate|Scheme|Chart)\b\s*\.?\s*"
                rf"{re.escape(num)}\b", epub_text, re.IGNORECASE):
            findings.append(("Med", "G", f"Fig. {num}: legend absent from EPUB"))
    # >30 vector ops usually means a ruled table grid, not a figure — a
    # `Table N.` legend decides it, and a digit-heavy page is a table
    # continuation; the two have different remedies (grid cells still
    # emit as flat text vs nothing emitted).
    def _is_grid_page(i: int) -> bool:
        t = pdf[i].get_text("text")
        if TABLE_LEGEND_RE.search(t):
            return True
        toks = WORD_RE.findall(t)
        return bool(toks) and sum(x.isdigit() for x in toks) / len(toks) > 0.5
    grid_pages = [i for i in vec_pages if _is_grid_page(i)]
    fig_vec = [i for i in vec_pages if i not in grid_pages]
    if grid_pages:
        findings.append(("Low", "G", (f"vector-ruled table grids on pages {grid_pages} — "
                                     "cells emit as flat text, structure lost")))
    if fig_vec:
        findings.append(("Low", "G", (f"vector graphics on pages {fig_vec} not capturable — "
                                     "verify content survived as text")))
    if pdf_rasters > len(epub_imgs):
        findings.append(("Med", "G", f"raster images: {pdf_rasters} in PDF vs {len(epub_imgs)} embedded"))
    for name, raw in chapters.items():
        for img in re.findall(r"<img[^>]*>", raw):
            if 'alt="' not in img or len(img.split('alt="')[1]) < 15:
                findings.append(("Low", "G", f"{Path(name).name}: <img> missing caption alt text"))

    # ---- H. notes ----
    if notes and re.search(rf"{_LEGEND_AT}Table\b\s*\.?\s*{CAP_NUM}\.",
                           notes, re.IGNORECASE):
        findings.append(("Med", "H", "notes.xhtml contains table legends — notes zone swallowing grid content"))
    # Unguarded: links pointing at #fnN with no notes.xhtml (or no
    # matching anchor) are dead by construction — that's the defect.
    fn_ids = set(re.findall(r'id="fn(\d+)"', notes))
    fn_links = set(re.findall(r'href="[^"]*#fn(\d+)"', all_chap))
    if fn_links - fn_ids:
        findings.append(("Med", "H", f"dead footnote links: {sorted(fn_links - fn_ids)}"))

    # ---- I. artifacts ----
    fffd = epub_text.count("\ufffd")
    if fffd:
        kind = "source-faithful" if "\ufffd" in pdf_text or "\x04" in pdf_text else "converter-introduced"
        sev = "OK" if kind == "source-faithful" else "Med"
        findings.append((sev, "I", f"{fffd} U+FFFD replacement chars ({kind})"))

    # ---- report ----
    rank = {"High": 0, "Med": 1, "Low": 2, "OK": 3}
    findings.sort(key=lambda f: rank[f[0]])
    n_high = sum(1 for f in findings if f[0] == "High")
    print(f"# epub-eval: {args.pdf.name}\n")
    print(f"**Verdict:** {n_high} high-severity findings — "
          f"{'needs attention' if n_high else 'clean'}; "
          f"verbatim word coverage {coverage_pct:.1f}% ({verbatim}/{sum(pw.values())}, "
          f"epub emitted {sum(ew.values())} words after rejoins)\n")
    print("## Checks")
    print(f"- A container: {'PASS' if not absent and not xml_bad else 'FAIL'} — "
          f"{len(members)} members, {len(chapters)} chapters, {len(epub_imgs)} images")
    print(f"- B metadata: title={meta['title'][:60]!r}")
    print(f"- C coverage: rejoined {sum(rejoined.values())}, furniture {sum(furn.values())}, "
          f"superscript splits {sum(supers.values())}, genuine loss {n_genuine}")
    print(f"- D toc: {len(entries)} entries, h1={n_h1} h2={n_h2}, dupes={dup or 'none'}")
    print(f"- G figures: pdf rasters={pdf_rasters}, embedded={len(epub_imgs)}, vector pages={vec_pages}\n")
    print("## Findings (severity-ranked)")
    print("| Sev | Check | Finding |")
    print("|-----|-------|---------|")
    for sev, chk, detail in findings:
        print(f"| {sev} | {chk} | {detail.replace('|', '/')} |")
    if not findings:
        print("| OK | — | no findings |")
    print("\n## Classified word loss")
    print("| class | words | top examples |")
    print("|-------|-------|--------------|")
    for label, c in (("rejoined hyphenation", rejoined), ("stripped furniture", furn),
                     ("superscript splits", supers), ("genuine loss", genuine)):
        ex = ", ".join(w for w, _ in c.most_common(8))
        print(f"| {label} | {sum(c.values())} | {ex} |")
    return min(n_high, 1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ImportError, RuntimeError, ValueError, KeyError, OSError,
            zipfile.BadZipFile, ET.ParseError) as e:
        # Mid-run operational failures (missing fitz, corrupt PDF/zip
        # members) must not exit 1 — that code means "High findings",
        # not "the eval itself broke".
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
