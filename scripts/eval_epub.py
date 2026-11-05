#!/usr/bin/env python3
"""Evaluate a PDF -> EPUB conversion and print a severity-ranked report.

Usage: python3 scripts/eval_epub.py <input.pdf> <output.epub>

The report grades one conversion the same way every time so regressions
are visible: container validity, metadata sanity, classified word
coverage, TOC/headings, reading-order splices, table disposition,
figure coverage, notes integrity, and whether odd artifacts came from
the source text layer or the converter. Output is markdown; paste it
into an issue or read it inline. Exit code is `1` when any
High-severity finding exists (usable as a CI gate), `0` otherwise.
"""
from __future__ import annotations

import argparse
import html
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

WORD_RE = re.compile(r"[A-Za-z0-9]+")
TAG_RE = re.compile(r"<[^>]+>")
# Collection uses the same `Table N.` shape the disposition search
# requires — a bare citation `(see Table 5)` must not produce a
# 0-coverage "legend+data absent" High and fail the gate. Periodless
# caption styles (`Table 5 Descriptive`) go unchecked — documented limit.
TABLE_LEGEND_RE = re.compile(r"\bTable\s+(\d+)\.")
FIG_LEGEND_RE = re.compile(r"\bFig(?:ure)?\.?\s*(\d+)[a-z]?\b")
# A caption label glued mid-paragraph: "25+ chars of sentence text, then
# a bold Table/Fig N. legend inside the same <p>" — a legend that belongs
# to a floating object was spliced into body flow. The run-up excludes
# `<` so it can never cross a tag boundary — a bold caption opening its
# own <p> has only `>` + tagname available before it, not 25 chars, while
# text sharing the paragraph still matches.
SPLICE_RE = re.compile(r"([^<]{25,}<b>(?:Table|Fig)\.?\s*\d+\.)")
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
    'OP-SCAN150067 1758..1768' carries '..'-ranges, long digit runs, or more
    digits than a title ever needs — the current pipeline only checks for
    the presence of letters, which these strings still pass."""
    if not title:
        return True
    if re.search(r"\d+\s*\.\.\s*\d+|\d{5,}", title):
        return True
    digits = sum(c.isdigit() for c in title)
    return digits > 0 and digits >= sum(c.isalpha() for c in title) * 0.5


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


STAT_HDR_RE = re.compile(r"\b(?:df|CI|SD|SE|Min|Max|Mean|Median)\b", re.IGNORECASE)
# A numbered heading keeps number and title on ONE extracted line — a
# `\s+` separator would let a numeric table cell adopt the label on the
# next line (`6.14\nGender` is not a section). One sublevel max so
# `6.14` decimals can't pass as `6.1` + stray digit; `Surname, I` tails
# are excluded so numbered references can't pose as sections either.
SECTION_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d)?)\.?[ \t]+([A-Z][A-Za-z][A-Za-z &,\-]{2,60})\.?\s*$", re.MULTILINE)
REF_TAIL_RE = re.compile(r",\s*[A-Z]{1,3}$")


def header_like_titles(entries: list[str]) -> list[str]:
    """Nav titles built from table column headers (`Right df t b p CI`,
    `Group M SD Min Max`) — a header row promoted to h2 both pollutes the
    TOC and explodes the table into pseudo-chapters."""
    return [e for e in entries if len(STAT_HDR_RE.findall(e)) >= 3]


def missing_sections(pdf_text: str, entries: list[str]) -> list[str]:
    """Numbered headings (`2. Methods`, `3.1. Participants`) present in the
    source but absent from the TOC — the converter emits them as plain
    paragraphs, so the reader gets no way into the body. Only the
    alphabetic core is matched, so `2. Methods` still finds a nav entry
    titled `Methods`."""
    nav_blob = " ".join(entries).lower()
    out = []
    for m in SECTION_RE.finditer(pdf_text):
        title = m.group(2).strip()
        if title.lower() not in nav_blob and title not in out and not REF_TAIL_RE.search(title):
            out.append(title)
    return out


def nav_entries(nav: str) -> list[str]:
    return [html.unescape(t).strip() for t in re.findall(r'<a href="[^"]*">(.*?)</a>', nav, re.DOTALL)]


def table_dispositions(pdf_text: str, chapters: dict[str, str], notes: str):
    """For each `Table N` legend in the source, decide where it landed:
    inside <table> markup, flattened into prose/blockquote, swept into
    notes.xhtml, or missing. Legends require a trailing period
    (`Table N.`) so in-text citations `(Table N)` don't fake a hit.
    Content survival is scored with 4-grams — a lone digit or cell word
    coincidentally matching somewhere proves nothing, but a contiguous
    cell run can't fake its way in. Every `Table N.` occurrence gets a
    55-token window and the worst coverage wins: a citation window is
    ordinary body text (~70-90%), the real grid is the risky one."""
    epub_tokens = []
    for c in chapters.values():
        epub_tokens += WORD_RE.findall(xhtml_text(c).lower())
    epub_tokens += WORD_RE.findall(xhtml_text(notes).lower())
    grams = {tuple(epub_tokens[i:i + 4]) for i in range(len(epub_tokens) - 3)}
    notes_txt = xhtml_text(notes)
    out = []
    for num in sorted(set(TABLE_LEGEND_RE.findall(pdf_text)), key=int):
        legend_re = rf"Table\s+{num}\."
        coverages = []
        for m in re.finditer(legend_re + r"\s*(.{0,700})", pdf_text, re.DOTALL):
            toks = WORD_RE.findall(m.group(1).lower())[:55]
            win = {tuple(toks[i:i + 4]) for i in range(len(toks) - 3)}
            coverages.append(sum(g in grams for g in win) / max(len(win), 1))
        coverage = min(coverages, default=0.0)
        in_markup = any(re.search(legend_re, seg)
                        for c in chapters.values()
                        for seg in re.findall(r"<table\b.*?</table>", c, re.DOTALL))
        in_notes = re.search(legend_re, notes_txt)
        in_chaps = any(re.search(legend_re, xhtml_text(c)) for c in chapters.values())
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
        sys.exit("error: input file not found")

    import fitz  # pymupdf — same dependency as the pipeline

    findings: list[tuple[str, str, str]] = []  # (severity, check, detail)

    # ---- A. container ----
    try:
        members = epub_members(args.epub)
    except zipfile.BadZipFile:
        sys.exit("FATAL: epub is not a zip archive")
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
    if len(creator) > 150 or any(h in creator.lower() for h in AFFILIATION_HINTS):
        findings.append(("High", "B", f"dc:creator contaminated (len={len(creator)}): {creator[:80]!r}…"))

    # ---- C. coverage ----
    pdf = fitz.open(str(args.pdf))
    pdf_text = " ".join(p.get_text("text") for p in pdf)
    epub_text = " ".join(xhtml_text(c) for c in chapters.values()) + " " + xhtml_text(notes)
    pw, ew = Counter(WORD_RE.findall(pdf_text.lower())), Counter(WORD_RE.findall(epub_text.lower()))
    missing, extra = pw - ew, ew - pw
    rejoined, furn, supers, genuine = classify_missing(missing, extra, furniture_words(pdf), set(ew))
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
    missing_secs = missing_sections(pdf_text, entries)
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
    for num, where, cov in table_dispositions(pdf_text, chapters, notes):
        if where == "missing":
            findings.append(("High", "F", f"Table {num}: legend+data absent from EPUB"))
        elif where == "notes":
            findings.append(("High", "F", f"Table {num}: swept into notes.xhtml (coverage {cov:.0%})"))
        elif where == "prose":
            findings.append(("Med", "F", f"Table {num}: flattened to prose, no grid markup"))
        if where != "missing" and cov < 0.6:
            findings.append(("High", "F", f"Table {num}: content largely absent ({cov:.0%} 4-gram coverage)"))

    # ---- G. figures ----
    pdf_rasters = sum(len(p.get_images()) for p in pdf)
    epub_imgs = [m for m in members if "/images/" in m]
    vec_pages = [i for i, p in enumerate(pdf) if len(p.get_drawings()) > 30]
    for num in sorted(set(FIG_LEGEND_RE.findall(pdf_text)), key=int):
        if not re.search(rf"Fig(?:ure)?\.?\s*{num}[a-z]?\b", epub_text):
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
    if notes:
        if re.search(r"Table\s+\d+\.", xhtml_text(notes)):
            findings.append(("Med", "H", "notes.xhtml contains table legends — notes zone swallowing grid content"))
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
    sys.exit(main())
