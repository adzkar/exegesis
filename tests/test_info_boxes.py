"""Front-matter info-box detection (#41): label-seeded field runs,
distinct-label boxes, frozen-core membership, and the false-positive
guards. These drive real ``convert()`` — the detector marks blocks
pre-grid and the machinery lifts them wholesale.
"""
from __future__ import annotations

import re
import subprocess
import sys
import zipfile
from pathlib import Path

import fitz
import pytest
from builders import _page, _save, block, image, text
from helpers import chapter_body_text, doc_html, lint_epub

from exegesis import convert
from exegesis.analyze import (
    _body_font_size,
    _detect_asides,
    _doc_column_edges,
)
from exegesis.lang import EN

EVAL = Path(__file__).resolve().parent.parent / "scripts" / "eval_epub.py"

BODY_BLOCK = (
    "Ordinary article prose keeps its place in the body flow and",
    "establishes the single body column this page is measured by.",
)
_ASIDE_RE = re.compile(r"<aside class=\"sidebar\">.*?</aside>", re.DOTALL)


def _convert(pdf_path, out):
    stats = convert(pdf_path, out)
    html = "\n".join(doc_html(out).values())
    return stats, html


def _asides(html):
    """Emitted ``<aside class="sidebar">`` fragments."""
    return _ASIDE_RE.findall(html)


def _run_eval(pdf, epub, *args):
    return subprocess.run(
        [sys.executable, str(EVAL), str(pdf), str(epub), *args],
        check=False,
        capture_output=True,
        text=True,
    )


def _delete_epub_text(epub_path, phrase):
    """Drop ``phrase`` from chapter XHTML — simulates lost body text."""
    with zipfile.ZipFile(epub_path) as zin:
        members = {n: zin.read(n) for n in zin.namelist()}
    for name, data in members.items():
        if name.rsplit("/", 1)[-1].startswith("chap_"):
            members[name] = data.replace(phrase.encode(), b"")
    with zipfile.ZipFile(epub_path, "w") as zout:
        for name, data in members.items():
            zout.writestr(name, data)


def test_colon_fields_and_badge_lift(tmp_path):
    """The natcomm shape: colon fields plus a media-anchored badge."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A colon-label information panel study", size=18)
    text(page, 400, 240, "Received: 3 March 2024", size=9)
    text(page, 400, 264, "Accepted: 9 April 2024", size=9)
    image(page, (400, 292, 430, 322))
    text(page, 436, 306, "Check for updates", size=8)
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    assert "Received: 3 March 2024" in aside
    assert "Accepted: 9 April 2024" in aside
    # the badge's image and its grouped text ride the same aside
    assert "<img" in aside and "Check for updates" in aside
    assert lint_epub(out) == []


def test_caps_stack_marker_and_email(tmp_path):
    """The Frontiers shape: all-caps labels, a `*` marker, an email
    value carried by the last field's run, body prose beside the box."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 80, "A caps-stack front matter panel", size=18)
    labels = ("EDITED BY", "REVIEWED BY", "RECEIVED  #35 April 2024",
              "ACCEPTED", "PUBLISHED", "*CORRESPONDENCE")
    for i, lbl in enumerate(labels):
        text(page, 450, 120 + i * 14, lbl, size=8)
    text(page, 450, 204, "author@example.org", size=8)
    block(page, 72, 300, BODY_BLOCK, leading=14)
    block(page, 72, 460, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    for needle in (
        "EDITED BY", "REVIEWED BY", "RECEIVED", "#35 April 2024",
        "ACCEPTED", "PUBLISHED", "CORRESPONDENCE", "author@example.org",
    ):
        assert needle in aside
    # nothing lifted renders a second copy in the body flow
    stripped = _ASIDE_RE.sub("", html)
    for needle in ("EDITED BY", "author@example.org"):
        assert needle not in stripped


def test_wrapped_citation_extends_the_run(tmp_path):
    """The MDPI shape: a `Citation:` field wraps across aligned
    continuation blocks before the next field seeds. The 14pt
    spacing keeps the wrap blocks separate in extraction (tighter
    lines fuse into the seed block)."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A wrapped-citation information box", size=18)
    text(page, 40, 240, "Citation: Doe J, Smith K.", size=8)
    text(page, 40, 254, "Wrapped findings from the study", size=8)
    text(page, 40, 268, "Journal Example 2024, 9, 1-12", size=8)
    text(page, 40, 292, "Academic Editor: Jane R. Doe", size=8)
    text(page, 40, 306, "Received: 1 January 2024", size=8)
    text(page, 40, 320, "Accepted: 2 February 2024", size=8)
    text(page, 40, 334, "Published: 5 March 2024", size=8)
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    for needle in (
        "Wrapped findings from the study",
        "Journal Example 2024, 9, 1-12",
        "Academic Editor: Jane R. Doe",
        "Published: 5 March 2024",
    ):
        assert needle in aside


def test_multiword_and_fused_labels(tmp_path):
    """Multi-word colon labels and a fused two-label block both box."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Multi-word label panel", size=18)
    text(page, 400, 240, "Data Availability Statement: on request", size=9)
    text(page, 400, 264, "Academic Editor: Jane R. Doe", size=9)
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    assert "Data Availability Statement: on request" in aside
    assert "Academic Editor: Jane R. Doe" in aside

    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "Fused label panel", size=18)
    block(
        page, 400, 240,
        ("Received: 3 March 2024", "Accepted: 9 April 2024"),
        leading=14, size=9,
    )
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "fused.pdf")
    out = tmp_path / "fused.epub"
    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    assert "Received: 3 March 2024" in aside
    assert "Accepted: 9 April 2024" in aside


def test_single_label_is_never_a_box(tmp_path):
    """One labeled field alone stays body flow."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A single-label control study", size=18)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    assert "Received: 3 March 2024" in chapter_body_text(out)


def test_same_label_twice_is_not_a_box(tmp_path):
    """Two adjacent `Received:` fields carry one distinct label."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A repeated-label control", size=18)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    text(page, 40, 264, "Received: 4 April 2024", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    assert chapter_body_text(out).count("Received:") == 2


def test_fields_beyond_gap_stay_body(tmp_path):
    """Two labels separated by more than 2x line height are not a box."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A wide-gap control study", size=18)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    text(page, 40, 340, "Accepted: 9 April 2024", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    assert "Received: 3 March 2024" in body
    assert "Accepted: 9 April 2024" in body


def test_body_text_is_a_barrier(tmp_path):
    """A prose block between the fields breaks adjacency — no box.
    It sits off the fields' left edge so it can't join as a wrap
    continuation; its overlap with the open gap is the barrier."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A barrier control study", size=18)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    text(page, 100, 258, "A note overlapping the field column", size=9)
    text(page, 40, 272, "Accepted: 9 April 2024", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    assert "Received: 3 March 2024" in body
    assert "Accepted: 9 April 2024" in body


def test_top_stack_with_one_label_stays_flow(tmp_path):
    """The Frontiers `TYPE / PUBLISHED / DOI` shape — one known label
    in the stack is never a box. Placed below the signature edge zone
    so the stack renders as ordinary body text."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A top-stack control study", size=18)
    text(page, 40, 180, "TYPE", size=9)
    text(page, 40, 196, "PUBLISHED", size=9)
    text(page, 40, 212, "DOI", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    for needle in ("TYPE", "PUBLISHED", "DOI"):
        assert needle in body


def test_fields_past_page_one_are_ignored(tmp_path):
    """Labels on page 2+ are out of the detector's front-matter scan."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A late-fields control study", size=18)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    for _ in range(2):
        page = _page(doc)
        block(page, 180, 300, BODY_BLOCK, leading=14)
    page = _page(doc)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    text(page, 40, 264, "Accepted: 9 April 2024", size=9)
    block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 0
    body = chapter_body_text(out)
    assert "Received: 3 March 2024" in body
    assert "Accepted: 9 April 2024" in body


def test_prose_below_box_and_straddler_stay_body(tmp_path):
    """A block straddling the frozen core boundary stays whole in body
    flow, and the paragraph under a valid box never joins — outside
    attachments need a media anchor. (A media straddler stands in for
    text: in extraction any text line overlapping a field's band fuses
    into the field's own block, so a separate crossing block can only
    be non-text.)"""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "A boundary control study", size=18)
    text(page, 400, 240, "Received: 3 March 2024", size=9)
    text(page, 400, 264, "Accepted: 9 April 2024", size=9)
    # crosses the core's left boundary — not fully inside, not in an
    # outside attach window: stays a body-flow image
    image(page, (390, 250, 460, 280))
    block(
        page, 400, 320,
        ("The body paragraph right below the box keeps its",
         "place in the ordinary reading flow untouched."),
        leading=14,
    )
    block(page, 180, 400, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 1
    aside = _asides(html)[0]
    assert "<img" not in aside
    assert "right below the box" not in aside
    body = chapter_body_text(out)
    assert "right below the box" in body
    # the straddler renders as an ordinary body-flow image
    assert "<img" in _ASIDE_RE.sub("", html)


def test_signature_furniture_is_not_absorbed(tmp_path):
    """A recurring head within the attach window can't join — text
    attachments need a media anchor, and furniture never is one."""
    doc = fitz.open()
    for _ in range(2):
        page = _page(doc)
        text(page, 40, 30, "J TEST RUNNING HEAD", size=8)
        text(page, 40, 700, "Received: 3 March 2024", size=9)
        text(page, 40, 724, "Accepted: 9 April 2024", size=9)
        text(page, 40, 750, "J TEST RUNNING FOOT", size=8)
        block(page, 180, 300, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, html = _convert(pdf, out)
    assert stats["asides"] == 2
    for aside in _asides(html):
        assert "RUNNING" not in aside
    # the unclaimed signatures still drop as furniture
    assert "RUNNING" not in chapter_body_text(out)


def test_citation_run_cap_bounds_growth(tmp_path):
    """At the run cap the wraps all join the box; one line over the
    cap leaves a leftover that barriers the next field — no box.
    14pt spacing keeps every wrap a separate block in extraction."""
    for wraps, expected in ((7, 1), (8, 0)):
        doc = fitz.open()
        page = _page(doc)
        text(page, 180, 110, "A run-cap control study", size=18)
        text(page, 40, 240, "Citation: Doe J, Smith K.", size=8)
        for i in range(wraps):
            text(
                page, 40, 254 + i * 14,
                f"wrapped citation line {i}", size=8,
            )
        text(page, 40, 368, "Accepted: 9 April 2024", size=8)
        block(page, 180, 500, BODY_BLOCK, leading=14)
        pdf = _save(doc, tmp_path / f"input{wraps}.pdf")
        out = tmp_path / f"out{wraps}.epub"

        stats, html = _convert(pdf, out)
        assert stats["asides"] == expected
        body = chapter_body_text(out)
        if expected:
            aside = _asides(html)[0]
            assert f"wrapped citation line {wraps - 1}" in aside
            assert "Accepted: 9 April 2024" in aside
        else:
            assert "Accepted: 9 April 2024" in body
            assert f"wrapped citation line {wraps - 1}" in body


def test_box_does_not_vote_a_column_edge(tmp_path):
    """The claimed field column never seeds a document edge."""
    doc = fitz.open()
    for _ in range(2):
        page = _page(doc)
        text(page, 40, 240, "Received: 3 March 2024", size=9)
        text(page, 40, 264, "Accepted: 9 April 2024", size=9)
        block(page, 180, 300, BODY_BLOCK, leading=14)
        block(page, 180, 460, BODY_BLOCK, leading=14)
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"

    stats, _html = _convert(pdf, out)
    assert stats["asides"] == 2
    assert stats["columns"] == 1
    with fitz.open(pdf) as d:
        pages = [(p, p.get_text("dict")) for p in d]
        _bs, line_h = _body_font_size(pages)
        _detect_asides(pages, line_h, set(), lang=EN)
        # the marked field column is invisible to edge voting —
        # unmarked it would cluster a phantom edge at x40
        assert _doc_column_edges(pages, set()) == pytest.approx([180.0])


def test_eval_counts_aside_text_as_retained(tmp_path):
    """The evaluator sees aside text as kept content; a real loss
    still trips the coverage gate."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, "An eval-retention information study", size=18)
    text(page, 40, 240, "Received: 3 March 2024", size=9)
    text(page, 40, 264, "Accepted: 9 April 2024", size=9)
    block(
        page, 180, 300,
        ("The eval fixture carries a unique body phrase for loss",
         "detection, so word coverage can prove it survived."),
        leading=14,
    )
    pdf = _save(doc, tmp_path / "input.pdf")
    out = tmp_path / "out.epub"
    convert(pdf, out)

    r = _run_eval(pdf, out)
    assert r.returncode == 0, r.stdout + r.stderr

    _delete_epub_text(out, "a unique body phrase")
    r = _run_eval(pdf, out)
    assert r.returncode == 1, r.stdout + r.stderr
