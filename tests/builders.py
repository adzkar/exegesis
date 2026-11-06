"""Small deterministic PDF builders shared by corpus-independent layout tests."""
from __future__ import annotations

import fitz

W, H = 612, 792
BODY = 10


def _save(doc, path):
    """Close saved PDFs before extraction so tests exercise the on-disk input."""
    doc.save(str(path))
    doc.close()
    return path


def _page(doc, *, width=W, height=H):
    """Use a fixed letter page unless a spread explicitly needs wider geometry."""
    return doc.new_page(width=width, height=height)


def text(page, x, y, content, *, size=BODY, font="helv", rotate=0):
    """Place text at a baseline, retaining explicit rotation for direction contracts."""
    page.insert_text((x, y), content, fontsize=size, fontname=font, rotate=rotate)


def block(page, x, y, lines, *, size=BODY, font="helv", leading=None):
    """Keep adjacent lines in one text operation to reproduce fused PDF blocks."""
    if not isinstance(lines, str):
        lines = "\n".join(lines)
    page.insert_text(
        (x, y), lines, fontsize=size, fontname=font,
        lineheight=leading / size if leading is not None else 1.25,
    )


def paragraph(page, rect, content, *, size=BODY, font="helv"):
    """Reject overflowing textboxes instead of silently generating empty fixtures."""
    remaining = page.insert_textbox(
        fitz.Rect(rect), content, fontsize=size, fontname=font,
    )
    assert remaining >= 0, f"fixture text does not fit {rect}: {content!r}"


def image(page, rect):
    """Embed deterministic nontrivial pixels that clear the converter's tiny-image floor."""
    size = 96
    samples = bytes(
        component
        for y in range(size)
        for x in range(size)
        for component in ((x * y) % 256, (x * 3 + y) % 256, (x + y * 7) % 256)
    )
    pix = fitz.Pixmap(fitz.csRGB, size, size, samples, False)
    data = pix.tobytes("png")
    assert len(data) > 2048, "fixture image must survive the tiny-image filter"
    page.insert_image(fitz.Rect(rect), stream=data, keep_proportion=False)
    return fitz.Rect(rect)


def drawing(page, rect):
    """Draw axes and interior rules so labels occupy vector geometry, not a table."""
    rect = fitz.Rect(rect)
    page.draw_line(rect.bl, rect.tl)
    page.draw_line(rect.bl, rect.br)
    for fraction in (0.25, 0.5, 0.75, 1.0):
        y = rect.y1 - rect.height * fraction
        page.draw_line((rect.x0, y), (rect.x1, y), color=(0.7, 0.7, 0.7))
    page.draw_line(
        (rect.x0 + 8, rect.y1 - 8), (rect.x1 - 8, rect.y0 + 8),
        color=(0.1, 0.2, 0.8),
    )
    return rect
