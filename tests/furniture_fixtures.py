"""From-scratch PDFs isolating furniture thresholds and front-matter intrusions."""
from __future__ import annotations

import fitz
from builders import _page, _save, block, image, text

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

STACKED_HEAD_LINES = (
    "Stacked Journal Testing",
    "Volume Twelve Issue Three",
)
STACKED_TITLE_LINES = (
    "A study of stacked running heads",
    "with a fused block on page one",
)
STACKED_TITLE = " ".join(STACKED_TITLE_LINES)
STACKED_BODY = (
    "Stacked-head body page {} retains ordinary evidence below the title.",
    "The sentences keep the article's argument in the normal body flow.",
    "Stacked-head body page {} closes with a separate retained result.",
    "Its prose outweighs the small running heads in every font vote.",
)

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

UPRIGHT_EDGE_LINE = "Edge prose kept"
UPRIGHT_EDGE_LINES = ("EDGE MARGIN LINE ONE", "EDGE MARGIN LINE TWO")
# per-page letter-word tags: signatures ignore digits, so a "90"/"270"
# suffix would not differentiate the two pages' edge texts
UPRIGHT_TAGS = {90: "ninety", 270: "twoseventy"}
UPRIGHT_BODY = (
    "Body prose reads upright on the rotated page.",
    "Its lines report the mediabox direction a rotated",
    "page needs for displayed horizontal text.",
    "Nothing about the margin neighbors may remove them.",
)

TABLE_CELLS = tuple(f"CELLCOL{i} DATA WORDS" for i in range(4))
AXIS_LABEL = "Y AXIS"
TABLE_BODY = (
    "Body prose below the rotated table keeps ordinary text.",
    "The grid above it can only survive as a raster image.",
)

DUAL_STRIPS = (
    "DualLeftA Journal Of Tests",
    "DualLeftB More Margin Text",
    "DualRightC Vol 12",
)
DUAL_STRIP_X = (30, 50, 575)
DUAL_BODY = (
    "Ordinary body prose sits inside the union of the margin strips",
    "and must survive as text rather than rasterize into a table image.",
    "Several sentences keep the block wide enough to intersect the span",
    "the three rotated sidebars project across the page.",
)

STAG_CELLS = (
    "StagLeftA Journal Tests",
    "StagLeftB More Margin",
    "StagRightC Vol 12",
    "StagBodyD Data Words",
    "StagBodyE Other Cells",
)
STAG_CELL_X = (30, 50, 575, 300, 320)
STAG_CELL_BASE = (360, 360, 360, 460, 460)
STAG_BODY = (
    "Body prose below the staggered rotated table stays text.",
    "The grid above it can only survive as a raster image.",
)

OVER_CHAR_HEAD = (
    ("Specimen identifier collection site coordinates depth measured "
     "values replicate counts treatment group dosage regimen"),
    ("exposure window response score and adjusted confidence intervals "
     "laboratory batch instrument serial calibration notes"),
    ("reference and quality control outcomes for the continued table "
     "with supplementary long column labels and provenance"),
)
OVER_LINE_HEAD = tuple(f"Column {i} label" for i in range(1, 8))
OVER_BOUND_BODY = tuple(
    f"Over-bound body page {{}} keeps ordinary body-size line {i} in flow."
    for i in range(1, 14)
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


def stacked_fused_heads(path):
    """Two independently-recurring heads — separate edge-zone blocks on
    later pages, fused ahead of the title in one block on page 1 — leave
    a post-split remainder that is itself signature furniture (#46)."""
    doc = fitz.open()
    for number in range(1, 4):
        page = _page(doc)
        if number == 1:
            # adjacent inserts fuse into one block — heads at head size,
            # title at title size, like the real journal fusion
            text(page, 72, 100, STACKED_HEAD_LINES[0], size=9)
            text(page, 72, 112, STACKED_HEAD_LINES[1], size=9)
            block(page, 72, 131, STACKED_TITLE_LINES, size=18, leading=21)
        else:
            text(page, 72, 100, STACKED_HEAD_LINES[0], size=9)
            text(page, 72, 140, STACKED_HEAD_LINES[1], size=9)
        block(
            page, 72, 240,
            tuple(part.format(number) for part in STACKED_BODY[:2]),
            leading=14,
        )
        block(
            page, 72, 340,
            tuple(part.format(number) for part in STACKED_BODY[2:]),
            leading=14,
        )
    return _save(doc, path)


def rotated_page_upright_margin(path):
    """Two /Rotate pages whose margin blocks display upright text.

    On a rotated page the stamp's own geometry still lands tall and
    narrow in the mediabox margin, but lines inserted with the page's
    own rotation report the mediabox dir that maps to *displayed*
    upright — the upright veto must keep them in flow: a <25pt
    strip-shaped block the legacy rule would drop, a >25pt multi-line
    block, and ordinary body prose sharing the same reported dir.
    """
    doc = fitz.open()
    for rotation in (90, 270):
        page = _page(doc)
        page.set_rotation(rotation)
        # texts differ per page: identical blocks at the same edge on
        # both pages would recur into the signature layer and leave
        # through that arm instead of testing the margin-strip veto
        tag = UPRIGHT_TAGS[rotation]
        block(page, 150, 300,
              tuple(f"{line} {rotation}" for line in UPRIGHT_BODY),
              rotate=rotation)
        text(page, 35, 620, f"{UPRIGHT_EDGE_LINE} {tag}",
             size=10, rotate=rotation)
        text(page, 580, 620,
             "\n".join(f"{line} {tag}" for line in UPRIGHT_EDGE_LINES),
             size=9, rotate=rotation)
    return _save(doc, path)


def rotated_table_margin_cells(path):
    """A rotated table whose outer columns sit inside the outer margins.

    Four tall rotated cells share a y-band; the outermost pair crosses
    the 10% margin lines but the region reaches the body band, so the
    cells must be claimed into the raster — never dropped as margin
    strips. A rotated in-figure label sits inside the body band below
    the region and stays plain text.
    """
    doc = fitz.open()
    page = _page(doc)
    for cell, x in zip(TABLE_CELLS, (30, 90, 150, 575)):
        text(page, x, 500, cell, size=10, rotate=90)
    # the label sits above the region in the body band — inside its x
    # span it would be claimed into the raster like the cells
    text(page, 320, 360, AXIS_LABEL, size=9, rotate=90)
    block(page, 72, 610, TABLE_BODY, leading=14)
    return _save(doc, path)


def dual_margin_strips(path):
    """Two left + one right rotated sidebars sharing a y-band.

    Their union trivially spans both outer margins, so a union-span
    test lets the strips exempt themselves into a fake rotated table
    that rasterizes intersecting body prose away. The band-member gate
    must refuse the clip — every band member sits in a margin — and all
    three strips drop as furniture while the body stays text.
    """
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 100, "A Dual Margin Study", size=18)
    for x, cell in zip(DUAL_STRIP_X, DUAL_STRIPS):
        text(page, x, 500, cell, size=10, rotate=90)
    block(page, 72, 380, DUAL_BODY, leading=14)
    return _save(doc, path)


def rotated_table_staggered_cells(path):
    """A rotated table whose margin cells open the band and whose body
    cells join later — three left/right margin cells span the upper band
    (the first >=3-overlap point has only margin members), two inner
    cells join lower, so membership must be sampled across every band
    range or the outer cells lose their clip and drop as strips."""
    doc = fitz.open()
    page = _page(doc)
    text(page, 72, 100, "A staggered rotated table", size=18)
    for x, base, cell in zip(STAG_CELL_X, STAG_CELL_BASE, STAG_CELLS):
        text(page, x, base, cell, size=10, rotate=90)
    block(page, 72, 620, STAG_BODY, leading=14)
    return _save(doc, path)


def over_bound_running_head(path):
    """Two-page doc whose repeated heads each overflow one bound arm —
    a >300-letter signature and a 7-line block — so neither may enter
    the boilerplate set and both stay in flow."""
    doc = fitz.open()
    for number in range(2):
        page = _page(doc)
        block(page, 72, 12, OVER_CHAR_HEAD, size=6, leading=6)
        block(page, 72, 60, OVER_LINE_HEAD, size=6, leading=6)
        block(
            page, 72, 200,
            tuple(part.format(number + 1) for part in OVER_BOUND_BODY),
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


BOTTOM_FOOT = "Preprint posted under license terms - Example Journal"
BOTTOM_COPY_TAGS = ("Alpha", "Beta", "Gamma")
BOTTOM_BODY = (
    "Bottom-foot body page {} opens with ordinary scientific evidence.",
    "The measurements describe a continuous argument in the article body.",
    "All observations remain useful even when the repeated foot is removed.",
    "Bottom-foot body page {} closes with an independently retained result.",
    "The control paragraphs outweigh the small running foot in font voting.",
    "Their positions also establish one stable document column above the edge.",
)

# Single-block-page (book-scan) shapes. Every text page is ONE dict block:
# one insert call at body size/leading, heads fused as leading lines. The
# alternating heads recur below the signature floor — each leading sig
# appears 2-3 times against a 12-page floor of 4 — so only folio evidence,
# not recurrence, can separate them from body.
SBP_TITLE_HEAD = "Journal of Synthetic Layouts"
SBP_AUTHOR_HEAD = "Jane Q. Tester"
SBP_AUTHOR_VARIANT = "TESTER"
SBP_TITLE = "Fused Evidence and Synthetic Heads"
SBP_HEAD_BLANK = " "
SBP_OPENER = (
    "7",
    "___________________",
    " ",
    SBP_AUTHOR_HEAD.upper(),
    "TEST UNIVERSITY",
    " ",
    "Introduction",
    "The opener paragraph presents a general framework for the study.",
    "Its byline line above carries no folio and must survive intact.",
)
SBP_BODY_LINES = (
    "Synthetic {} body page {} line {} keeps the measured argument moving.",
    "Every retained sentence {} {} {} reports evidence the removal must spare.",
    "This filler row {} {} {} carries the block toward a realistic page depth.",
    "No line in the body column {} {} {} is signed as running furniture.",
)
# letter-varied per-page tags: digit-stripped signatures would otherwise
# collapse every page's filler into one whole-block signature
SBP_PAGE_TAGS = (
    "Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot",
    "Golf", "Hotel", "India", "Juliet", "Kilo", "Lima",
)
SBP_FOOTNOTE_LINES = (
    "___________________",
    "1. A trailing note at smaller size belongs to the page,",
    "   and must stay out of any trailing-head scan.",
)


def _sbp_insert(page, x, y, lines, *, size=10, leading=13.4):
    """One insert per page: same-x0 lines fuse into the scan's single block."""
    page.insert_text(
        (x, y), "\n".join(lines), fontsize=size,
        lineheight=leading / size,
    )


def _sbp_body(pi, count=44):
    """``count`` per-page-unique filler lines — one long block per page."""
    tag = SBP_PAGE_TAGS[pi % len(SBP_PAGE_TAGS)]
    return [
        SBP_BODY_LINES[i % len(SBP_BODY_LINES)].format(tag, pi, i)
        for i in range(count)
    ]


def single_block_book_heads(path):
    """A 12-page book-scan shape: each text page extracts as one
    page-long block, alternating folio'd running heads sit as leading
    lines below the signature floor (title head 3x, author head 2x
    against a floor of 4), and the opener carries a chapter number,
    rule, and folio-less byline that must survive. Residual shapes —
    a suffix head, an internal head, a same-y separate-line folio
    variant, and a folio-less head — reproduce the measured edge
    cases of the motivating scan."""
    doc = fitz.open()
    folio_of = {1: 138, 2: 139, 3: 140, 4: 141, 6: 143, 7: 144, 9: 146}
    for pi in range(12):
        page = _page(doc)
        if pi == 0:
            # opener: folio-free chapter number, rule, byline,
            # affiliation, the chapter title, then the Introduction
            # label and body — all inside one page-long block
            lines = [SBP_OPENER[0], SBP_OPENER[1], *SBP_OPENER[2:6],
                     SBP_TITLE, *SBP_OPENER[6:], *_sbp_body(pi, 40)]
            _sbp_insert(page, 54, 48, lines)
            continue
        if pi == 5:
            # head-less image page
            image(page, (150, 200, 400, 450))
            _sbp_insert(page, 54, 500, tuple(_sbp_body(pi, 8)))
            continue
        if pi == 7:
            # same-y separate-line folio variant: folio and partial
            # head text are two line objects on the head row; the body
            # insert below fuses all of it into the page's one block
            head_y = 48
            text(page, 54, head_y, str(folio_of[pi]), size=10)
            text(page, 400, head_y, SBP_AUTHOR_VARIANT, size=10)
            _sbp_insert(page, 54, head_y + 13.4,
                        [SBP_HEAD_BLANK] + _sbp_body(pi))
            continue
        body = _sbp_body(pi)
        if pi == 9:
            # internal head at line index 10, outside the edge zone
            lines = body[:10] + [
                f"{folio_of[pi]}  {SBP_AUTHOR_HEAD.upper()}"] + body[10:]
        elif pi == 6:
            # trailing (suffix) head at the block's end — after a
            # footnote-shaped region that must also stay put
            lines = body + list(SBP_FOOTNOTE_LINES)
            lines += [SBP_HEAD_BLANK,
                      f"{SBP_TITLE_HEAD}     {folio_of[pi]}"]
        elif pi == 8:
            # folio-less leading head — the p27 residual shape
            lines = [SBP_TITLE_HEAD, SBP_HEAD_BLANK] + body
        elif pi >= 10:
            # head-less body pages
            lines = body
        else:
            head = (
                f"{SBP_TITLE_HEAD}     {folio_of[pi]}"
                if pi % 2 else
                f"{folio_of[pi]}  {SBP_AUTHOR_HEAD.upper()}"
            )
            lines = [head, SBP_HEAD_BLANK] + body
        _sbp_insert(page, 54, 48, lines)
    return _save(doc, path)


def single_block_section_heads(path):
    """Body-size section heads fused inside page-long blocks (#74):
    an isolated Title-Case head, an all-caps head, a wrapped caps
    pair, a vocabulary `Summary`, a roman-numeral `IV`, the next
    chapter's `CHAPTER FIVE` (its `chapter` needle is an exact match
    only), and a head behind a marker-opening page — each separated
    by blank lines from its body — promote to h2, while the traps
    stay prose: the opener's isolated byline names (pages 0–1 admit
    vocabulary hits only), a folio-less residual repeating the
    committed `CHAPTER 4` head, the same committed `ROBERT TRIVERS`
    head wrap-split into a `ROBERT`/`TRIVERS` pair (the unit's joined
    needle vetoes it), a period-ending isolated line, a
    paragraph-final short line, under-floor tokens `ON`/`D`/`DID`
    (a lone letter is a drop cap and `DID` is no numeral), and the
    caption-shaped `PLATE IV`/`PLATE IV-B`."""
    doc = fitz.open()
    folio_of = {1: 138, 2: 139, 3: 140}
    for pi in range(6):
        page = _page(doc)
        if pi == 0:
            lines = [
                "1", "___________________", SBP_HEAD_BLANK,
                "JANE Q. TESTER", "TEST UNIVERSITY", SBP_HEAD_BLANK,
                "Fused Evidence and Synthetic Layouts", SBP_HEAD_BLANK,
                "Introduction", SBP_HEAD_BLANK,
            ] + _sbp_body(pi, 35)
            _sbp_insert(page, 54, 48, lines)
            continue
        body = _sbp_body(pi)
        # folio rows cluster on the 137 offset whatever their text —
        # a multi-word committed head seeds the wrap-split veto
        head = (
            [
                f"{folio_of[pi]}  ROBERT TRIVERS"
                if pi == 2 else f"{folio_of[pi]}  CHAPTER 4",
                SBP_HEAD_BLANK,
            ]
            if pi in folio_of else []
        )
        if pi == 2:
            lines = head + body[:6] + [
                SBP_HEAD_BLANK, "Isolated Section Head", SBP_HEAD_BLANK,
            ] + body[6:16] + [
                SBP_HEAD_BLANK, "CAPS SECTION HEAD", SBP_HEAD_BLANK,
            ] + body[16:22] + [
                SBP_HEAD_BLANK, "WRAPPED CAPS", "SECTION HEAD",
                SBP_HEAD_BLANK,
            ] + body[22:30]
        elif pi == 3:
            lines = head + body[:10] + [
                SBP_HEAD_BLANK, "Summary", SBP_HEAD_BLANK,
            ] + body[10:18] + [
                # a folio-less residual repeating the committed head —
                # the exact needle match keeps it prose
                SBP_HEAD_BLANK, "CHAPTER 4", SBP_HEAD_BLANK,
            ] + body[18:26] + [
                # containment once vetoed this too — exact-match
                # needles leave the next chapter's head promotable
                SBP_HEAD_BLANK, "CHAPTER FIVE", SBP_HEAD_BLANK,
            ] + body[26:34]
        elif pi == 4:
            # the traps page stays inside the 792pt sheet — ~54 lines
            # at the fixture's 13.4pt pitch from y=48
            lines = body[:6] + [
                SBP_HEAD_BLANK, "University Press.", SBP_HEAD_BLANK,
            ] + body[6:14] + [
                # paragraph-final short line — no isolation, no head
                "Short tail",
            ] + body[14:22] + [
                # a bare two-letter caps token is under the floor…
                SBP_HEAD_BLANK, "ON", SBP_HEAD_BLANK,
            ] + body[22:26] + [
                # …but a roman numeral is a real head in this domain
                SBP_HEAD_BLANK, "IV", SBP_HEAD_BLANK,
            ] + body[26:30] + [
                # caption-shaped, not a section head
                SBP_HEAD_BLANK, "PLATE IV", SBP_HEAD_BLANK,
                # a sub-plate dash suffix is still a caption
                SBP_HEAD_BLANK, "PLATE IV-B", SBP_HEAD_BLANK,
                # the committed `ROBERT TRIVERS` head, wrap-split —
                # the unit's joined needle vetoes the pair
                SBP_HEAD_BLANK, "ROBERT", "TRIVERS", SBP_HEAD_BLANK,
                # a lone letter is a drop cap; `DID` is no numeral
                SBP_HEAD_BLANK, "D", SBP_HEAD_BLANK,
                SBP_HEAD_BLANK, "DID",
            ]
        elif pi == 5:
            # a marker-opening block (a carried-over footnote) can't
            # veto the page's heads — only its own marker lines
            lines = [
                "* A carried-over footnote opens this page's block,",
                "its marker line alone can never be a head.",
                SBP_HEAD_BLANK,
            ] + body[:10] + [
                SBP_HEAD_BLANK, "Marker Page Head", SBP_HEAD_BLANK,
            ] + body[10:30]
        else:
            lines = head + body
        _sbp_insert(page, 54, 48, lines)
    return _save(doc, path)


def stacked_digit_heads(path):
    """One page opens with three in-zone folio'd head-shaped rows whose
    offsets cluster — the quorum counts *distinct pages*, so a lone
    stacked page can never satisfy it and all three lines stay in
    body flow."""
    doc = fitz.open()
    for pi in range(6):
        page = _page(doc)
        lead = (
            [
                "138 STACKED JOURNAL HEAD",
                "138 SECOND STACKED HEAD",
                "139 THIRD STACKED HEAD",
            ]
            if pi == 0 else []
        )
        _sbp_insert(page, 54, 48, lead + _sbp_body(pi))
    return _save(doc, path)


def lowercase_digit_openers(path):
    """Every page opens on a lowercase filler line ending in a small
    digit — prose, not a head. The digits sit inside the cluster's
    ±1 window on early pages without tracking anything; a lowercase
    remainder keeps the line out of candidacy at either folio edge."""
    doc = fitz.open()
    for pi in range(6):
        page = _page(doc)
        _sbp_insert(
            page, 54, 48,
            [f"filler words carry the page {pi}"] + _sbp_body(pi),
        )
    return _save(doc, path)


def multi_block_folio_tops(path):
    """Every page carries a tracking folio'd head in its own small top
    block — a multi-block page, not a single-block scan. The span
    gate skips the page outright, so even a doc-wide folio cluster
    commits nothing through the folio pass."""
    doc = fitz.open()
    for pi in range(6):
        page = _page(doc)
        text(page, 200, 48, f"{138 + pi}  MULTI HEAD", size=10)
        _sbp_insert(page, 54, 200, _sbp_body(pi, 30))
    return _save(doc, path)


RECURRING_OPENER = "The experiment continues with further measurements below."


def recurring_page_opener(path):
    """Every page opens with one identical sentence — 100% recurrence at
    the page top inside a page-long block, but shared *content*, not
    furniture. A rule that keys on bare line recurrence would strip it;
    it must stay in flow."""
    doc = fitz.open()
    for pi in range(6):
        page = _page(doc)
        _sbp_insert(page, 54, 48, [RECURRING_OPENER] + _sbp_body(pi))
    return _save(doc, path)


NUMBERED_TOP_LINES = (
    "Figure {}.",
    "Table {}.",
    "Column {} label",
)


def numbered_page_top_lines(path):
    """Pages open with numbered lines whose digit-stripped signatures
    collapse to one token ('figure', 'table', 'columnlabel') recurring at
    the page top: a naive line collector learns the shared signature and
    eats the labels. They are ordinary content and must stay in flow."""
    doc = fitz.open()
    for pi in range(9):
        page = _page(doc)
        first = NUMBERED_TOP_LINES[pi % 3].format(pi // 3 + 1)
        _sbp_insert(page, 54, 48, [first] + _sbp_body(pi))
    return _save(doc, path)


def repeated_bottom_foot(path):
    """Three pages carrying a repeated bottom running foot plus a
    per-page-varying © line — the footer's signature recurs inside the
    bottom signature zone (signature arm) while each © line differs and
    matches furniture content in the outer 7% edge (pattern arm), so
    both bottom halves of the furniture mirror must excuse them."""
    doc = fitz.open()
    for number, tag in enumerate(BOTTOM_COPY_TAGS, 1):
        page = _page(doc)
        if number == 1:
            text(page, 72, 100, "A Bottom Footer Study", size=18)
        block(
            page, 72, 200,
            tuple(part.format(number) for part in BOTTOM_BODY[:3]),
            leading=14,
        )
        block(
            page, 72, 300,
            tuple(part.format(number) for part in BOTTOM_BODY[3:]),
            leading=14,
        )
        text(page, 72, 768, BOTTOM_FOOT, size=8)
        text(page, 400, 782, f"© 2024 Example Journal page {tag}", size=8)
    return _save(doc, path)


def tall_top_folio_tops(path):
    """Multi-block pages whose top block still dominates the height:
    a tall block holding the folio'd head plus body lines, and a
    small secondary block below. The span gate scans them — it bounds
    the heuristic to top-block-dominated pages, not single-block
    ones — so the tracking heads commit exactly like a fused
    one-block page's."""
    doc = fitz.open()
    for pi in range(3):
        page = _page(doc)
        _sbp_insert(
            page,
            54,
            48,
            [f"{138 + pi} JANE Q. TESTER"] + _sbp_body(pi, 30),
        )
        text(page, 300, 700, "a small trailing block", size=10)
    return _save(doc, path)


def folio_head_aside_seed(path):
    """Two pages whose top blocks each carry two distinct info-label
    lines ('EDITED BY … folio', 'RECEIVED …') — enough labels to seed
    a self-contained info box on a scanned page. Page 0's block is
    marked ``folio_head`` in the test: seeding is the last aside
    membership path a committed head could take, and the exemption
    must close it while the unmarked page-1 twin still claims."""
    doc = fitz.open()
    for pi in range(2):
        page = _page(doc)
        _sbp_insert(
            page,
            54,
            48,
            [
                f"EDITED BY Jane Q. Tester  {138 + pi}",
                "RECEIVED 12 January 2024",
            ],
        )
    return _save(doc, path)
