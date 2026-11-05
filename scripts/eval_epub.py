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
# Caption numbers mirror the pipeline's `_CAP_NUM`: `s?` supplementary
# prefix, letter suffix (`1a`) or parenthesized letter (`2(b)`); the
# range tail sits outside the capture so `Table 3–4.` keys on `3`.
CAP_NUM = r"s?\d+(?:[a-z]|\s*\([a-zA-Z]\))?"
CAP_RANGE = r"(?:\s*[–-]\s*s?\d+)?"


def _num_pat(num: str) -> str:
    """Escape a collected caption number for re-search: the pipeline's
    `\\s*\\(` means `Table 2 (b).` in the source may emit fused as
    `Table 2(b).` — spaces inside the number become `\\s*`."""
    return re.escape(num).replace("\\ ", r"\s*").replace(" ", r"\s*")


# Collection uses the same `Table N.` shape the disposition search
# requires — a bare citation `(see Table 5)` must not produce a
# 0-coverage "legend+data absent" High and fail the gate — and the same
# legend position: anchored at an extracted block start (blocks are
# flattened one-per-line before matching) so a wrapped `…in\nTable 5.
# The…` sentence or a cite-only `Table S3.` can't collect either.
# Periodless caption styles (`Table 5 Descriptive`) go unchecked —
# documented limit.
TABLE_LEGEND_RE = re.compile(
    rf"(?:^|\n)\s*Table\b\s*\.?\s*({CAP_NUM}){CAP_RANGE}\.",
    re.IGNORECASE)
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
    r"([^<]{25,}<b>(?:Table|Fig(?:ure)?|Plate|Scheme|Chart)"
    r"(?:\.?\s*|</b><b>\s*)*" + CAP_NUM + r"(?:</b><b>\s*)*"
    + CAP_RANGE + r"\.)</b>(?!\s*[,;])", re.IGNORECASE)
# Copied from `exegesis.lang.EN.furniture` — the eval grades English
# conversions until the legend-pattern localization follow-up lands.
FURNITURE_RE = re.compile(
    r"www\.|downloaded from|vol\.?\s*\d|issn|doi[:\s]|©|copyright|"
    r"\b\d{2}\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)",
    re.IGNORECASE,
)
# 'center'/'centre'/'college' are plausible surnames — flag them only in
# affiliation context (after a comma or `of`); the rest are never names,
# so a bare word match suffices (`laborator` is a stem, left unbound).
AFFIL_NAME_RE = re.compile(
    r"(?:,|\bof)\s+\b(?:center|centre|college)\b", re.IGNORECASE)
AFFIL_WORD_RE = re.compile(
    r"\b(?:university|department|institute|hospital|school of)\b|"
    r"\blaborator", re.IGNORECASE)


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


def furniture_words(pdf_pages) -> Counter:
    """Counted vocabulary of words the converter *should* have stripped,
    mirroring the pipeline's own signals rather than approximating them —
    `order._ordered_blocks`
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
    sidebars). Counted, not a set: a running-head word excuses at most
    its own stripped occurrences, so `of`/`vol`/page digits can't
    blanket-excuse genuine body loss."""
    sig_re = re.compile(r"[^a-z]+")
    counts: Counter = Counter()
    edge_words: Counter = Counter()
    zone_blocks: list[str] = []
    for page in pdf_pages:
        h, w = page.rect.height, page.rect.width
        seen = set()
        for b in page.get_text("blocks"):
            x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            joined = " ".join(text.split())
            # _is_furniture searches dehyphenated `_block_text` — a
            # `copy-`/`right` running head reads `copyright` to the
            # converter; the vocab must excuse the same words. Gate on
            # the dehyphenated form, collect the raw tokens (the actual
            # missing-side words are the unjoined fragments).
            dehyph = re.sub(r"([a-z])-\s", r"\1", joined)
            if (y0 > h * 0.93 or y1 < h * 0.07) and (
                FURNITURE_RE.search(dehyph) or re.fullmatch(r"\d{1,4}", joined)
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
    furn = Counter(edge_words)
    for text in zone_blocks:
        if sig_re.sub("", text) in boilerplate:
            furn.update(WORD_RE.findall(text))
    return furn


def classify_missing(missing: Counter, extra: Counter, furniture: Counter,
                     epub_counts: Counter, epub_text: str = ""):
    """Sort missing words into stripped furniture, rejoined-hyphenation
    (a fragment that is a prefix/suffix of an extra EPUB word), superscript
    splits (source token `Kral1` emits as `Kral` + superscript `1` —
    both halves present AND adjacent in emitted text, so `covid19`
    can't hide behind free-floating `covid`/`19`), or genuine loss.
    Furniture is
    checked first — a running-head word that happens to prefix an extra
    word is still furniture. Dehyphenation only ever concatenates a
    fragment *pair*, so the rejoined claim requires the complement to
    be missing too — a lone `the` prefixing an unrelated `them` is
    coincidence, not a join. Every excuse class is count-capped —
    furniture excuses at most its own stripped occurrences, a rejoined
    fragment at most `min(own, complement, merges)` instances, and a
    superscript split at most the adjacent-pair count — so unpaired
    excess stays genuine and feeds the >1% gate."""
    extra_words = list(extra)
    rejoined, furn, supers, genuine = Counter(), Counter(), Counter(), Counter()
    for w, n in missing.items():
        cap, cls = 0, genuine
        if furniture.get(w):
            cap, cls = min(n, furniture[w]), furn
        elif len(w) >= 3:
            for e in extra_words:
                if e == w:
                    continue
                if e.startswith(w) and len(e) - len(w) >= 2:
                    cap = max(cap, min(n, missing.get(e[len(w):], 0), extra[e]))
                if e.endswith(w) and len(e) - len(w) >= 2:
                    cap = max(cap, min(n, missing.get(e[:-len(w)], 0), extra[e]))
            if cap:
                cls = rejoined
        if not cap and (m := re.match(r"^([a-z]+)(\d+)$|^(\d+)([a-z]+)$", w)):
            pair = [p for p in m.groups() if p]
            adj = len(re.findall(
                rf"(?<![0-9A-Za-z]){re.escape(pair[0])}\s*"
                rf"{re.escape(pair[1])}(?![0-9A-Za-z])",
                epub_text, re.IGNORECASE))
            cap = min(n, adj)
            if cap:
                cls = supers
        cls[w] += cap
        if n - cap:
            genuine[w] = n - cap
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
    paragraphs, so the reader gets no way into the body. The alphabetic
    core is matched against each nav entry with its own leading number
    stripped — `2. Methods` finds a nav entry titled `Methods`, while an
    entry merely containing the word (`Materials and Methods`) does NOT
    count as coverage. Two more discriminators keep numbered non-headings
    out: a candidate whose own numbered line survives in notes.xhtml
    (marker then title at a note-paragraph start) was a numbered note —
    a note merely containing the word 'results' must not silence a
    missing `2. Results` — and a candidate followed by a
    lowercase-starting line is a wrapped list item/sentence — real
    headings are followed by a new sentence. A candidate flanked by
    `N±1.`/`N±1)` lines with no body between is a numbered-list run
    (`1. Collect`, `2. Wash`) — periodless items share the `N. Title`
    shape but never reach the TOC. A heading can also wrap mid-title
    (`2. Materials and\nMethods`) — it counts as covered when a nav
    title continues with the next line's first word."""
    nav_titles = {
        re.sub(r"^\s*\d+(?:\.\d+)?\s*[.)]?\s*", "", e.lower()).strip()
        for e in entries}
    notes_paras = [" ".join(xhtml_text(p).split()).lower()
                   for p in re.findall(r"<p\b[^>]*>(.*?)</p>", notes,
                                       re.DOTALL)]
    zone = REF_ZONE_RE.search(pdf_text)
    out = []
    for m in SECTION_RE.finditer(pdf_text):
        if zone and m.start() > zone.end():
            continue
        title = m.group(2).strip()
        nxt = next((ln.strip() for ln in pdf_text[m.end():].split("\n")
                    if ln.strip()), "")
        # The note marker precedes its text — `<sup>2</sup>Results`
        # emits fused after tag-stripping, so separators are optional.
        note_re = (rf"[*†‡§\s]*{re.escape(m.group(1))}[.\s)]*"
                   rf"{re.escape(title.lower())}")
        if (any(re.match(note_re, p) for p in notes_paras)
                or (nxt and nxt[0].islower())):
            continue
        # Adjacent `N±1.`/`N±1)` lines with no body between are a
        # numbered-list run (`1. Collect`, `2. Wash`) — list items
        # never reach the TOC, so only an isolated numbered line can
        # be a demoted heading.
        seq = m.group(1).split(".")
        nxt_n = ".".join(seq[:-1] + [str(int(seq[-1]) + 1)])
        prv_n = ".".join(seq[:-1] + [str(int(seq[-1]) - 1)])
        prv = next((ln.strip() for ln in reversed(
                    pdf_text[:m.start()].split("\n")) if ln.strip()), "")
        if (re.match(rf"{re.escape(nxt_n)}[.)]\s", nxt)
                or re.match(rf"{re.escape(prv_n)}[.)]\s", prv)):
            continue
        # Two distinct sections can share a title (`3. Methods` and
        # `7. Methods`) — dedupe on (number, title), not title alone.
        if (title.lower() not in nav_titles
                and (m.group(1), title) not in out
                and not REF_TAIL_RE.search(title)):
            # A numbered heading can wrap mid-title (`2. Materials
            # and\nMethods`) — covered when a nav title continues the
            # first word of the next line.
            joined = f"{title} {nxt.split()[0]}".lower() if nxt else ""
            if joined and any(t.startswith(joined) for t in nav_titles):
                continue
            out.append((m.group(1), title))
    return out


def nav_entries(nav: str) -> list[str]:
    return [html.unescape(t).strip() for t in re.findall(r'<a href="[^"]*">(.*?)</a>', nav, re.DOTALL)]


# Block-level openers a legend can start inside — a `>` emitted by an
# inline tag (<b>/<i>/<a>/<sup>…) is a mid-sentence position, so
# `…in <b>Table 3.</b>` citations stay citations while `<p><b>Table
# 3.</b>` still counts. The repeated group also skips whitespace,
# `*†‡§` note markers, empty-position tags, and whole inline elements
# carrying text — restricted to `<sup>` so a styled lead-in
# (`<p><i>see</i> Table 3.`) stays a citation: a numbered note emits
# `<p class="fn"><sup>3</sup>Table 4.` where the marker element carries
# real text between the block opener and the legend.
_BLOCK_OPEN = (r"<(?:p|td|th|caption|figcaption|h[1-6]|blockquote|"
               r"dt|dd|li|div|section|thead|tbody)\b[^>]*>")
_LEGEND_AT = (rf"(?:^|\n|{_BLOCK_OPEN})(?:[\s*†‡§]|<[^>]+>|"
              rf"<sup\b[^>]*>(?:<[^>]+>[^<]*</[^>]+>|[^<]*)*</sup>)*")


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
        re.search(rf"{_LEGEND_AT}Table\b\s*\.?\s*{_num_pat(num)}"
                  rf"{CAP_RANGE}\.", raw, re.IGNORECASE)
    )


def _legend_near(chap: str, num: str, needle: str,
                 radius: int = 1500) -> bool:
    """Does `needle` occur within `radius` chars of this table's legend
    inside one chapter? A detached caption (`<p>Table 3.</p>` beside its
    `<table>`) and a rasterized grid (`images/table_x.png` beside the
    in-flow legend) both keep the grid near its legend — proximity
    attributes the markup/image to *this* table when several share a
    chapter."""
    m = re.search(rf"{_LEGEND_AT}Table\b\s*\.?\s*{_num_pat(num)}"
                  rf"{CAP_RANGE}\.", chap, re.IGNORECASE)
    if not m:
        return False
    return needle in chap[max(0, m.start() - radius):m.end() + radius]


def table_dispositions(blocks_text: str, chapters: dict[str, str],
                       notes: str, furniture: Counter):
    """For each `Table N` legend in the source, decide where it landed:
    inside <table> markup, beside <table> markup it failed to join
    (detached caption), beside a rasterized `images/table_*.png` (the
    intended rotated-table path — cells live in the image, not text),
    flattened into prose/blockquote, swept into notes.xhtml, or
    missing. Legends require a trailing period
    (`Table N.`) at a block start (the caller passes block-flattened
    text — a wrapped sentence breaking at `Table 5.` stays mid-line),
    so in-text citations `(Table N)` don't fake a hit, and
    the EPUB side must match in legend position so a sentence-ending
    citation can't stand in for the legend. Content survival is scored
    with 4-grams — a lone digit or cell word coincidentally matching
    somewhere proves nothing, but a contiguous cell run can't fake its
    way in. Every `Table N.` occurrence gets its own window — iterated
    by position, not a greedy consume, so a citation <700 chars before
    the real legend can't swallow the grid's window, and the window is
    truncated at the *next* table legend so a surviving sibling can't
    lend its content — built from the first 55 tokens. Furniture words
    keep their stream positions: deleting them would fabricate
    n-gram adjacency that exists in neither text (`group0 12 0 3 0`
    reads `group0 0 0` once digits drop). Instead, grams containing a
    furniture word leave the denominator — stripped heads are
    correctly absent, so their grams are unassessable, not missing —
    and a window left with <5 furniture-free grams is skipped. The
    worst surviving window decides."""
    epub_tokens = []
    for c in chapters.values():
        epub_tokens += WORD_RE.findall(xhtml_text(c).lower())
    epub_tokens += WORD_RE.findall(xhtml_text(notes).lower())
    grams = {tuple(epub_tokens[i:i + 4]) for i in range(len(epub_tokens) - 3)}
    bounds = sorted(m.start() for m in TABLE_LEGEND_RE.finditer(blocks_text))
    out = []
    for num in sorted({n.upper() for n in TABLE_LEGEND_RE.findall(blocks_text)},
                      key=_num_key):
        legend_re = (rf"(?:^|\n)\s*Table\b\s*\.?\s*{_num_pat(num)}"
                     rf"{CAP_RANGE}\.")
        coverages = []
        for m in re.finditer(legend_re, blocks_text, re.IGNORECASE):
            nxt = next((b for b in bounds if b > m.start()), m.end() + 700)
            toks = WORD_RE.findall(
                blocks_text[m.end():min(m.end() + 700, nxt)].lower())[:55]
            win = {tuple(toks[i:i + 4]) for i in range(len(toks) - 3)
                   if all(t not in furniture for t in toks[i:i + 4])}
            if len(win) < 5:
                continue
            coverages.append(sum(g in grams for g in win) / len(win))
        coverage = min(coverages) if coverages else None  # unassessed
        in_markup = any(_legend_present(seg, num)
                        for c in chapters.values()
                        for seg in re.findall(r"<table\b.*?</table>", c, re.DOTALL))
        in_notes = _legend_present(notes, num)
        in_chaps = any(_legend_present(c, num) for c in chapters.values())
        if in_markup:
            where = "table"
        elif any(_legend_near(c, num, "<table")
                 for c in chapters.values()):
            # grid markup sits beside the legend but didn't absorb it
            # as a caption — linkage failed, not flattening
            where = "detached"
        elif any(_legend_near(c, num, "images/table_")
                 for c in chapters.values()):
            # the intended rotated-table path: legend stays in flow,
            # cells live inside a rendered PNG — coverage can't assess
            where = "rasterized"
            coverage = None
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
    if AFFIL_NAME_RE.search(creator) or AFFIL_WORD_RE.search(creator) or absorbed:
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
    # Table and figure legends collect on block-joined text: each
    # extracted block flattens to one line, so `\n` anchors mean block
    # starts — a real caption opens its own block, while a sentence
    # that merely wraps at `Table 5.`/`Figure 3` (or a cite-only
    # `Table S3.`) stays mid-line and can't fake a disposition row.
    pdf_blocks_text = "\n".join(
        " ".join(b[4].split())
        for p in pdf for b in p.get_text("blocks"))
    epub_text = " ".join(xhtml_text(c) for c in chapters.values()) + " " + xhtml_text(notes)
    pw, ew = Counter(WORD_RE.findall(pdf_text.lower())), Counter(WORD_RE.findall(epub_text.lower()))
    if not pw:
        findings.append(("High", "C", ("no extractable text in PDF — scanned/"
                                      "image-only input, eval is blind")))
    missing, extra = pw - ew, ew - pw
    furn_vocab = furniture_words(pdf)
    rejoined, furn, supers, genuine = classify_missing(
        missing, extra, furn_vocab, ew, epub_text)
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
    shown_secs = [f"{n}. {t}" for n, t in missing_secs]
    if len(missing_secs) >= 2:
        findings.append(("High", "D", (f"numbered section headings missing from TOC "
                                      f"({len(missing_secs)}): {shown_secs[:5]}{'…' if len(shown_secs) > 5 else ''}")))
    elif missing_secs:
        findings.append(("Low", "D", f"numbered section heading missing from TOC: {shown_secs}"))
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
    for num, where, cov in table_dispositions(pdf_blocks_text, chapters, notes, furn_vocab):
        if where == "missing":
            # A dropped legend on an intact grid scores high coverage —
            # the data survived, so 'legend+data absent' would misstate
            # the defect.
            if cov is not None and cov >= 0.6:
                findings.append(("Med", "F", f"Table {num}: legend absent; grid data survives ({cov:.0%} coverage)"))
            else:
                findings.append(("High", "F", f"Table {num}: legend+data absent from EPUB"))
        elif where == "detached":
            findings.append(("Low", "F", f"Table {num}: legend detached from grid markup (caption linkage failed)"))
        elif where == "rasterized":
            findings.append(("Low", "F", f"Table {num}: rasterized to image (cells not text-checkable)"))
        elif where == "notes":
            cov_s = "n/a" if cov is None else f"{cov:.0%}"
            findings.append(("High", "F", f"Table {num}: swept into notes.xhtml (coverage {cov_s})"))
        elif where == "prose":
            findings.append(("Med", "F", f"Table {num}: flattened to prose, no grid markup"))
        if where != "missing" and cov is not None and cov < 0.6:
            findings.append(("High", "F", f"Table {num}: content largely absent ({cov:.0%} 4-gram coverage)"))

    # ---- G. figures ----
    # The pipeline embeds one `images/img_N.ext` per unique *rendered*
    # image — dict type-1 blocks ≥2048B, sha1-deduped — so count the
    # same population: get_images() lists /Resources instead, missing
    # XObject-nested draws and counting never-rendered entries.
    raster_digests = set()
    for p in pdf:
        for b in p.get_text("dict")["blocks"]:
            if b["type"] == 1:
                data = b.get("image") or b""
                if len(data) >= 2048:
                    raster_digests.add(hashlib.sha1(data).digest())
    pdf_rasters = len(raster_digests)
    # Figure embeds only — `images/table_<digest>.png` members are
    # pipeline-rendered tables, not source rasters; counting them here
    # would let a dropped figure hide behind a rasterized grid.
    epub_imgs = [m for m in members
                 if Path(m).parent.name == "images"
                 and Path(m).name.startswith("img_")]
    vec_pages = [i for i, p in enumerate(pdf) if len(p.get_drawings()) > 30]
    # Same citation-vs-legend split as the table side: collect on the
    # block-flattened text and require legend position in the raw xhtml
    # — an in-text 'as shown in Figure 3' can't satisfy a dropped
    # caption.
    for num in sorted({n.upper() for n in FIG_LEGEND_RE.findall(pdf_blocks_text)},
                      key=_num_key):
        if not re.search(
                rf"{_LEGEND_AT}(?:Fig(?:ure)?|Plate|Scheme|Chart)\b"
                rf"\s*\.?\s*{_num_pat(num)}(?![A-Za-z0-9])",
                all_chap, re.IGNORECASE):
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
        findings.append(("Low", "G", (f"vector-ruled table grids on pages "
                                      f"{[i + 1 for i in grid_pages]} — "
                                      "cells emit as flat text, structure lost")))
    if fig_vec:
        findings.append(("Low", "G", (f"vector graphics on pages "
                                      f"{[i + 1 for i in fig_vec]} not capturable — "
                                      "verify content survived as text")))
    if pdf_rasters > len(epub_imgs):
        findings.append(("Med", "G", f"raster images: {pdf_rasters} in PDF vs {len(epub_imgs)} embedded"))
    for name, raw in chapters.items():
        for img in re.findall(r"<img[^>]*>", raw):
            # The pipeline emits alt="" for uncaptioned figures and the
            # `table_alt` fallback ("table") for uncaptioned grids —
            # those are the meaningful misses; a short real caption
            # (alt="Fig. 1") is fine.
            alt = re.search(r'alt="([^"]*)"', img)
            if not alt or alt.group(1) in ("", "table"):
                findings.append(("Low", "G", f"{Path(name).name}: <img> missing caption alt text"))

    # ---- H. notes ----
    if notes and re.search(
            rf"{_LEGEND_AT}Table\b\s*\.?\s*{CAP_NUM}{CAP_RANGE}\.",
            notes, re.IGNORECASE):
        findings.append(("Med", "H", "notes.xhtml contains table legends — notes zone swallowing grid content"))
    # Unguarded: links pointing at #fnN with no notes.xhtml (or no
    # matching anchor) are dead by construction — that's the defect.
    fn_ids = set(re.findall(r'id="fn(\d+)"', notes))
    fn_links = set(re.findall(r'href="[^"]*#fn(\d+)"', all_chap + notes))
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
    print(f"- A container: {'PASS' if not absent and not xml_bad and spine_refs <= manifest_ids else 'FAIL'} — "
          f"{len(members)} members, {len(chapters)} chapters, {len(epub_imgs)} images")
    print(f"- B metadata: title={meta['title'][:60]!r}")
    print(f"- C coverage: rejoined {sum(rejoined.values())}, furniture {sum(furn.values())}, "
          f"superscript splits {sum(supers.values())}, genuine loss {n_genuine}")
    print(f"- D toc: {len(entries)} entries, h1={n_h1} h2={n_h2}, dupes={dup or 'none'}")
    print(f"- G figures: pdf rasters={pdf_rasters}, embedded={len(epub_imgs)}, "
          f"vector pages={[i + 1 for i in vec_pages]}\n")
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
    except Exception as e:  # noqa: BLE001 — any operational failure is exit 2
        # Mid-run operational failures (missing fitz, corrupt PDF/zip
        # members) must not exit 1 — that code means "High findings",
        # not "the eval itself broke".
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
