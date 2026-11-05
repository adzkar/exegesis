#!/usr/bin/env python3
"""Evaluate a PDF -> EPUB conversion and print a severity-ranked report.

Usage: python3 scripts/eval_epub.py <input.pdf> <output.epub>

The report grades one conversion the same way every time so regressions
are visible: container validity, metadata sanity, classified word
coverage, TOC/headings, reading-order splices, table disposition,
figure coverage, notes integrity, and whether odd artifacts came from
the source text layer or the converter. Output is markdown; paste it
into an issue or read it inline. Exit code is the number of
High-severity findings (0 = clean), so it can gate CI if wanted.
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
TABLE_LEGEND_RE = re.compile(r"\bTable\s+(\d+)\b")
FIG_LEGEND_RE = re.compile(r"\bFig(?:ure)?\.?\s*(\d+)[a-z]?\b")
# A caption label glued mid-paragraph: ">20 chars of sentence text, then a
# bold Table/Fig N. legend inside the same <p>" — a legend that belongs
# to a floating object was spliced into body flow.
SPLICE_RE = re.compile(r"(.{25,}<b>(?:Table|Fig)\.?\s*\d+\.)", re.DOTALL)
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
    """Words the converter *should* have stripped: block text repeated on
    >=40% of pages (running-head signatures) plus anything in the outer 8%
    vertical edge (page numbers, pattern furniture). Mirrors the
    pipeline's own signals so 'missing' words here count as correct
    stripping, not loss."""
    sig_pages: dict[str, set[int]] = {}
    edge_words: set[str] = set()
    for i, page in enumerate(pdf_pages):
        h = page.rect.height
        for b in page.get_text("blocks"):
            _x0, y0, _x1, y1, text = b[0], b[1], b[2], b[3], b[4]
            sig = " ".join(WORD_RE.findall(text.lower()))[:80]
            if sig:
                sig_pages.setdefault(sig, set()).add(i)
            if y1 < h * 0.08 or y0 > h * 0.92:
                edge_words.update(WORD_RE.findall(text.lower()))
    n = max(len(pdf_pages), 1)
    rep = set()
    for sig, pages in sig_pages.items():
        if len(pages) / n >= 0.4:
            rep.update(sig.split())
    return rep | edge_words


def classify_missing(missing: Counter, extra: Counter, furniture: set[str]):
    """Sort missing words into stripped furniture, rejoined-hyphenation
    (a fragment that is a prefix/suffix of an extra EPUB word), or genuine
    loss. Furniture is checked first — a running-head word that happens to
    prefix an extra word is still furniture. Prefix/suffix matching is
    enough because dehyphenation only ever concatenates a fragment pair."""
    extra_words = list(extra)
    rejoined, furn, genuine = Counter(), Counter(), Counter()
    for w, n in missing.items():
        if w in furniture:
            furn[w] = n
        elif len(w) >= 3 and any(e != w and (e.startswith(w) or e.endswith(w)) for e in extra_words):
            rejoined[w] = n
        else:
            genuine[w] = n
    return rejoined, furn, genuine


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
    rejoined, furn, genuine = classify_missing(missing, extra, furniture_words(pdf))
    verbatim = sum((pw - missing).values())
    coverage_pct = 100 * verbatim / max(sum(pw.values()), 1)
    if sum(genuine.values()) > sum(pw.values()) * 0.01:
        findings.append(("High", "C", (f"genuine word loss {sum(genuine.values())} "
                                    f"(>1%): {genuine.most_common(10)}")))
    elif genuine:
        findings.append(("Med", "C", f"genuine word loss {sum(genuine.values())}: {genuine.most_common(10)}"))

    # ---- D. TOC / headings ----
    entries = nav_entries(nav)
    dup = [t for t, n in Counter(entries).items() if n > 1]
    if dup:
        findings.append(("Low", "D", f"duplicate TOC titles: {dup}"))
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
        if not re.search(rf"Fig(?:ure)?\.?\s*{num}\b", epub_text):
            findings.append(("Med", "G", f"Fig. {num}: legend absent from EPUB"))
    if vec_pages:
        findings.append(("Low", "G", f"vector graphics on pages {vec_pages} not capturable — verify content survived as text"))
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
          f"genuine loss {sum(genuine.values())}")
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
                     ("genuine loss", genuine)):
        ex = ", ".join(w for w, _ in c.most_common(8))
        print(f"| {label} | {sum(c.values())} | {ex} |")
    return min(n_high, 1)


if __name__ == "__main__":
    sys.exit(main())
