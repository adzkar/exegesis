#!/usr/bin/env python3
"""Build the GitHub Pages conversion report from the local corpus.

Every ``*.pdf`` at the repo root (the gitignored local corpus — the same
files ``tests/golden/`` snapshots) is converted into a tempdir; the
``convert()`` stats plus timing and outcome are written to
``docs/report/index.html`` and a machine-readable ``report.json`` beside
it. Only the rendered report is committed — EPUBs stay transient. Re-run
before a release to refresh the published numbers.
"""
from __future__ import annotations

import datetime
import html
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import exegesis
from exegesis import convert

OUT_DIR = ROOT / "docs" / "report"

_CSS = """
body { font-family: -apple-system, Helvetica, Arial, sans-serif;
       margin: 2rem auto; max-width: 72rem; padding: 0 1rem;
       color: #222; }
h1 { font-size: 1.4rem; }
table { border-collapse: collapse; width: 100%; font-size: 0.85rem; }
th, td { border: 1px solid #ccc; padding: 0.3rem 0.5rem;
         text-align: left; vertical-align: top; }
th { background: #f2f2f2; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
tr.err td { background: #fdeeee; }
.meta { color: #666; font-size: 0.8rem; }
"""

_COLUMNS = [
    ("pdf", "PDF"),
    ("title", "Title"),
    ("pages", "Pages"),
    ("columns", "Cols"),
    ("chapters", "Chap"),
    ("headings", "Head"),
    ("footnotes", "Notes"),
    ("images", "Img"),
    ("grid_tables", "Grid"),
    ("raster_tables", "Raster"),
    ("seconds", "Sec"),
]


def _convert_one(pdf: Path, out_dir: Path) -> dict:
    """Convert one corpus PDF; returns its report row.

    ``convert()`` stats carry the layout signals (columns, both table
    kinds); failures are recorded rather than raised so one bad paper
    can't kill the whole report.
    """
    row: dict = {"pdf": pdf.name, "status": "ok"}
    start = time.perf_counter()
    try:
        stats = convert(pdf, out_dir / (pdf.stem + ".epub"))
    except Exception as e:  # noqa: BLE001 — record failure, keep reporting
        row.update(status="error", error=f"{type(e).__name__}: {e}")
    else:
        row.update(stats)
        row.pop("output", None)
    row["seconds"] = round(time.perf_counter() - start, 1)
    return row


def _render_html(rows: list[dict], generated: str) -> str:
    """Standalone report page: one stats row per corpus PDF."""
    cells = "".join(f"<th>{label}</th>" for _k, label in _COLUMNS)
    body = []
    for r in rows:
        if r["status"] != "ok":
            vals = {
                "pdf": html.escape(r["pdf"]),
                "title": html.escape(r.get("error", "failed")),
            }
            tds = "".join(
                f"<td>{vals.get(k, '')}</td>" for k, _l in _COLUMNS[:-1]
            )
            body.append(f'<tr class="err">{tds}<td class="num">{r["seconds"]}</td></tr>')
            continue
        tds = "".join(
            f'<td class="num">{r[k]}</td>'
            if k not in ("pdf", "title")
            else f"<td>{html.escape(str(r[k]))}</td>"
            for k, _l in _COLUMNS
        )
        body.append(f"<tr>{tds}</tr>")
    ok = sum(1 for r in rows if r["status"] == "ok")
    version = getattr(exegesis, "__version__", "dev")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>exegesis — conversion report</title>
<style>{_CSS}</style></head><body>
<h1>exegesis conversion report</h1>
<p class="meta">Generated {generated} · exegesis {version} ·
{ok}/{len(rows)} converted</p>
<table><thead><tr>{cells}</tr></thead>
<tbody>{"".join(body)}</tbody></table>
<p class="meta">Cols = detected text columns; Grid = upright tables
emitted as &lt;table&gt;; Raster = rotated tables embedded as images.</p>
</body></html>
"""


def main() -> None:
    """Convert the corpus and write docs/report/{index.html,report.json}."""
    pdfs = sorted(ROOT.glob("*.pdf"))
    if not pdfs:
        sys.exit("build_report: no *.pdf corpus files at repo root")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    generated = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    with tempfile.TemporaryDirectory() as tmp:
        rows = [_convert_one(pdf, Path(tmp)) for pdf in pdfs]
    (OUT_DIR / "index.html").write_text(_render_html(rows, generated))
    (OUT_DIR / "report.json").write_text(
        json.dumps(
            {
                "generated": generated,
                "version": getattr(exegesis, "__version__", "dev"),
                "results": rows,
            },
            indent=2,
        )
        + "\n"
    )
    ok = sum(1 for r in rows if r["status"] == "ok")
    print(f"{OUT_DIR}: {ok}/{len(rows)} converted")


if __name__ == "__main__":
    main()
