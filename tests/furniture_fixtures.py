"""From-scratch PDFs isolating furniture thresholds and front-matter intrusions."""
from __future__ import annotations

import fitz
from builders import _page, _save, block, text

ROTATED_STAMP = "arXiv:2412.00001v1 [cs.LG] 8 Nov 2024"
ROTATED_PREFIX = (
    "Reliable measurements connect local geometry to the full spectrum",
    "while the surrounding argument stays inside one uninterrupted paragraph",
    "before the calculation identifies a charac-",
)
ROTATED_SUFFIX = (
    "ter table that closes the central argument and preserves its full meaning.",
    "A further sentence keeps the continuation in the same extracted block.",
)
ROTATED_PARAGRAPH = (
    "Reliable measurements connect local geometry to the full spectrum "
    "while the surrounding argument stays inside one uninterrupted paragraph "
    "before the calculation identifies a character table that closes the central "
    "argument and preserves its full meaning. A further sentence keeps the "
    "continuation in the same extracted block."
)
NORMAL_MARGIN_LINES = ("Margin", "prose", "stays.")
NORMAL_MARGIN_TEXT = "Margin prose stays."

LONG_HEAD_LINES = (
    "This preprint is made available under a Creative Commons",
    "Attribution NonCommercial NoDerivatives International license",
    "The author and funder grant the host permission to display",
    "this manuscript in perpetuity without peer review certification",
    "and no reuse is allowed without permission from the rights holder",
)

FUSED_HEAD_LINES = (
    "Article",
    "https://doi.org/10.1038/s41467-024-51696-w",
)
FUSED_TITLE_LINES = (
    "Self assembled structures protect",
    "the air water interface in cryoEM",
)
FUSED_TITLE = " ".join(FUSED_TITLE_LINES)

INFO_TITLE = "Evidence across connected measurements"
INFO_FIELDS = ("Received: 3 March 2024", "Accepted: 9 April 2024")
INFO_PREFIX = (
    "Abstract evidence connects the earliest measurements to the later results",
    "while the same line of reasoning keeps its central claim open until",
    "the final",
)
INFO_SUFFIX = (
    "observation establishes the conclusion without interrupting the argument.",
    "The surrounding prose remains part of this single abstract paragraph.",
)
INFO_PARAGRAPH = " ".join((*INFO_PREFIX, *INFO_SUFFIX))
INFO_BODY_PROSE = (
    "Ordinary prose below the information fields must remain outside any lifted box."
)
INLINE_RECEIVED_PROSE = (
    "The Methods paragraph mentions Received: as ordinary prose rather than a field."
)


def rotated_margin_stamp(path):
    """Put a 27pt vertical stamp between the blocks of one hyphenated paragraph."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 100, "A margin stamp threshold control", size=18)
    block(page, 578, 180, NORMAL_MARGIN_LINES, size=7.5, leading=10)
    block(page, 72, 235, ROTATED_PREFIX, leading=14)
    text(page, 32.5, 625, ROTATED_STAMP, size=19.6, rotate=90)
    block(page, 72, 320, ROTATED_SUFFIX, leading=14)
    return _save(doc, path)


def long_repeated_running_head(path):
    """Repeat one five-line license head whose 265-letter signature exceeds 80."""
    doc = fitz.open()
    for number in range(1, 4):
        page = _page(doc)
        block(page, 72, 12, LONG_HEAD_LINES, size=6, leading=6)
        if number == 1:
            text(page, 72, 120, "Running head threshold control", size=18)
        block(
            page, 72, 220,
            (
                f"Long-head body page {number} opens with ordinary scientific evidence.",
                "The measurements describe a continuous argument in the article body.",
                "All observations remain useful even when the repeated license is removed.",
            ),
            leading=14,
        )
        block(
            page, 72, 340,
            (
                f"Long-head body page {number} closes with an independently retained result.",
                "The control paragraphs outweigh the small running head in font voting.",
                "Their positions also establish one stable document column below the edge.",
            ),
            leading=14,
        )
    return _save(doc, path)


def fused_running_head_title(path):
    """Fuse two head lines with a large title while later pages repeat only the head."""
    doc = fitz.open()
    doc.set_metadata({"title": FUSED_TITLE})
    for number in range(1, 4):
        page = _page(doc)
        text(page, 40, 105, FUSED_HEAD_LINES[0], size=12)
        text(page, 398, 112, FUSED_HEAD_LINES[1], size=9)
        if number == 1:
            block(page, 40, 139, FUSED_TITLE_LINES, size=18, leading=21)
        block(
            page, 72, 230,
            (
                f"Fused-head body page {number} retains the evidence below the article title.",
                "The normal text determines the body font independently of the front matter.",
                "Its complete sentences should not be mistaken for authors or running heads.",
            ),
            leading=14,
        )
        block(
            page, 72, 340,
            (
                f"Fused-head body page {number} preserves a second block of article prose.",
                "The first page alone adds the title to the otherwise identical running head.",
                "This final sentence supplies a separate anchor for the single body column.",
            ),
            leading=14,
        )
    return _save(doc, path)


def front_matter_info_box(path):
    """Stack two fields beside a split abstract without creating a phantom column."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 180, 110, INFO_TITLE, size=18)
    block(page, 180, 180, INFO_PREFIX, size=9, leading=12)
    text(page, 40, 242, INFO_FIELDS[0], size=9)
    text(page, 40, 266, INFO_FIELDS[1], size=9)
    block(page, 180, 302, INFO_SUFFIX, size=9, leading=12)
    block(
        page, 180, 410,
        (
            INFO_BODY_PROSE,
            "This neighboring paragraph is article content, not a field continuation.",
        ),
        leading=14,
    )
    for number in range(2, 5):
        page = _page(doc)
        block(
            page, 180, 220,
            (
                f"Info-box body page {number} continues with ordinary article content.",
                INLINE_RECEIVED_PROSE,
                "A label mentioned inside a sentence must not become a front-matter field.",
            ),
            leading=14,
        )
        block(
            page, 180, 340,
            (
                f"Info-box body page {number} closes with another complete scientific result.",
                "Later pages establish the body column without any left-side labeled stack.",
                "Only the first page carries the two adjacent publication-history fields.",
            ),
            leading=14,
        )
    return _save(doc, path)
