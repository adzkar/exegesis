"""Golden-snapshot corpus tests over the real sample papers.

Skipped when a corpus PDF is absent (they're gitignored). Regenerate
snapshots after an intentional heuristic change — ``-m ""`` clears the
default ``not slow`` filter so the slow corpus case regenerates too:

    pytest --update-golden -m ""
"""
import difflib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from helpers import ROOT, corpus_pdf, lint_epub, snapshot_epub

from exegesis import convert

GOLDEN = Path(__file__).parent / "golden"
EVAL = ROOT / "scripts" / "eval_epub.py"

CORPUS = [
    "acp_copernicus.pdf",
    "arxiv_latnet_1705.09036.pdf",
    "arxiv_revtex.pdf",
    "Bed-Sharing in Couples Is Associated With Increased and Stabilized REM Sleep and Sleep- Stage Synchronization.pdf",
    "bmjopen_medical.pdf",
    "bundesges_de.pdf",
    "elife_marginalia.pdf",
    "frontiers_2col.pdf",
    "iscience_cell.pdf",
    "jbigdata_springer.pdf",
    "mdpi_2col.pdf",
    pytest.param("NASA_Spinoff_2025.pdf", marks=pytest.mark.slow),
    "peerj_oa.pdf",
    "plosone_marginalia.pdf",
    "sciadv_aaas.pdf",
    "scirep_nature.pdf",
    pytest.param("UNESCO_Courier_2023_3.pdf", marks=pytest.mark.slow),
    "wiley_ee.pdf",
]


def _slug(name):
    return Path(name).stem.replace(" ", "_")


def _check_golden(golden_path: Path, actual: str, update_golden: bool,
                  kind: str):
    """Write-or-compare one golden snapshot. `actual` is normalized to a
    single trailing newline so JSON and report text share the helper."""
    GOLDEN.mkdir(exist_ok=True)
    actual = actual.rstrip("\n") + "\n"
    if update_golden or not golden_path.is_file():
        golden_path.write_text(actual)
        if not update_golden:
            pytest.fail(f"golden created: {golden_path} — re-run to verify")
        return
    expected = golden_path.read_text()
    if actual != expected:
        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                "golden",
                "actual",
                lineterm="",
            )
        )
        pytest.fail(
            f"{kind} golden mismatch for {golden_path.name} "
            f'(run pytest --update-golden -m "" to accept):\n{diff[:8000]}'
        )


@pytest.mark.parametrize("pdf_name", CORPUS)
def test_corpus_golden(pdf_name, tmp_path, update_golden):
    pdf = corpus_pdf(pdf_name)
    out = tmp_path / (pdf.stem + ".epub")
    stats = convert(pdf, out)
    stats.pop("output", None)
    assert lint_epub(out) == []
    snap = snapshot_epub(out)
    snap["stats"] = stats
    actual = json.dumps(snap, indent=2, sort_keys=True)
    _check_golden(GOLDEN / (_slug(pdf_name) + ".json"), actual,
                  update_golden, "conversion")


@pytest.mark.parametrize("pdf_name", CORPUS)
def test_corpus_eval(pdf_name, tmp_path, update_golden):
    """The eval's full report on the same conversion, snapshotted —
    pins the report format (verdict, checks summary, findings table,
    classified word loss) and catches regressions inside the checks
    themselves (a new finding surfaces as a golden diff, not a
    silently-changed verdict). Exit 2 is always an operational failure,
    never a gradeable verdict."""
    pdf = corpus_pdf(pdf_name)
    out = tmp_path / (pdf.stem + ".epub")
    convert(pdf, out)
    r = subprocess.run([sys.executable, str(EVAL), str(pdf), str(out)],
                       check=False, capture_output=True, text=True)
    assert r.returncode != 2, r.stdout + r.stderr
    _check_golden(GOLDEN / (_slug(pdf_name) + ".eval.txt"), r.stdout,
                  update_golden, "eval report")
