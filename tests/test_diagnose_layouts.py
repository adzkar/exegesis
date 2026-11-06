"""Exercise the standalone survey through its CLI; never import it into the test suite."""
import subprocess
import sys
from pathlib import Path

import fitz
import pytest
from builders import _page, _save, paragraph, text
from furniture_fixtures import rotated_margin_stamp
from layout_fixtures import GENERATORS

ROOT = Path(__file__).resolve().parent.parent
DIAGNOSTIC = ROOT / "dev" / "diagnose_layouts.py"


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    """A corpus-free subprocess also guards the diagnostic's private render-trace contract."""
    directory = tmp_path_factory.mktemp("diagnostic-cli")
    for name in (
        "vector_figure", "raster_figure", "outdented_two_spans",
        "heads_only_phantom", "scan_2up",
    ):
        GENERATORS[name](directory / f"{name}.pdf")
    rotated_margin_stamp(directory / "rotated_stamp.pdf")
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250), "Normal article text begins before the short control.")
    text(page, 72, 320, "Control")
    paragraph(page, (72, 410, 540, 470), "Normal article text continues below the short control.")
    text(page, 72, 760, "www.a.io")
    _save(doc, directory / "furniture.pdf")
    result = subprocess.run(
        [sys.executable, str(DIAGNOSTIC), "--journals-dir", str(directory)],
        cwd=ROOT, capture_output=True, text=True, check=False, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("epub: lint=clean") == 7
    return result.stdout


def _report(reports, name):
    """Read one human-readable document section without coupling tests to private functions."""
    marker = f"=== {name}.pdf ==="
    assert marker in reports
    return reports.split(marker, 1)[1].split("\n=== ", 1)[0]


def test_diagnostic_separates_raw_from_surviving_fragments(reports):
    """Dropping furniture changes the surviving metric but cannot change raw PDF counts."""
    report = _report(reports, "furniture")
    assert "INPUT short <=12: total=2" in report
    assert "SURVIVING BODY-FLOW short <=12: total=1" in report
    assert "actual reading order" in report


def test_diagnostic_reports_fragment_locations(reports):
    """Connected axes/rules cover labels; raster overlays differ from exterior panel letters."""
    vector = _report(reports, "vector_figure")
    raster = _report(reports, "raster_figure")
    assert "total=3 vector-only=3 raster-only=0 both=0 neither=0" in vector
    assert "total=4 vector-only=0 raster-only=2 both=0 neither=2" in raster


def test_diagnostic_reports_rotated_direction_and_bounds(reports):
    """The ~27pt stamp is vertical writing, not a page /Rotate effect or a thin strip."""
    report = _report(reports, "rotated_stamp")
    assert "/Rotate=0" in report
    assert "rotated-lines=1" in report
    assert "dir=(0.0, -1.0) block_bbox=(11.4, 277.5, 38.4, 625.0)" in report
    assert "body-flow=yes" in report


def test_diagnostic_reports_nonspanning_outdent_shares(reports):
    """The heading-like edge metric is separate from the <=12-character location metric."""
    spans = _report(reports, "outdented_two_spans")
    heads = _report(reports, "heads_only_phantom")
    assert "edge=36.0" in spans and "INPUT 4/4 (100.0%)" in spans
    assert "edge=90.0" in heads and "INPUT 6/6 (100.0%)" in heads
    assert "heading-like single-line share (<=120 chars)" in spans
    assert "total=6 non-spanning=6 spanning=0" in heads


def test_diagnostic_flags_twoup_whitespace(reports):
    """The symmetric spread trips the central-strip probe; ordinary pages do not."""
    assert "2-up-whitespace-pages=1" in _report(reports, "scan_2up")
    assert "2-up-whitespace-pages=0" in _report(reports, "furniture")
