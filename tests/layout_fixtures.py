"""Regenerable layout shapes; every PDF starts empty and uses only shared builders."""
from __future__ import annotations

import fitz
from builders import _page, _save, block, drawing, image, paragraph, text


def mixed_1col_plus_2col(path):
    """A minority two-column page exposes the document-wide grid's zipper order (#37)."""
    doc = fitz.open()
    for number in range(4):
        page = _page(doc)
        for y, label in ((190, "opening"), (410, "closing")):
            paragraph(page, (72, y, 540, y + 100),
                       f"Single page {number} {label} contains ordinary flowing prose. "
                       "The majority layout must not depend on a real source PDF.")
    page = _page(doc)
    for x, side in ((72, "Left"), (340, "Right")):
        for y, label in ((190, "upper"), (410, "lower")):
            paragraph(page, (x, y, x + 210, y + 100),
                       f"{side} {label} minority paragraph has several wrapped lines. "
                       "Its continuation belongs to this column alone.")
    return _save(doc, path)


def magazine_wrap(path):
    """Far-side wrap text currently follows resumed full-width prose (#38)."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 180, 540, 240),
               "Wrap opening starts the normal single-column article before the float.")
    image(page, (230, 290, 340, 440))
    for x, side in ((72, "Near"), (365, "Far")):
        for y, label in ((310, "upper"), (390, "lower")):
            text(page, x, y, f"{side} {label} wrap text")
    paragraph(page, (72, 510, 540, 590),
               "Resumed body belongs after both sides of the wrapped float. "
               "It must not jump ahead of the far-side lines.")
    return _save(doc, path)


def scan_2up(path):
    """A symmetric two-up spread already reads left page then right page (#39)."""
    doc = fitz.open()
    page = _page(doc, width=1000)
    for x, side in ((60, "Left"), (580, "Right")):
        for y, label in ((180, "opening"), (360, "middle"), (650, "closing")):
            paragraph(page, (x, y, x + 330, y + 80),
                       f"{side} spread {label} contains readable sentences on its own page. "
                       "The central seam has no text crossing it.")
    return _save(doc, path)


def vector_figure(path):
    """Drawn axes with short labels are not an image and currently leak into prose (#35)."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
               "Vector body precedes the plotted results and must remain ordinary prose.")
    drawing(page, (180, 290, 440, 480))
    for y, label in ((330, "VECTORONE"), (390, "VECTORTWO"), (450, "VECTORTHREE")):
        text(page, 210, y, label)
    text(page, 72, 520, "Figure 1. Vector trend.")
    paragraph(page, (72, 570, 540, 640),
               "Vector continuation explains the result without incorporating axis labels.")
    return _save(doc, path)


def raster_figure(path):
    """Overlay text and exterior panel letters currently survive beside a raster (#35)."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 190, 540, 250),
               "Raster body precedes the figure and must remain ordinary prose.")
    image(page, (180, 290, 440, 470))
    text(page, 210, 350, "RASTERONE")
    text(page, 300, 420, "RASTERTWO")
    text(page, 164, 310, "A")
    text(page, 447, 450, "B")
    text(page, 72, 520, "Figure 2. Raster panels.")
    paragraph(page, (72, 570, 540, 640),
               "Raster continuation discusses the panels without inline label fragments.")
    return _save(doc, path)


def outdented_heads(path):
    """Wide reference-style spans establish x36; x42 heads then precede all x166 body (#43)."""
    doc = fitz.open()
    for number in range(6):
        page = _page(doc)
        if number < 3:
            for y, label in ((190, "first"), (450, "last")):
                paragraph(page, (36, y, 576, y + 70),
                           f"Reference span {number} {label} retains its complete full-width "
                           "bibliographic content across the page, beyond the body edge.")
            for y, label in ((290, "upper"), (550, "lower")):
                paragraph(page, (166, y, 576, y + 70),
                           f"Reference body {number} {label} keeps ordinary explanatory prose.")
        else:
            for y, label in ((210, "opening"), (390, "middle"), (570, "closing")):
                paragraph(page, (166, y, 576, y + 70),
                           f"Outdent body {number} {label} follows its own section in reading order.")
            for y, label in ((330, "alpha"), (510, "beta")):
                text(page, 42, y, f"Section {number} {label}", font="hebo")
    return _save(doc, path)


def outdented_two_spans(path):
    """A span-only left sequence must not skip the intervening head/body pair (#43)."""
    doc = fitz.open()
    for number in range(4):
        page = _page(doc)
        paragraph(page, (36, 190, 576, 260),
                   f"First span {number} carries wide reference-style prose across the "
                   "body boundary and must emit exactly once before the section.")
        text(page, 42, 320, f"Between spans {number}", font="hebo")
        paragraph(page, (166, 390, 576, 460),
                   f"Between body {number} must remain between the two full-width spans.")
        paragraph(page, (36, 540, 576, 610),
                   f"Last span {number} carries wide reference-style prose across the "
                   "body boundary and must emit exactly once after the section.")
        paragraph(page, (166, 610, 576, 680),
                   f"Final body {number} supplies a second vote for the actual body edge.")
    return _save(doc, path)


def outdented_consumers(path):
    """Heads, notes, a caption, and quote isolation resolve on the folded column (#43)."""
    doc = fitz.open()
    for number in range(6):
        page = _page(doc)
        if number < 2:
            for y in (190, 450):
                paragraph(page, (36, y, 576, y + 60),
                           f"Consumer span {number} at {y} crosses the body edge and votes it.")
            for y in (290, 550):
                paragraph(page, (166, y, 576, y + 60),
                           f"Consumer span-body {number} at {y} sits in the true column.")
            continue
        text(page, 42, 180, f"Head {number} alpha", font="hebo")
        paragraph(page, (166, 210, 576, 275),
                   f"Consumer body {number} alpha follows its head in reading order.")
        image(page, (200, 300, 340, 440))
        # outdented short caption: adopts only under the shared reading column
        text(page, 42, 470, f"Figure {number}. View")
        text(page, 42, 500, f"Head {number} beta", font="hebo")
        paragraph(page, (166, 530, 576, 600),
                   f"Consumer body {number} beta remains body prose, not a quotation.")
        block(page, 200, 620,
              [f"Quoted consumer passage {number} one continues.",
               f"quoted consumer passage {number} two concludes."])
        # unique wording per page: a digit-only difference normalizes into
        # the bottom-edge boilerplate set and the note would drop as furniture
        text(page, 166, 700,
             f"Consumer footnote {number}: grant acknowledgement variant "
             f"{('alpha', 'beta', 'gamma', 'delta')[number - 2]} continues.",
             size=8)
    return _save(doc, path)


def two_col_heading_run(path):
    """A real 2-col page: the left column mixes lone heads with wrapped prose (#43)."""
    doc = fitz.open()
    for number in range(4):
        page = _page(doc)
        # below the boilerplate edge zone so lone heads can't pass as furniture
        text(page, 72, 200, f"Left head {number}", font="hebo")
        for y, label in ((230, "upper"), (360, "middle"), (490, "lower"), (620, "closing")):
            paragraph(page, (72, y, 330, y + 60),
                       f"Left prose {number} {label} wraps across several lines "
                       "inside its own genuine column band.")
        for y, label in ((230, "upper"), (490, "lower")):
            paragraph(page, (360, y, 580, y + 60),
                       f"Right prose {number} {label} keeps the second column.")
    return _save(doc, path)


def two_col_label_list(path):
    """A short-line label column under the outdent share keeps its own column (#43)."""
    doc = fitz.open()
    for number in range(4):
        page = _page(doc)
        for i, y in enumerate(range(190, 610, 60)):
            if i in (2, 5):
                # three nonempty lines: a wrapped list entry, never a strip
                # fragment, so the edge's share stays below the fold
                block(page, 72, y, [f"Wrapped label {number} {i} line one",
                                    f"wrapped label {number} {i} line two",
                                    f"wrapped label {number} {i} line three"])
            else:
                text(page, 72, y, f"Label {number} {i}")
        for y, label in ((230, "upper"), (490, "lower")):
            paragraph(page, (360, y, 580, y + 60),
                       f"List body {number} {label} supplies the real prose column.")
    return _save(doc, path)


def span_only_edge(path):
    """An edge voted only by spans has no non-spanning evidence and stays (#43)."""
    doc = fitz.open()
    for number in range(4):
        page = _page(doc)
        for y, label in ((190, "upper"), (450, "lower")):
            paragraph(page, (36, y, 576, y + 60),
                       f"Wide band {number} {label} crosses every gutter as a span.")
        for y, label in ((290, "first"), (550, "second")):
            paragraph(page, (166, y, 576, y + 60),
                       f"Span-only body {number} {label} reads after the bands.")
    return _save(doc, path)


def signature_outdent_margin(path):
    """Match the 29-page, 13-versus-17-vote interaction with a quorum of 14."""
    doc = fitz.open()
    for number in range(29):
        page = _page(doc)
        text(page, 78, 45, f"Author et al. Page {number + 1}", size=9)
        for y, label in ((190, "opening"), (390, "middle"), (570, "closing")):
            paragraph(page, (150, y, 570, y + 70),
                       f"Manuscript body {number} {label} remains in a single reading column.")
        if number < 17:
            text(page, 90, 320, f"Aim {number:02d}", font="hebo")
        if number < 13:
            text(page, 90, 510, f"Data {number:02d}", font="hebo")
    return _save(doc, path)


def heads_only_phantom(path):
    """Two outdented heads on half the pages independently reach the quorum (#43)."""
    doc = fitz.open()
    for number in range(6):
        page = _page(doc)
        for y, label in ((190, "opening"), (390, "middle"), (570, "closing")):
            paragraph(page, (150, y, 570, y + 70),
                       f"Latent body {number} {label} belongs to a single reading column.")
        if number < 3:
            text(page, 90, 320, f"Aim {number:02d}", font="hebo")
            text(page, 90, 510, f"Data {number:02d}", font="hebo")
    return _save(doc, path)


def retained_content(path):
    """Ordinary horizontal margin prose and full-width spans are not disposable labels."""
    doc = fitz.open()
    page = _page(doc)
    paragraph(page, (72, 180, 540, 250),
               "Negative opening is real body content and not a metadata field.")
    block(page, 18, 320, ["Normal margin prose", "continues as real content."])
    paragraph(page, (36, 420, 576, 490),
               "Full-width content must survive even when its left edge differs from "
               "the ordinary paragraphs. This is prose, not a figure label.")
    paragraph(page, (72, 560, 540, 630),
               "Negative closing remains in body flow after the full-width content.")
    return _save(doc, path)


GENERATORS = {
    "mixed_1col_plus_2col": mixed_1col_plus_2col,
    "magazine_wrap": magazine_wrap,
    "scan_2up": scan_2up,
    "vector_figure": vector_figure,
    "raster_figure": raster_figure,
    "outdented_heads": outdented_heads,
    "outdented_two_spans": outdented_two_spans,
    "outdented_consumers": outdented_consumers,
    "two_col_heading_run": two_col_heading_run,
    "two_col_label_list": two_col_label_list,
    "span_only_edge": span_only_edge,
    "signature_outdent_margin": signature_outdent_margin,
    "heads_only_phantom": heads_only_phantom,
    "retained_content": retained_content,
}
