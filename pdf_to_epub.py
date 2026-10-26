#!/usr/bin/env python3
"""Convert a PDF (including two-column journal papers) to a reflowable EPUB.

Text blocks are reordered with column awareness so two-column layouts read in
the correct order, embedded images are carried over, and headings detected by
font size become the EPUB table of contents.
"""

import argparse
import hashlib
import html
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import median

import fitz
from ebooklib import epub

EDGE_ZONE = 0.20
BOILERPLATE_COVERAGE = 0.4
COLUMN_GAP = 0.08
MIN_IMAGE_BYTES = 2048
MIN_HEADING_CHARS = 4
MAX_HEADING_CHARS = 120
PAGES_PER_FALLBACK_CHAPTER = 10


def _block_lines(block):
    """Return the list of line dicts for a text block (empty for images)."""
    return block.get("lines", [])


def _line_text(line):
    """Concatenate the span texts of one line."""
    return "".join(s["text"] for s in line["spans"])


def _block_text(block):
    """Join all lines of a text block, undoing soft hyphenation."""
    parts = [
        t
        for line in _block_lines(block)
        if (t := _line_text(line).strip())
    ]
    return _join_lines(parts)


def _line_max_size(line):
    """Return the largest font size among the line's spans."""
    return max((s["size"] for s in line["spans"]), default=0)


def _line_is_bold(line):
    """True when every span in the line has the bold flag set."""
    return bool(line["spans"]) and all(s["flags"] & 16 for s in line["spans"])


def _block_median_size(block):
    """Median span size of a text block."""
    sizes = [
        s["size"]
        for l in _block_lines(block)
        for s in l["spans"]
        if s["text"].strip()
    ]
    return median(sizes) if sizes else 0


def _join_lines(parts):
    """Join line fragments, merging words split by a soft hyphen."""
    out = []
    for t in parts:
        if out and re.search(r"[a-z]-$", out[-1]):
            out[-1] = out[-1][:-1] + t
        else:
            out.append(t)
    return " ".join(out)


def _body_font_size(doc):
    """Return (modal body span size, median line height) across the document."""
    sizes = Counter()
    heights = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            for line in _block_lines(block):
                heights.append(line["bbox"][3] - line["bbox"][1])
                for span in line["spans"]:
                    if span["text"].strip():
                        sizes[round(span["size"], 1)] += len(span["text"])
    return (
        sizes.most_common(1)[0][0] if sizes else 10.0,
        median(heights) if heights else 12.0,
    )


def _looks_like_heading(text):
    """True if the string is plausible heading text (no mid-sentence periods)."""
    return MIN_HEADING_CHARS <= len(text) <= MAX_HEADING_CHARS and not re.search(
        r"\.", text[:-1]
    )


def _heading_level(line, body_size):
    """Classify a line as h1/h2/None from its font size and weight."""
    text = _line_text(line).strip()
    if not _looks_like_heading(text):
        return None
    size = _line_max_size(line)
    if size >= body_size * 1.6:
        return 1
    if size >= body_size * 1.3 or (size >= body_size * 1.05 and _line_is_bold(line)):
        return 2
    return None


def _standalone_heading_level(block, body_size):
    """Classify a single-line block as a heading (h1/h2) or None.

    Section headings typically sit in their own block at a modestly larger
    size than body text, where per-line thresholds tuned for inline spans
    would miss them.
    """
    lines = _block_lines(block)
    if len(lines) != 1:
        return None
    size = _line_max_size(lines[0])
    if size >= body_size * 1.6:
        level = 1
    elif size >= body_size * 1.08:
        level = 2
    else:
        return None
    text = _line_text(lines[0]).strip()
    return level if _looks_like_heading(text) else None


def _first_line_indent(block):
    """Indent of a block's first line relative to the block's left edge."""
    lines = _block_lines(block)
    if not lines:
        return 0
    return lines[0]["bbox"][0] - block["bbox"][0]


def _sup_marker(block, body_size):
    """Footnote number if the block opens with a superscript digit, else None."""
    lines = _block_lines(block)
    if not lines:
        return None
    first = next((s for s in lines[0]["spans"] if s["text"].strip()), None)
    if first and first["size"] < body_size * 0.75:
        m = re.match(r"^(\d+)['’]?$", first["text"].strip())
        if m:
            return int(m.group(1))
    return None


def _footnote_tops(blocks, mid_x, body_size, page_height, line_h):
    """Map each column ('L'/'R') to the y where its footnote region starts.

    Two detection paths: (a) blocks opening with a superscript digit in the
    lower half of the page, and (b) unnumbered footnote zones — a smaller-than
    -body block in the bottom 40% separated from the text above by a gap much
    larger than the line height (author notes sit under a rule or whitespace).
    """
    tops = {}
    cols = {"L": [], "R": []}
    for b in blocks:
        if b["type"] != 0:
            continue
        col = "L" if (b["bbox"][0] + b["bbox"][2]) / 2 < mid_x else "R"
        cols[col].append(b)
        if b["bbox"][1] >= page_height * 0.45 and _sup_marker(b, body_size) is not None:
            tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
    for col, cblocks in cols.items():
        cblocks.sort(key=lambda b: b["bbox"][1])
        for prev, cur in zip(cblocks, cblocks[1:]):
            if (
                cur["bbox"][1] > page_height * 0.6
                and cur["bbox"][1] - prev["bbox"][3] > 2 * line_h
                and _block_median_size(cur) < body_size * 0.95
            ):
                tops[col] = min(tops.get(col, cur["bbox"][1]), cur["bbox"][1])
                break
    return tops


def _signature(text):
    """Normalize block text for boilerplate comparison (digits/case removed)."""
    return re.sub(r"[^a-z]+", "", text.lower())


def _boilerplate_signatures(doc):
    """Find normalized texts that recur near page edges (headers/footers).

    A short text block sitting in the top or bottom ``EDGE_ZONE`` of at least
    ``BOILERPLATE_COVERAGE`` of pages is treated as a running header/footer.
    """
    counts = Counter()
    for page in doc:
        height = page.rect.height
        seen = set()
        for block in page.get_text("dict")["blocks"]:
            if block["type"] != 0:
                continue
            _, y0, _, y1 = block["bbox"]
            if y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE):
                sig = _signature(_block_text(block))
                if 3 <= len(sig) <= 80:
                    seen.add(sig)
        counts.update(seen)
    threshold = max(2, int(doc.page_count * BOILERPLATE_COVERAGE))
    return {sig for sig, n in counts.items() if n >= threshold}


def _ordered_blocks(page, boilerplate):
    """Return page blocks in reading order, handling two-column layouts.

    Full-width blocks (titles, abstracts, wide figures) split the page into
    horizontal segments; within each segment the left column is read top to
    bottom, then the right column. Text blocks whose signature is in
    ``boilerplate`` (running headers/footers) are dropped.
    """
    rect = page.rect
    mid_x = rect.width / 2
    margin = rect.width * COLUMN_GAP
    items = []
    for block in page.get_text("dict")["blocks"]:
        x0, y0, x1, y1 = block["bbox"]
        if block["type"] == 0:
            text = _block_text(block)
            if not text:
                continue
            near_edge = y1 < rect.height * EDGE_ZONE or y0 > rect.height * (
                1 - EDGE_ZONE
            )
            if near_edge and _signature(text) in boilerplate:
                continue
        items.append(block)

    def is_full_width(b):
        x0, _, x1, _ = b["bbox"]
        return x0 < mid_x - margin and x1 > mid_x + margin

    def flush_segment(segment):
        left = [b for b in segment if (b["bbox"][0] + b["bbox"][2]) / 2 < mid_x]
        right = [b for b in segment if (b["bbox"][0] + b["bbox"][2]) / 2 >= mid_x]
        if len(left) >= 2 and len(right) >= 2:
            key = lambda b: (b["bbox"][1], b["bbox"][0])
            return sorted(left, key=key) + sorted(right, key=key)
        return sorted(segment, key=lambda b: (b["bbox"][1], b["bbox"][0]))

    ordered, segment = [], []
    for block in sorted(items, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        if is_full_width(block):
            ordered.extend(flush_segment(segment))
            segment = []
            ordered.append(block)
        else:
            segment.append(block)
    ordered.extend(flush_segment(segment))
    return ordered


def _line_html(line, body_size):
    """Render a line's spans to HTML, linking superscript footnote markers."""
    parts = []
    for s in line["spans"]:
        text = html.escape(s["text"])
        if s["size"] < body_size * 0.75 and s["text"].strip():
            inner = html.escape(s["text"].strip())
            if s["text"].strip().isdigit() and len(inner) <= 2:
                parts.append(f'<sup><a href="notes.xhtml#fn{inner}">{inner}</a></sup>')
            else:
                parts.append(f"<sup>{inner}</sup>")
        else:
            parts.append(text)
    return "".join(parts)


def _footnote_text(block, body_size):
    """Render a footnote block's text; returns (marker_number, html)."""
    pieces = [_line_html(l, body_size) for l in _block_lines(block)]
    return _sup_marker(block, body_size), _join_lines(
        [p for p in pieces if p.strip()]
    )


def _render_page(page, page_index, state):
    """Render one page to HTML fragments; collect headings, images, footnotes.

    ``state`` carries ``images`` (hash -> EpubItem), ``headings`` (as
    ``(level, text, anchor, page_index)`` tuples), ``footnotes`` (as
    ``(number|None, html)`` tuples), ``par`` (open paragraph line fragments
    carried across blocks and pages), ``heading_seq`` and ``boilerplate``.

    Blocks that start flush-left continue the open paragraph; blocks whose
    first line is indented start a new one. Footnote-region blocks are pulled
    out of the flow into ``state['footnotes']``.
    """
    body_size = state["body_size"]
    mid_x = page.rect.width / 2
    ordered = _ordered_blocks(page, state["boilerplate"])
    foot_tops = _footnote_tops(
        ordered, mid_x, body_size, page.rect.height, state["line_h"]
    )
    frags = []

    def flush_par():
        if state["par"]:
            frags.append(f"<p>{_join_lines(state['par'])}</p>")
            state["par"] = []

    def emit_heading(level, text):
        flush_par()
        state["heading_seq"] += 1
        anchor = f"h{state['heading_seq']}"
        state["headings"].append((level, text, anchor, page_index))
        frags.append(f'<h{level} id="{anchor}">{html.escape(text)}</h{level}>')

    for block in ordered:
        if block["type"] == 1:
            data = block.get("image")
            if data and len(data) >= MIN_IMAGE_BYTES:
                flush_par()
                digest = hashlib.sha1(data).hexdigest()
                if digest not in state["images"]:
                    ext = block.get("ext", "png")
                    name = f"images/img_{len(state['images'])}.{ext}"
                    state["images"][digest] = epub.EpubItem(
                        file_name=name,
                        media_type=f"image/{'jpeg' if ext in ('jpg', 'jpeg') else ext}",
                        content=data,
                    )
                frags.append(
                    f'<p class="fig"><img src="{state["images"][digest].file_name}" alt=""/></p>'
                )
            continue

        col = "L" if (block["bbox"][0] + block["bbox"][2]) / 2 < mid_x else "R"
        if col in foot_tops and block["bbox"][1] >= foot_tops[col] - 5:
            state["footnotes"].append(_footnote_text(block, body_size))
            continue

        level = _standalone_heading_level(block, body_size)
        if level:
            emit_heading(level, _line_text(block["lines"][0]).strip())
            continue

        med = _block_median_size(block)
        if med >= body_size * 1.15:
            flush_par()
            text = _join_lines(
                _line_html(l, body_size).strip()
                for l in _block_lines(block)
                if _line_text(l).strip()
            )
            frags.append(f'<p class="byline">{text}</p>')
            continue

        if (
            not state["abstract_done"]
            and page_index <= 1
            and 0.75 * body_size < med < 0.92 * body_size
            and block["bbox"][0] > page.rect.width * 0.15
        ):
            emit_heading(2, "Abstract")
            state["abstract_done"] = True

        new_par = _first_line_indent(block) > 4
        if not new_par and state["par"]:
            tail = re.sub(r"<[^>]+>", "", state["par"][-1]).rstrip()
            new_par = bool(re.search(r"[.!?…][\"”’)\]]*$", tail))
        if new_par:
            flush_par()
        for line in _block_lines(block):
            line_level = _heading_level(line, body_size)
            if line_level:
                emit_heading(line_level, _line_text(line).strip())
                continue
            t = _line_html(line, body_size).strip()
            if t:
                state["par"].append(t)
    return "\n".join(frags)


def convert(pdf_path, out_path, title=None, author=None, drop_edges=True):
    """Convert ``pdf_path`` to an EPUB at ``out_path``; returns a stats dict."""
    doc = fitz.open(pdf_path)
    meta = doc.metadata or {}
    title = title or meta.get("title") or pdf_path.stem
    author = author or meta.get("author") or "Unknown"

    body_size, line_h = _body_font_size(doc)
    state = {
        "body_size": body_size,
        "line_h": line_h,
        "abstract_done": False,
        "images": {},
        "headings": [],
        "footnotes": [],
        "par": [],
        "heading_seq": 0,
        "boilerplate": (
            _boilerplate_signatures(doc) if drop_edges and doc.page_count > 3 else set()
        ),
    }

    page_html = [_render_page(page, i, state) for i, page in enumerate(doc)]
    if state["par"] and page_html:
        page_html[-1] += f"\n<p>{_join_lines(state['par'])}</p>"

    fn_nums = {n for n, _ in state["footnotes"] if n is not None}
    page_html = [
        re.sub(
            r'<sup><a href="notes.xhtml#fn(\d+)">(\d+)</a></sup>',
            lambda m: m.group(0)
            if int(m.group(1)) in fn_nums
            else f"<sup>{m.group(2)}</sup>",
            frag,
        )
        for frag in page_html
    ]

    boundaries = [
        i
        for i, frag in enumerate(page_html)
        if '<h1 id="' in frag or '<h2 id="' in frag
    ]
    if not boundaries and len(page_html) > PAGES_PER_FALLBACK_CHAPTER:
        boundaries = list(range(0, len(page_html), PAGES_PER_FALLBACK_CHAPTER))
    if not boundaries or boundaries[0] != 0:
        boundaries.insert(0, 0)

    book = epub.EpubBook()
    book.set_identifier(hashlib.sha1(str(pdf_path).encode()).hexdigest()[:16])
    book.set_title(title)
    book.set_language("en")
    book.add_author(author)

    chapters = []
    for idx, start in enumerate(boundaries):
        end = boundaries[idx + 1] if idx + 1 < len(boundaries) else len(page_html)
        content = "\n".join(page_html[start:end])
        heading = next(
            (t for lv, t, a, p in state["headings"] if lv in (1, 2) and start <= p < end),
            None,
        )
        chap = epub.EpubHtml(
            title=heading or f"Section {idx + 1}",
            file_name=f"chap_{idx + 1}.xhtml",
            lang="en",
        )
        chap.content = (
            f"<html><body>{content.strip() or '<p></p>'}</body></html>"
        )
        book.add_item(chap)
        chapters.append((chap, start))

    note_entries = []
    seen_fn = set()
    for num, frag in state["footnotes"]:
        frag = re.sub(
            r"^<sup><a[^>]*>(\d+)</a></sup>", r"<sup>\1</sup>", frag
        )
        if num is None and note_entries:
            note_entries[-1] = note_entries[-1][:-4] + " " + frag + "</p>"
        elif num is not None and num not in seen_fn:
            seen_fn.add(num)
            note_entries.append(f'<p class="fn" id="fn{num}">{frag}</p>')
        else:
            note_entries.append(f'<p class="fn">{frag}</p>')

    book.toc = []
    for level, text, anchor, page_i in state["headings"]:
        chap_idx = max(j for j, (_, s) in enumerate(chapters) if s <= page_i)
        book.toc.append(
            epub.Link(f"{chapters[chap_idx][0].file_name}#{anchor}", text, anchor)
        )
    if note_entries:
        notes = epub.EpubHtml(title="Notes", file_name="notes.xhtml", lang="en")
        notes.content = (
            "<html><body><h1>Notes</h1>" + "".join(note_entries) + "</body></html>"
        )
        book.add_item(notes)
        chapters.append((notes, len(page_html)))
        book.toc.append(epub.Link("notes.xhtml", "Notes", "notes"))
    for item in state["images"].values():
        book.add_item(item)
    book.spine = ["nav"] + [c for c, _ in chapters]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    epub.write_epub(str(out_path), book)
    return {
        "title": title,
        "pages": doc.page_count,
        "chapters": len(chapters),
        "headings": len(state["headings"]),
        "footnotes": len(state["footnotes"]),
        "images": len(state["images"]),
        "output": str(out_path),
    }


def main(argv=None):
    """Parse CLI arguments and run the conversion."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="PDF file to convert")
    parser.add_argument("-o", "--output", type=Path, help="output .epub path")
    parser.add_argument("--title", help="override EPUB title")
    parser.add_argument("--author", help="override EPUB author")
    parser.add_argument(
        "--keep-headers",
        action="store_true",
        help="keep header/footer strips (page numbers, running heads)",
    )
    args = parser.parse_args(argv)

    if not args.input.is_file():
        parser.error(f"not a file: {args.input}")
    out = args.output or args.input.with_suffix(".epub")

    stats = convert(
        args.input, out, title=args.title, author=args.author,
        drop_edges=not args.keep_headers,
    )
    print(
        f"{stats['output']}: {stats['pages']} pages -> {stats['chapters']} "
        f"chapter(s), {stats['headings']} heading(s), "
        f"{stats['footnotes']} footnote(s), {stats['images']} image(s)"
    )


if __name__ == "__main__":
    sys.exit(main())
