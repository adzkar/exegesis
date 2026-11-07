"""Standalone layout diagnosis for gitignored ``journals/**/*.pdf``.

Retains document column profiles, raw page x0 clusters/off-grid candidates,
wrapped-float and central-whitespace probes, an actual reading-order sample,
and EPUB lint/chapter body text. Adds per-page writing directions and rotated-line
bboxes, short-fragment locations, and per-document-edge heading-like line shares.

A fragment is a nonempty extracted text BLOCK, not a span, line, or output HTML
fragment. Location counts use 1..12 characters of ``_block_text`` (including spaces).
Edge shares instead count outdent-strip-like blocks: up to two nonempty lines,
1..120 characters (``MAX_HEADING_CHARS``), no terminal .!?… after closing
quotes/brackets, and an x-extent that stays inside ``OUTDENT_BAND_FILL`` of the
band the edge shares with its right column. FOLD is the share that decided the
tag — read straight from ``_reading_columns``'s own evidence locals, so only
pages >=1, non-spanning, non-furniture blocks bound numerator and denominator
while note/table-bound blocks still count. INPUT/SURVIVING stay broader
all-pages probes (denominator: every non-spanning text block at the edge) and
can legitimately differ from FOLD — page-0 and furniture blocks pollute INPUT,
note/table-bound exits shrink SURVIVING.
Total/spanning heading-like counts also expose heads that cross a phantom gutter.
This is a text-shape probe — the same ``analyze._outdent_like`` shape the
converter's outdent-edge detection uses — not the renderer's font/context
heading classifier. Each physical edge prints its reading column (``-> rcolN``;
``(outdent)`` when it folds into a later edge); reading-order tags use mapped
``colN`` like the converter does.
INPUT counts precede furniture removal. SURVIVING BODY-FLOW counts use the actual
renderer's ``ordered`` blocks minus ``nonbody_ids``: headings/bylines/labels count,
but furniture, notes, rasterized cells, and grid-table cells/captions do not.
Blocks the detector lifted into ``<aside>`` regions never reach ``ordered``;
each page's ``aside-lifted`` line reports that claimed count beside SURVIVING.
These are source-block counts, not final paragraph counts or text-quality scores.

Locations require full block-bbox containment in a connected vector area bbox or
one raw raster image block bbox; overlap gets a separate 'both' bucket. Vector
areas union connected path bboxes after 1pt padding per path, ignoring paths whose
width AND height reach 90% of the unrotated page. These are bounding-box proxies,
not detected figures: clipping is ignored, and 'neither' does not prove a fragment
is outside a figure. Bboxes and line dirs are in extraction (unrotated) coordinates;
page /Rotate is reported separately.

Preparation and rendering run through ``convert`` itself. A temporary profiling
hook reads private renderer locals at return (``page``/``page_index``/``state``
/``ordered``/``nonbody_ids``/``aside_blocks``), before convert releases each
page, and ``_reading_columns``'s ``evidence`` locals at its return. Keep that
small instrumentation contract in sync if those locals change. This
manual corpus tool is not a test helper and must never be imported by tests.
It runs on ``requirements.txt`` alone, so the test helpers it borrows must not
require test-only deps at import time.

Usage: ``python3 dev/diagnose_layouts.py [--journals-dir DIR] [name-substring ...]``
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from statistics import median

import fitz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from helpers import chapter_body_text, lint_epub

from exegesis import convert, render
from exegesis.analyze import (
    _assign_column,
    _column_edges,
    _heading_like,
    _is_spanning,
    _outdent_like,
    _reading_columns,
)
from exegesis.const import MAX_HEADING_CHARS, OUTDENT_BAND_FILL, OUTDENT_MIN_BLOCKS
from exegesis.text import _block_lines, _block_text, _line_text

JOURNALS = ROOT / "journals"
EDGE_TOL = 15
SHORT_CHARS = 12
DRAWING_PAD = 1.0
PAGE_FRAME_RATIO = 0.9
LOCATIONS = ("vector-only", "raster-only", "both", "neither")


@dataclass(frozen=True)
class _Fragment:
    """Lightweight block provenance retained without embedded image bytes."""

    column: int
    chars: int
    heading_like: bool
    outdent_like: bool
    spanning: bool
    location: str
    survives: bool


def _column_profiles(fragments, edges):
    """Profile nearest-edge text assignments, keeping spans out of the share.

    The share denominator is all nonempty non-spanning text blocks at this
    edge; the numerator additionally requires the documented strip shape.
    """
    prof = [
        {"lens": [], "nonspan": 0, "heading_single": 0, "heading_total": 0,
         "strip_nonspan": 0}
        for _ in edges
    ]
    for f in fragments:
        p = prof[f.column]
        p["lens"].append(f.chars)
        p["heading_total"] += f.heading_like
        if not f.spanning:
            p["nonspan"] += 1
            p["heading_single"] += f.heading_like
            p["strip_nonspan"] += f.outdent_like
    for p in prof:
        p["median_len"] = median(p["lens"]) if p["lens"] else 0
    return prof


def _share(profile):
    """Expose numerator and denominator so an empty edge is not called clean."""
    n, d = profile["strip_nonspan"], profile["nonspan"]
    return f"{n}/{d} ({100 * n / d:.1f}%)" if d else "0/0 (n/a)"


def _fold_share(col, edges, width, evidence):
    """The share ``_reading_columns`` itself evaluated for this edge.

    ``evidence`` is read from the converter's own frame, so the printed
    share cannot disagree with the ``(outdent)`` tag the way the all-pages
    INPUT/SURVIVING probes can — page 0 and furniture are already out, and
    note/table-bound blocks stay in. The last edge's share is never
    evaluated (no right column to fold into); an under-floor edge shows
    the ``OUTDENT_MIN_BLOCKS`` shortfall its tag is silent about.
    """
    if col + 1 >= len(edges) or col >= len(evidence):
        return "n/a"
    ev = evidence[col]
    if not ev:
        return "0/0 (n/a)"
    n = sum(
        _outdent_like(b, t, _band_end(col, edges, width)) for b, t in ev
    )
    floor = (
        f"; <{OUTDENT_MIN_BLOCKS} evidence"
        if len(ev) < OUTDENT_MIN_BLOCKS
        else ""
    )
    return f"{n}/{len(ev)} ({100 * n / len(ev):.1f}%{floor})"


def _band_end(col, edges, width):
    """Same band bound `_reading_columns` gives `_outdent_like`.

    The last edge has no right column and can never fold; its row still
    prints a share, so bound its band at the page width for reporting.
    """
    edge = edges[col]
    if col + 1 >= len(edges):
        return edge + (width - edge) * OUTDENT_BAND_FILL
    return edge + (edges[col + 1] - edge) * OUTDENT_BAND_FILL


def _vector_regions(page):
    """Bound connected drawing rects without requiring newer cluster_drawings.

    Pad each path by 1pt so thin/touching axes connect (gaps <2pt can join).
    Ignore near-page-size paths: width AND height >=90% of the unrotated page.
    Connectivity uses individual padded rects, not the empty space introduced
    by their union bbox. Bbox proxies can still cover unrelated enclosed text,
    and the frame guard also excludes genuine page-sized plots.
    """
    page_rect = page.rect * page.derotation_matrix
    remaining = []
    for drawing in page.get_drawings():
        rect = fitz.Rect(drawing["rect"])
        if not rect.is_valid or (
            rect.width >= PAGE_FRAME_RATIO * page_rect.width
            and rect.height >= PAGE_FRAME_RATIO * page_rect.height
        ):
            continue
        remaining.append(rect + (-DRAWING_PAD, -DRAWING_PAD, DRAWING_PAD, DRAWING_PAD))
    regions = []
    while remaining:
        pending = [remaining.pop()]
        region = fitz.Rect(pending[0])
        while pending:
            current = pending.pop()
            for i in range(len(remaining) - 1, -1, -1):
                if current.intersects(remaining[i]):
                    neighbor = remaining.pop(i)
                    pending.append(neighbor)
                    region |= neighbor
        regions.append(region)
    return regions


def _heading_counts(profile):
    """Expose heads that the converter calls spans across a phantom gutter."""
    total, nonspan = profile["heading_total"], profile["heading_single"]
    return f"total={total} non-spanning={nonspan} spanning={total - nonspan}"


def _fragment_location(bbox, vectors, rasters):
    """Classify this block's containment, never the page's drawing count."""
    rect = fitz.Rect(bbox)
    vector = any(rect in region for region in vectors)
    raster = any(rect in region for region in rasters)
    return (
        "both"
        if vector and raster
        else ("vector-only" if vector else "raster-only" if raster else "neither")
    )


def _short_counts(fragments):
    """Count nonempty <=12-character blocks in mutually exclusive locations."""
    counts = Counter(f.location for f in fragments if f.chars <= SHORT_CHARS)
    return f"total={sum(counts.values())} " + " ".join(
        f"{location}={counts[location]}" for location in LOCATIONS
    )


def _wrap_flags(blocks, edges):
    """Return page-wide flanked-image hits and the single-home-column subset.

    Retain the original page-wide probe: a float can itself create phantom
    document columns. Only the subset requires non-spanning images/lines with
    the same nearest edge. Neither count proves wrapping or figure ownership.
    """
    hits = home_hits = 0
    lines = [l for b in blocks if b["type"] == 0 for l in _block_lines(b)]
    for img in (b for b in blocks if b["type"] == 1):
        col = _assign_column(img, edges)
        ix0, iy0, ix1, iy1 = img["bbox"]
        left = right = home_left = home_right = False
        for line in lines:
            ly0, ly1 = line["bbox"][1], line["bbox"][3]
            if ly1 < iy0 or ly0 > iy1:
                continue
            same_home = (
                not _is_spanning(img, edges)
                and not _is_spanning(line, edges)
                and _assign_column(line, edges) == col
            )
            for span in line["spans"]:
                sx0, sx1 = span["bbox"][0], span["bbox"][2]
                left |= sx1 <= ix0 + 2
                right |= sx0 >= ix1 - 2
                home_left |= same_home and sx1 <= ix0 + 2
                home_right |= same_home and sx0 >= ix1 - 2
        hits += left and right
        home_hits += home_left and home_right
    return hits, home_hits


def _twoup_flag(page, blocks):
    """Probe a clear central 16% strip with content spanning >55% page height.

    All block types can obstruct the strip; an image-only scanned spread is
    therefore not recognized. This whitespace signature is not proof of 2-up.
    """
    w, h = page.rect.width, page.rect.height
    strip_lo, strip_hi = w * 0.42, w * 0.58
    ys = []
    for b in blocks:
        x0, y0, x1, y1 = b["bbox"]
        if x0 < strip_hi and x1 > strip_lo:  # crosses the strip
            return False
        if x1 <= strip_lo or x0 >= strip_hi:
            ys.append((y0, y1))
    if not ys:
        return False
    return max(y1 for _, y1 in ys) - min(y0 for y0, _ in ys) > h * 0.55


def _bbox(bbox):
    """Round extraction coordinates only for readable, stable presentation."""
    return tuple(round(v, 1) for v in bbox)


def _line_directions(blocks, surviving_ids):
    """Report raw nonempty line dirs and every departure from (1, 0) >0.001.

    Reverse and skewed lines count too; the page's /Rotate is not line rotation.
    """
    directions, rotated = Counter(), []
    for bi, b in enumerate(blocks):
        if b["type"] != 0:
            continue
        for li, line in enumerate(_block_lines(b)):
            text = _line_text(line).strip()
            if not text:
                continue
            direction = line.get("dir")
            key = tuple(round(v, 4) for v in direction) if direction else "missing"
            directions[key] += 1
            if direction is None:
                continue
            dx, dy = direction
            if abs(dx - 1) <= 0.001 and abs(dy) <= 0.001:
                continue
            rotated.append(
                f"b{bi}/l{li} dir={key} block_bbox={_bbox(b['bbox'])} "
                f"line_bbox={_bbox(line['bbox'])} "
                f"body-flow={'yes' if id(b) in surviving_ids else 'no'} {text[:58]!r}"
            )
    return directions, rotated


def _reading_order(ordered, edges, col_map, lang):
    """Sample the renderer's actual ordering using its own column/span helpers.

    Tags show the *reading* column (``col_map``), matching the converter's
    sequence keys — an outdented head tags the same ``colN`` as the body
    column it folds into, while ``col{i} edge=`` headers stay physical.
    """
    sample = []
    for b in ordered[:18]:
        tag = "IMG" if b["type"] == 1 else _block_text(b, lang)[:58]
        col = (
            "furn"
            if b.get("is_furniture")
            else (
                "span"
                if _is_spanning(b, edges)
                else f"col{col_map[_assign_column(b, edges)]}"
            )
        )
        sample.append(f"y{b['bbox'][1]:5.0f} {col:5} {tag!r}")
    return sample


def _page_report(local):
    """Snapshot actual render decisions before convert clears input page dicts."""
    page, pi, state = local["page"], local["page_index"], local["state"]
    blocks, ordered = state.page_items[pi], local["ordered"]
    surviving_ids = {
        id(b) for b in ordered if b["type"] == 0 and id(b) not in local["nonbody_ids"]
    }
    n_asides = len(local["aside_blocks"])
    vectors = _vector_regions(page)
    rasters = [fitz.Rect(b["bbox"]) for b in blocks if b["type"] == 1]
    fragments = [
        _Fragment(
            column=col,
            chars=len(text),
            heading_like=_heading_like(b, text),
            outdent_like=_outdent_like(
                b, text, _band_end(col, state.edges, page.rect.width),
            ),
            spanning=_is_spanning(b, state.edges),
            location=_fragment_location(b["bbox"], vectors, rasters),
            survives=id(b) in surviving_ids,
        )
        for b in blocks
        if b["type"] == 0
        and (text := _block_text(b, state.lang))
        and (col := _assign_column(b, state.edges)) is not None
    ]
    clusters = _column_edges([b for b in blocks if b["type"] == 0])
    directions, rotated = _line_directions(blocks, surviving_ids)
    return {
        "index": pi,
        "width": page.rect.width,
        "height": page.rect.height,
        "rotation": page.rotation,
        "edges": list(state.edges),
        "body": state.body_size,
        "line_h": state.line_h,
        "fragments": fragments,
        "image_cols": Counter(
            _assign_column(b, state.edges) for b in blocks if b["type"] == 1
        ),
        "clusters": clusters,
        "off_grid": [
            c for c in clusters if all(abs(c - e) >= EDGE_TOL for e in state.edges)
        ],
        "wrap": _wrap_flags(blocks, state.edges),
        "twoup": _twoup_flag(page, blocks),
        "directions": directions,
        "rotated": rotated,
        "col_map": list(state.col_map),
        "asides": n_asides,
        "order": _reading_order(ordered, state.edges, state.col_map, state.lang),
    }


def _convert_with_reports(pdf, out, lang, drop_edges):
    """Instrument real convert(), restoring any profiler even on failure.

    Reading ``ordered``/``nonbody_ids`` at render return avoids duplicating
    preparation, table claims, note zones, or keep-headers decisions. The
    pending cross-page paragraph is subsequently finalized by convert itself.
    ``_reading_columns``'s ``evidence`` locals are captured the same way, so
    the FOLD row reports the share that actually decided each edge's tag.
    """
    reports = []
    render_code = render._render_page.__code__
    reading_code = _reading_columns.__code__
    fold = {"evidence": []}

    def capture(frame, event, arg):
        """Read completed render frames, leaving all converter data untouched."""
        if event != "return":
            return
        if frame.f_code is render_code and "nonbody_ids" in frame.f_locals:
            reports.append(_page_report(frame.f_locals))
        elif frame.f_code is reading_code and "evidence" in frame.f_locals:
            fold["evidence"] = frame.f_locals["evidence"]

    previous = sys.getprofile()
    sys.setprofile(capture)
    try:
        stats = convert(pdf, out, lang=lang, drop_edges=drop_edges)
    finally:
        sys.setprofile(previous)
    if not reports or len(reports) != stats["pages"]:
        raise RuntimeError(
            "incomplete render trace; check renderer instrumentation contract"
        )
    return stats, reports, fold["evidence"]


def _print_report(stats, pages, fold_evidence):
    """Separate raw input geometry from renderer-surviving source-block metrics."""
    first, edges = pages[0], pages[0]["edges"]
    col_map = pages[0]["col_map"]
    fragments = [f for p in pages for f in p["fragments"]]
    surviving = [f for f in fragments if f.survives]
    raw_prof = _column_profiles(fragments, edges)
    body_prof = _column_profiles(surviving, edges)
    image_cols = sum((p["image_cols"] for p in pages), Counter())
    widths = sorted({round(p["width"], 1) for p in pages})
    print(
        f"  {stats['pages']}p  widths={widths}  body={first['body']:.1f}  "
        f"line_h={first['line_h']:.1f}  doc_edges={_bbox(edges)} "
        f"reading_cols={len(set(col_map))}"
    )
    for i, edge in enumerate(edges):
        # an edge folds rightward only when its mapped id is shared with a
        # later edge — non-outdent edges keep a unique reading column
        folded = col_map[i] in col_map[i + 1 :]
        raw, body = raw_prof[i], body_prof[i]
        print(
            f"    col{i} edge={edge:.1f} -> rcol{col_map[i]}"
            f"{' (outdent)' if folded else ''}: INPUT text={len(raw['lens'])} "
            f"img={image_cols[i]} median_len={raw['median_len']:g}; "
            f"SURVIVING BODY-FLOW text={len(body['lens'])} median_len={body['median_len']:g}"
        )
        print(
            f"      heading-like single-line blocks: INPUT {_heading_counts(raw)}; "
            f"SURVIVING BODY-FLOW {_heading_counts(body)}"
        )
        print(
            f"      non-spanning outdent-strip-like share "
            f"(<={MAX_HEADING_CHARS} chars, <={OUTDENT_BAND_FILL:.0%} band fill): "
            f"FOLD {_fold_share(i, edges, first['width'], fold_evidence)}; "
            f"INPUT {_share(raw)}; SURVIVING BODY-FLOW {_share(body)}"
        )
    print(f"  INPUT short <={SHORT_CHARS}: {_short_counts(fragments)}")
    print(f"  SURVIVING BODY-FLOW short <={SHORT_CHARS}: {_short_counts(surviving)}")
    print(
        f"  INPUT geometry probes: off-grid pages={sum(bool(p['off_grid']) for p in pages)}/{len(pages)}  "
        f"wrapped-float-candidates={sum(p['wrap'][0] for p in pages)} "
        f"(single-home-column={sum(p['wrap'][1] for p in pages)})  "
        f"2-up-whitespace-pages={sum(p['twoup'] for p in pages)}"
    )
    sample = min(2, len(pages) - 1)
    for p in pages:
        raw = p["fragments"]
        body = [f for f in raw if f.survives]
        print(
            f"  page {p['index']} (0-based): {p['width']:.0f}x{p['height']:.0f} "
            f"/Rotate={p['rotation']} raw_x0_clusters={_bbox(p['clusters'])} "
            f"off-grid={_bbox(p['off_grid'])}"
        )
        print(f"    INPUT short <={SHORT_CHARS}: {_short_counts(raw)}")
        print(f"    SURVIVING BODY-FLOW short <={SHORT_CHARS}: {_short_counts(body)}")
        print(f"    aside-lifted blocks={p['asides']}")
        print(
            f"    INPUT line dirs={dict(p['directions'])} rotated-lines={len(p['rotated'])}"
        )
        for line in p["rotated"]:
            print(f"      {line}")
        if p["index"] == sample:
            print("    actual reading order (first 18 blocks):")
            for block in p["order"]:
                print(f"      {block}")
    print(
        f"  conversion: grid_tables={stats['grid_tables']} "
        f"raster_tables={stats['raster_tables']} notes={stats['footnotes']} "
        f"images={stats['images']}"
    )


def diagnose(pdf: Path, lang="en", drop_edges=True):
    """Convert once in a temporary directory and report geometry plus EPUB health."""
    print(f"\n=== {pdf.name} ===\n  source={pdf}")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / (pdf.stem + ".epub")
        stats, pages, fold_evidence = _convert_with_reports(
            pdf, out, lang, drop_edges
        )
        _print_report(stats, pages, fold_evidence)
        problems = lint_epub(out)
        print(f"  epub: lint={problems or 'clean'}")
        text = chapter_body_text(out)
        print(f"  chapter body text ({len(text)} chars): {text[:400]!r}")


def main():
    """Survey nested journals explicitly; operational failures, not probes, fail."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "patterns", nargs="*", help="case-insensitive PDF name substrings"
    )
    parser.add_argument(
        "--journals-dir", type=Path, default=JOURNALS, help="PDF directory (recursive)"
    )
    parser.add_argument("--lang", choices=("en", "de", "fr", "es"), default="en")
    parser.add_argument(
        "--keep-headers", action="store_true", help="match convert(drop_edges=False)"
    )
    args = parser.parse_args()
    if not args.journals_dir.is_dir():
        parser.error(f"not a directory: {args.journals_dir}")
    pats = [p.lower() for p in args.patterns]
    pdfs = sorted(
        p
        for p in args.journals_dir.rglob("*.pdf")
        if not pats or any(s in p.name.lower() for s in pats)
    )
    if not pdfs:
        parser.error(f"no matching PDFs in {args.journals_dir}")
    print(f"Pipeline options: lang={args.lang} drop_edges={not args.keep_headers}")
    print(
        "Fragments = nonempty source text blocks; short = 1..12 _block_text chars, including spaces."
    )
    print(
        "BODY-FLOW = actual ordered text minus nonbody_ids; includes headings/bylines/labels, not tables/notes/furniture."
    )
    print(
        "Locations = full block-bbox containment in connected vector-area/raster bboxes; 'both' is separate; not figure detection."
    )
    print(
        f"Vector areas = connected path rects padded by {DRAWING_PAD:g}pt each; "
        f"paths >= {100 * PAGE_FRAME_RATIO:g}% of page width AND height are ignored."
    )
    print(
        "Columns use nearest-edge home assignments for text AND images; "
        "rcolN is the reading column (outdent edges fold right)."
    )
    print(
        f"Edge share = up to 2 nonempty lines, 1..{MAX_HEADING_CHARS} chars, "
        "no terminal .!?… after closing quotes/brackets — FOLD over the "
        "converter's own deciding evidence (pages>=1, non-furniture); "
        "INPUT/SURVIVING over all non-spanning text blocks."
    )
    print(
        "Heading-like totals include spans; a phantom gutter can classify an outdented head as spanning."
    )
    print(
        "Page x0 clusters are raw/unfiltered (including furniture, [0] fallback); off-grid/float/2-up flags are probes only."
    )
    print(
        "Line dirs/bboxes are unrotated extraction coordinates; page /Rotate is separate; b/l indices are 0-based."
    )
    failures = 0
    for pdf in pdfs:
        try:
            diagnose(pdf, args.lang, not args.keep_headers)
        except Exception as e:  # noqa: BLE001 — one bad PDF can't kill the survey
            print(f"\n=== {pdf.name} ===\n  DIAGNOSIS/CONVERSION FAILED: {e}")
            failures += 1
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
