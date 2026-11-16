"""Report-only dry run of candidate line-level running-head rules (#69).

Iterates PDFs (root *.pdf corpus plus the optional gitignored journals/
survey set) and prints, per candidate rule, the page-top lines each rule
would split — evidence for the removal go/no-go decision. Nothing here
touches the converter and tests never import it.

Usage: ``python3 dev/dry_run_edge_lines.py [--journals-dir DIR] [name-substring ...]``
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exegesis.analyze import _signature
from exegesis.const import (
    BOILERPLATE_COVERAGE,
    EDGE_ZONE,
    FOLIO_HEAD_BLOCK_SPAN,
)
from exegesis.text import _line_text

JOURNALS = ROOT / "journals"
LEAD_WINDOW = 4   # leading lines of a block scanned for a head row


def leading_rows(pages):
    """(pi, line_index, y0_frac, text) for each in-zone line near the
    leading edge of the page's FIRST text block — the position a fused
    running head occupies on a single-block page."""
    for pi, (page, pdict) in enumerate(pages):
        height = page.rect.height
        first = next(
            (b for b in sorted(
                (b for b in pdict["blocks"] if b["type"] == 0),
                key=lambda b: b["bbox"][1])),
            None,
        )
        if first is None:
            continue
        for li, line in enumerate(first["lines"][:LEAD_WINDOW]):
            y0f = line["bbox"][1] / height
            if y0f > EDGE_ZONE:
                break
            text = _line_text(line).strip()
            if text:
                yield pi, li, y0f, text


def edge_folio(text):
    """A standalone integer at either end of the line = folio candidate;
    digit runs inside 2.2 / 0.528 / 16. / 2%) / 7‡§ are not tokens."""
    m = re.match(r"^(\d{1,4})(?=\s|$)", text) or re.search(r"(?<=\s)(\d{1,4})$", text)
    return int(m.group(1)) if m else None


def remainder_shape(text):
    """The non-folio remainder should look like a head — or be empty
    (a bare folio is furniture by itself). Returns None when the
    remainder looks like content: a sentence (terminal period, >10
    words, lowercase first word at either folio edge)."""
    rest = re.sub(r"^\s*\d{1,4}\s*|\s*\d{1,4}\s*$", "", text).strip()
    if not rest:
        return ""
    if rest.endswith((".", ",", ";", ":")):
        return None
    if len(rest) > 70 or len(rest.split()) > 10:
        return None
    if rest.split()[0][0].islower():
        return None
    return rest


def rule_folio_anchored(pages, rows):
    """Candidate A: page-top leading row carrying an edge folio whose
    folio − pdf_page offset clusters (±1 of the mode, ≥3 distinct
    pages — each page voting a distinct offset once), with a
    head-shaped or empty remainder. Mirrors the shipped rule's page
    gate: only a page whose top block spans half the page height is
    scanned."""
    eligible = set()
    for pi, (page, pdict) in enumerate(pages):
        height = page.rect.height
        first = next(
            (b for b in sorted(
                (b for b in pdict["blocks"] if b["type"] == 0),
                key=lambda b: b["bbox"][1])),
            None,
        )
        if first is not None and (
            first["bbox"][3] - first["bbox"][1]
            >= height * FOLIO_HEAD_BLOCK_SPAN
        ):
            eligible.add(pi)
    cand = [
        (pi, li, y0f, text, edge_folio(text))
        for pi, li, y0f, text in rows
        if pi in eligible and remainder_shape(text) is not None
    ]
    cand = [c for c in cand if c[4] is not None]
    if not cand:
        return []
    page_offs = {}
    for c in cand:
        page_offs.setdefault(c[0], set()).add(c[4] - c[0])
    offs = Counter(o for so in page_offs.values() for o in so)
    mode = offs.most_common(1)[0][0]
    hits = [c for c in cand if abs(c[4] - c[0] - mode) <= 1]
    return hits if len({c[0] for c in hits}) >= 3 else []


def rule_sig_floor(pages, rows):
    """Candidate B: digit-stripped signature of a leading line recurring
    on ≥40% of pages (the naive extension — the trap rules must beat).
    Signatures under 4 chars ('r', 'vs', 'c') are dropped here — they
    flood with math/panel labels otherwise."""
    floor = max(2, int(len(pages) * BOILERPLATE_COVERAGE))
    sigs = {}
    for pi, li, y0f, text in rows:
        if li > 1:
            continue  # first/second line only — a head row's position
        sig = _signature(text)
        if len(sig) < 4:
            continue
        sigs.setdefault(sig, set()).add(pi)
    rows_of = {}
    for pi, li, y0f, text in rows:
        sig = _signature(text)
        if sig in sigs and len(sigs[sig]) >= floor:
            rows_of.setdefault(sig, []).append((pi, li, y0f, text))
    return {s: h for s, h in rows_of.items() if len(sigs[s]) >= floor}


def rule_stable_y(pages, rows):
    """Candidate C: candidate B plus stable y0 (spread ≤2% of height)."""
    out = {}
    for sig, hits in rule_sig_floor(pages, rows).items():
        ys = [h[2] for h in hits]
        if max(ys) - min(ys) <= 0.02:
            out[sig] = hits
    return out


def describe(pdf):
    with fitz.open(pdf) as doc:
        pages = [(page, page.get_text("dict")) for page in doc]
        rows = list(leading_rows(pages))
        print(f"\n=== {pdf.name} ({len(pages)} pages, {len(rows)} zone rows) ===")
        folio_hits = rule_folio_anchored(pages, rows)
        if folio_hits:
            offs = Counter(h[4] - h[0] for h in folio_hits)
            print(f"  A folio-anchored: {len(folio_hits)} rows, offsets {dict(offs)}")
            for h in folio_hits[:8]:
                print(f"      p{h[0]:>2} y={h[2]:.3f} {h[3][:70]!r}")
        for name, fn in (("B sig-floor", rule_sig_floor),
                         ("C sig+stable-y", rule_stable_y)):
            hits = fn(pages, rows)
            if hits:
                total = sum(len(v) for v in hits.values())
                print(f"  {name}: {len(hits)} sigs, {total} rows")
                for sig, hs in list(hits.items())[:4]:
                    print(f"      sig={sig[:40]!r} x{len(hs)} e.g. p{hs[0][0]} {hs[0][3][:60]!r}")


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "patterns", nargs="*", help="case-insensitive PDF name substrings"
    )
    parser.add_argument(
        "--journals-dir",
        type=Path,
        default=JOURNALS,
        help="PDF directory (recursive)",
    )
    args = parser.parse_args(argv)
    if args.journals_dir != JOURNALS and not args.journals_dir.is_dir():
        parser.error(f"not a directory: {args.journals_dir}")
    pats = [p.lower() for p in args.patterns]
    pdfs = sorted(ROOT.glob("*.pdf"))
    if args.journals_dir.is_dir():
        pdfs += sorted(args.journals_dir.rglob("*.pdf"))
    for pdf in pdfs:
        if pats and not any(n in pdf.name.lower() for n in pats):
            continue
        try:
            describe(pdf)
        except Exception as e:  # noqa: BLE001 - report, never crash the survey
            print(f"{pdf.name}: FAILED {e}")


if __name__ == "__main__":
    main(sys.argv[1:])
