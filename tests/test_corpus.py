"""Golden-snapshot corpus tests over the real sample papers.

Skipped when a corpus PDF is absent (they're gitignored). Regenerate
snapshots after an intentional heuristic change:

    pytest --update-golden
"""
import difflib
import json
from pathlib import Path

import pytest
from helpers import corpus_pdf, lint_epub, snapshot_epub

import pdf_to_epub as m

GOLDEN = Path(__file__).parent / "golden"

CORPUS = [
    "2000_Biobehavioral-responses-to-stress-in-females_tend-and-befriend.pdf",
    "A Wandering Mind Is an Unhappy Mind.pdf",
    "Negativity_Bias_Negativity_Dominance_and_Contagion.pdf",
    "Polizzi-Lynn2021_Article_RegulatingEmotionalityToManage.pdf",
    pytest.param("Things Left Behind.pdf", marks=pytest.mark.slow),
]


def _slug(name):
    return Path(name).stem.replace(" ", "_") + ".json"


@pytest.mark.parametrize("pdf_name", CORPUS)
def test_corpus_golden(pdf_name, tmp_path, update_golden):
    pdf = corpus_pdf(pdf_name)
    out = tmp_path / (pdf.stem + ".epub")
    stats = m.convert(pdf, out)
    stats.pop("output", None)
    assert lint_epub(out) == []
    snap = snapshot_epub(out)
    snap["stats"] = stats
    GOLDEN.mkdir(exist_ok=True)
    golden_path = GOLDEN / _slug(pdf_name)
    actual = json.dumps(snap, indent=2, sort_keys=True)
    if update_golden or not golden_path.is_file():
        golden_path.write_text(actual + "\n")
        if not update_golden:
            pytest.fail(f"golden created: {golden_path} — re-run to verify")
        return
    expected = golden_path.read_text().strip()
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
            f"golden mismatch for {pdf_name} "
            f"(run pytest --update-golden to accept):\n{diff[:8000]}"
        )
