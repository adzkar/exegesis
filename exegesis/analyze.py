"""Document- and page-level analysis: body font metrics, boilerplate
signatures, column geometry, rotated- and horizontal-table regions,
footnote zones, and the /Rotate clip transform. Operates on the page
dicts extracted once in ``emit.convert``."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Container
from dataclasses import dataclass, field
from statistics import median

import fitz

from .const import (
    BOILERPLATE_COVERAGE,
    BOILERPLATE_MAX_CHARS,
    BOILERPLATE_MAX_LINES,
    DRAWING_PAD,
    EDGE_ZONE,
    FIGURE_AREA_MERGE,
    FIGURE_CLIP_PAD,
    FIGURE_LABEL_CHARS,
    FIGURE_LABEL_PAD,
    FIGURE_MIN_LABELS,
    INFO_ADJ_GAP,
    INFO_ATTACH_GAP,
    INFO_ATTACH_MAX,
    INFO_CONT_X_TOL,
    INFO_MEMBER_MAX_LINES,
    INFO_RUN_MAX_LINES,
    MARGIN_BAND_TOL,
    MARGIN_EDGE_MAX_FILL,
    MARGIN_EDGE_SHARE,
    MARGIN_RIGHT_START,
    MAX_HEADING_CHARS,
    MIN_IMAGE_BYTES,
    OUTDENT_BAND_FILL,
    OUTDENT_HEADING_SHARE,
    OUTDENT_MIN_BLOCKS,
    PAGE_FRAME_RATIO,
    SIGNATURE_PREFIX_MAX_LINES,
    ZONE_COL_MIN_BLOCKS,
    ZONE_COL_MIN_COVER,
    ZONE_COL_MIN_SPREAD,
    ZONE_COL_MIN_WIDTH,
    ZONE_EDGE_TOL,
    ZONE_GUTTER_GRAZE,
    ZONE_GUTTER_TOL,
)
from .lang import EN, LangProfile
from .text import (
    _block_lines,
    _block_median_size,
    _block_text,
    _join_lines,
    _line_text,
    _span_is_bold,
)


def _body_font_size(pages):
    """Return (modal body span size, median line height) across the document.

    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs so
    the extraction cost is paid once in ``convert``.
    """
    sizes = Counter()
    heights = []
    for _page, pdict in pages:
        for block in pdict["blocks"]:
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


def _signature(text):
    """Normalize block text for boilerplate comparison (digits/case removed)."""
    return re.sub(r"[^a-z]+", "", text.lower())


def _boilerplate_signatures(pages):
    """Find normalized texts that recur near page edges (headers/footers).

    A short text block sitting in the top or bottom ``EDGE_ZONE`` of at least
    ``BOILERPLATE_COVERAGE`` of pages is treated as a running header/footer.
    The block must stay inside ``BOILERPLATE_MAX_LINES`` lines and
    ``BOILERPLATE_MAX_CHARS`` normalized letters — bounds sized for real
    running heads (a five-line license head runs ~265 letters), so a
    page-spanning repeated block like a continued-table header can't
    excuse itself. ``pages`` is a list of ``(fitz.Page,
    page.get_text("dict"))`` pairs.
    """
    counts = Counter()
    for page, pdict in pages:
        height = page.rect.height
        seen = set()
        for block in pdict["blocks"]:
            if block["type"] != 0:
                continue
            _, y0, _, y1 = block["bbox"]
            if y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE):
                sig = _signature(_block_text(block))
                if (
                    3 <= len(sig) <= BOILERPLATE_MAX_CHARS
                    and len(_block_lines(block)) <= BOILERPLATE_MAX_LINES
                ):
                    seen.add(sig)
        counts.update(seen)
    threshold = max(2, int(len(pages) * BOILERPLATE_COVERAGE))
    return {sig for sig, n in counts.items() if n >= threshold}


def _column_edges(items):
    """Detect column left edges by clustering block x0 positions.

    Clusters of ≥2 blocks within 15pt count as a column; stray x0s (page
    numbers, margin notes) don't form edges. Returns 1+ edges left to right.
    """
    xs = sorted(b["bbox"][0] for b in items if b["type"] == 0)
    clusters = []
    for x in xs:
        if clusters and x - clusters[-1][-1] < 15:
            clusters[-1].append(x)
        else:
            clusters.append([x])
    return [median(c) for c in clusters if len(c) >= 2] or [0.0]


def _doc_column_edges(
    pages,
    boilerplate: Container[str] = frozenset(),
    lang: LangProfile = EN,
):
    """Column edges for the document: edges found on ≥half of all pages.

    Per-page clustering produces phantom edges from bylines, headings, and
    other spanning elements; real columns repeat on most pages, so only
    edges that recur survive. Prevents mid-page splits that corrupt both
    ordering and footnote-gap detection.
    ``pages`` is a list of ``(fitz.Page, page.get_text("dict"))`` pairs.
    """
    votes = []
    for page, pdict in pages:
        # furniture shapes can't form column edges — a sidebar or running
        # head recurring at the same x on every page is the classic
        # phantom-edge generator. Aside members cast no votes either:
        # a claimed info-box column must not vote a phantom edge
        items = [
            b
            for b in pdict["blocks"]
            if b["type"] == 0
            and b.get("aside") is None
            and not _is_furniture(
                b, page, _block_text(b, lang), lang
            )
            and not _is_signature_furniture(
                b, page.rect.height, boilerplate
            )
        ]
        for e in _column_edges(items):
            for v in votes:
                if abs(v[0] - e) < 15:
                    v[1] += 1
                    break
            else:
                votes.append([e, 1])
    votes.sort(key=lambda v: -v[1])
    needed = max(1, len(pages) // 2)
    edges = sorted(v[0] for v in votes if v[1] >= needed)
    return edges or [0.0]


def _info_lead_re(lang: LangProfile):
    """Compile the info-box label opener over the profile's vocabulary —
    ``None`` when a hand-built profile never declared one (the detector
    degrades rather than borrow English labels)."""
    if not lang.info_labels:
        return None
    return re.compile(
        rf"^\s*[*†‡§]?\s*({lang.info_labels})\b", re.IGNORECASE
    )


def _field_label(text, lead_re):
    """The normalized label a line opens with, or None.

    Two label forms qualify: ``Label:`` — any casing, multi-word
    labels and ``*``-style markers included — and an all-caps label
    with no colon (``EDITED BY``, ``RECEIVED  #35 April 2024``). A
    mixed-case word without the colon is a sentence opener, not a
    field; a mid-sentence ``Received:`` never reaches the anchor.
    """
    m = lead_re.match(text)
    if not m:
        return None
    label = re.sub(r"\s+", " ", m.group(1)).strip()
    if text[m.end():].lstrip().startswith(":") or label.isupper():
        return label.lower()
    return None


def _field_runs(texts, first_of, h, boilerplate, line_h):
    """Label-seeded field runs with aligned wrap continuations.

    A run opens on a block whose first line is a field; following
    unlabeled blocks join only with aligned text-run evidence — the
    same left edge within ``INFO_CONT_X_TOL`` and a gap under
    ``INFO_ADJ_GAP`` line heights. A wider wrap still joins (an
    MDPI citation's continuation is wider than its first line) while
    a paragraph in the body column — a different left edge — cannot.
    The run caps at ``INFO_RUN_MAX_LINES`` so a wrapped citation can
    extend without enabling growth into a following body paragraph;
    a new field seed or a signature-furniture block ends it.
    ``texts`` is the page's text blocks in (y, x) order.
    """
    runs = []
    taken = set()
    for i, b in enumerate(texts):
        if id(b) in taken or not first_of.get(id(b)):
            continue
        run = [b]
        nlines = len(_block_lines(b))
        rx0 = b["bbox"][0]
        for cand in texts[i + 1:]:
            if first_of.get(id(cand)):
                break
            gap = cand["bbox"][1] - run[-1]["bbox"][3]
            if (
                not (
                    -line_h <= gap <= INFO_ADJ_GAP * line_h
                    and abs(cand["bbox"][0] - rx0) <= INFO_CONT_X_TOL
                    and nlines + len(_block_lines(cand))
                    <= INFO_RUN_MAX_LINES
                )
                or cand.get("aside") is not None
                or _is_signature_furniture(cand, h, boilerplate)
            ):
                break
            run.append(cand)
            nlines += len(_block_lines(cand))
        taken.update(id(c) for c in run)
        runs.append(run)
    return runs


def _info_boxes(runs, labels_of, texts, line_h):
    """Chain adjacent field runs into boxes of ≥2 distinct labels.

    Runs chain while the vertical gap stays under ``INFO_ADJ_GAP``
    line heights and their x-ranges overlap — a wider separation is a
    section head or a prose column, not the same box. Ordinary body
    text is a barrier: any other text block intersecting the open gap
    inside the pair's x-union splits the chain — an info box's fields
    can't wrap a prose paragraph, and unclaimed signature furniture
    stops growth the same way. Distinctness counts every label line
    inside the runs: the same label twice is not a box, and a single
    labeled field never is.
    """
    boxes = []
    chain, chain_labels = [], set()
    for run in runs:
        if chain:
            prev = chain[-1]
            prev_x0 = min(b["bbox"][0] for b in prev)
            prev_x1 = max(b["bbox"][2] for b in prev)
            prev_y1 = max(b["bbox"][3] for b in prev)
            run_x0 = min(b["bbox"][0] for b in run)
            run_x1 = max(b["bbox"][2] for b in run)
            run_y0 = run[0]["bbox"][1]
            member = {id(c) for r in chain for c in r} | {
                id(c) for c in run
            }
            adjacent = (
                run_y0 - prev_y1 <= INFO_ADJ_GAP * line_h
                and min(prev_x1, run_x1) - max(prev_x0, run_x0) > 0
                and not any(
                    b["type"] == 0
                    and id(b) not in member
                    and b["bbox"][1] < run_y0
                    and b["bbox"][3] > prev_y1
                    and b["bbox"][2] > min(prev_x0, run_x0)
                    and b["bbox"][0] < max(prev_x1, run_x1)
                    for b in texts
                )
            )
            if not adjacent:
                if len(chain_labels) >= 2:
                    boxes.append(chain)
                chain, chain_labels = [], set()
        chain.append(run)
        chain_labels |= {
            lbl for c in run for lbl in labels_of.get(id(c), set())
        }
    if len(chain_labels) >= 2:
        boxes.append(chain)
    return boxes


def _detect_asides(pages, line_h, boilerplate, lang: LangProfile = EN):
    """Mark front-matter info-box members with a ``block["aside"]`` key.

    Runs pre-grid in ``convert()`` — right after the signature-prefix
    split, before ``_doc_column_edges`` — so a claimed field column can
    never vote a phantom document edge. Only pages 0–1 are scanned
    (info boxes are front matter) and only grid-independent evidence:
    labels open fields, aligned wraps extend them, adjacent
    distinct-label runs form a box, and the frozen core admits only
    inside-evidence members. ``pages`` is ``convert``'s extracted
    ``(page, pdict)`` list; each validated member gets a shared
    region key the render's aside path lifts wholesale.
    """
    lead_re = _info_lead_re(lang)
    if lead_re is None:
        return
    region = 0
    for page, pdict in pages[:2]:
        h = page.rect.height
        texts = sorted(
            (b for b in pdict["blocks"] if b["type"] == 0),
            key=lambda b: (b["bbox"][1], b["bbox"][0]),
        )
        labels_of, first_of = {}, {}
        for b in texts:
            if b.get("aside") is not None:
                continue
            ll = [
                _field_label(_line_text(l), lead_re)
                for l in _block_lines(b)
            ]
            labels_of[id(b)] = {x for x in ll if x}
            first_of[id(b)] = ll[0] if ll else None
        runs = _field_runs(texts, first_of, h, boilerplate, line_h)
        for box in _info_boxes(runs, labels_of, texts, line_h):
            members = [c for run in box for c in run]
            member_ids = {id(c) for c in members}
            cx0 = min(c["bbox"][0] for c in members)
            cy0 = min(c["bbox"][1] for c in members)
            cx1 = max(c["bbox"][2] for c in members)
            cy1 = max(c["bbox"][3] for c in members)
            # the core is frozen now — added members never expand it.
            # Inside it admits only media or text with independent box
            # evidence (short non-sentence values like an email line);
            # a straddler stays whole in body flow. Outside, only a
            # bounded media-anchored group within a hop of the field
            # edge may attach — a nearby text-only paragraph never can
            attached = []
            for b in pdict["blocks"]:
                if id(b) in member_ids or b.get("aside") is not None:
                    continue
                x0, y0, x1, y1 = b["bbox"]
                if (
                    x0 < cx0 - 4 or x1 > cx1 + 4
                    or y0 < cy0 - 4 or y1 > cy1 + 4
                ):
                    continue
                if b["type"] == 1 or (
                    len(_block_lines(b)) <= INFO_MEMBER_MAX_LINES
                    and not _is_signature_furniture(b, h, boilerplate)
                    and not re.search(
                        r"[.!?…][\"”’)\]]*$",
                        _block_text(b, lang).rstrip(),
                    )
                ):
                    attached.append(b)
            attached_ids = {id(b) for b in attached}
            media = [
                b
                for b in pdict["blocks"]
                if b["type"] == 1
                and id(b) not in member_ids | attached_ids
                and b.get("aside") is None
                and b["bbox"][2] > cx0
                and b["bbox"][0] < cx1
                and (
                    0 <= b["bbox"][1] - cy1 <= INFO_ADJ_GAP * line_h
                    or 0 <= cy0 - b["bbox"][3] <= INFO_ADJ_GAP * line_h
                )
            ]
            # inside admits can already exceed the cap on a dense
            # core — the remaining budget clamps at zero; a negative
            # slice bound would invert it and attach all but the tail
            for b in media[: max(0, INFO_ATTACH_MAX - len(attached))]:
                attached.append(b)
                attached_ids.add(id(b))
            for b in texts:
                if len(attached) >= INFO_ATTACH_MAX:
                    break
                if (
                    id(b) in member_ids | attached_ids
                    or b.get("aside") is not None
                    or _is_signature_furniture(b, h, boilerplate)
                    or not (
                        b["bbox"][2] > cx0 and b["bbox"][0] < cx1
                    )
                    or not any(
                        b["bbox"][1] <= m["bbox"][3]
                        and b["bbox"][3] >= m["bbox"][1]
                        or 0
                        <= b["bbox"][1] - m["bbox"][3]
                        <= INFO_ATTACH_GAP * line_h
                        or 0
                        <= m["bbox"][1] - b["bbox"][3]
                        <= INFO_ATTACH_GAP * line_h
                        for m in media
                    )
                ):
                    continue
                attached.append(b)
                attached_ids.add(id(b))
            for b in members + attached:
                b["aside"] = region
            region += 1


# a margin fragment reads as a sidebar label when it opens
# ``Label:`` — an uppercase-led head (markers allowed) ending in a
# colon inside 60 chars; URLs can't match ('https' opens lowercase,
# and '/' is refused before the colon anyway)
_MARGIN_LABEL_RE = re.compile(r"^\s*[*†‡§]?\s*[A-ZÀ-Þ][^\n:/]{1,60}:")
# manuscript line numbering — digits and punctuation, never words —
# is margin furniture, not a sidebar: it can never be sidebar evidence
_MARGIN_NUMBERS_RE = re.compile(r"^[\s\d.,;:*()±\-–—/°%]+$")
# a label stem plus a serial number ('Aim 01', 'Data 12', '3') is
# numbering, not a sidebar item — the strip veto's block-level arm
_MARGIN_SERIAL_RE = re.compile(
    r"^\s*[A-Za-zÀ-Þ]{0,10}\s*[-–—]?\s*\d{1,4}[a-zA-Z]?\s*[.,;:!?]?\s*$"
)


def _margin_sig(text):
    """Repeat-detection skeleton for margin fragments: digits collapse
    so a DOI family's numbering (``…journal.pone.NNNNN.tNNN``) or a
    ``Notes.`` stub recurring on later pages shares one signature."""
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _margin_recurring(text, sig_pages, group_sigs):
    """A fragment recurs *as sidebar evidence* only when its skeleton is
    punctuation-structured — URLs, ``Notes.``, DOI stems, ``NEGPOS > NEU``
    comparisons. Word-only heading stems (``Section N alpha``) normalize
    to the same skeleton too but are outdent material, not marginalia."""
    sig = _margin_sig(text)
    if not re.search(r"[^\w\s#]", sig):
        return False
    return len(sig_pages.get(sig, ())) >= 2 or group_sigs[sig] >= 2


def _margin_block_evidence(b, text, recurring, lead_re, multi, lang):
    """Sidebar evidence one text block carries: a table-legend or
    ``Label:`` opener, an ``info_labels`` field (grouped only — a lone
    field is a front-matter box fragment, #41's own contract), or a
    recurring fragment. Serial-numbered and digits-only fragments —
    manuscript line numbering — never carry evidence."""
    if (
        _MARGIN_NUMBERS_RE.fullmatch(text.strip())
        or _MARGIN_SERIAL_RE.fullmatch(text)
    ):
        return False
    lines = _block_lines(b)
    if lead_re is not None and lines and _field_label(
        _line_text(lines[0]), lead_re
    ):
        # field labels are #41's box vocabulary — evidence only when
        # they arrive grouped; a lone ``Received:`` stays body flow
        return multi
    if lang.table_caption.match(text) or _MARGIN_LABEL_RE.match(text):
        return True
    return recurring


def _margin_group_evidence(members, sig_pages, lead_re, lang):
    """Sidebar evidence across the group: any text member carrying a
    table-legend or ``Label:`` opener, an ``info_labels`` field, or a
    fragment that recurs — across doc pages, or inside the group itself
    (two sibling DOI links). Margin images ride an evidenced group but
    never justify one — a figure fragment spilling past the band is not
    marginalia on its own."""
    texts = {
        id(b): _block_text(b, lang) for b in members if b["type"] == 0
    }
    group_sigs = Counter(_margin_sig(t) for t in texts.values())
    multi = len(members) >= 2
    for b in members:
        if b["type"] != 0:
            continue
        text = texts[id(b)]
        recurring = _margin_recurring(text, sig_pages, group_sigs)
        if _margin_block_evidence(
            b, text, recurring, lead_re, multi, lang
        ):
            return True
    return False


def _margin_bands(page, pdict, edges, boilerplate, lang: LangProfile = EN):
    """The page's occupied body bands: ``[edge, right)`` per column,
    where ``right`` is the next edge or — for the last column and every
    gutter — the measured occupied extent. Occupancy counts text blocks
    starting inside the band's own left stretch (``MARGIN_RIGHT_START``
    of its nominal width), so a stray margin block can't stretch the
    band that would exclude it — and a right-margin strip starts too far
    out to measure the column's reach. Widths compare in the bboxes' own
    unrotated space (``page.mediabox``, the ``_is_margin_strip`` rule)
    — displayed ``page.rect`` swaps axes on /Rotate pages."""
    bands = []
    for i, e in enumerate(edges):
        nominal = (
            edges[i + 1] if i + 1 < len(edges) else page.mediabox.width
        )
        cap = e + MARGIN_RIGHT_START * (nominal - e)
        reach = [
            b["bbox"][2]
            for b in pdict["blocks"]
            if b["type"] == 0
            and e - 15 <= b["bbox"][0] <= cap
            and b.get("aside") is None
            and not _is_signature_furniture(
                b, page.rect.height, boilerplate
            )
            and not _is_furniture(b, page, _block_text(b, lang), lang)
        ]
        hi = min(nominal, max(reach)) if reach else nominal
        bands.append((e, hi))
    return bands


def _margin_candidates(page, pdict, bands, boilerplate,
                       lang: LangProfile = EN):
    """Blocks fully outside every body band — only candidates; position
    is never sufficient by itself. Already-claimed asides (#41),
    furniture-shaped blocks (running heads, page numbers, rotated
    margin strips), ``Figure N`` legends (a figure's caption stays with
    the figure machinery), and all-rotated label bands stay out — the
    rotated ones are figure-region material (#35), not marginalia."""
    upright = _upright_dir(page)
    cands = []
    for b in pdict["blocks"]:
        if b["type"] not in (0, 1) or b.get("aside") is not None:
            continue
        x0, _y0, x1, _y1 = b["bbox"]
        if any(
            x1 > lo - MARGIN_BAND_TOL and x0 < hi + MARGIN_BAND_TOL
            for lo, hi in bands
        ):
            continue
        if b["type"] == 1:
            if len(b.get("image") or b"") >= MIN_IMAGE_BYTES:
                cands.append(b)
            continue
        text = _block_text(b, lang)
        if (
            not text
            or lang.figure_caption.match(text)
            or _is_furniture(b, page, text, lang)
            or _is_signature_furniture(b, page.rect.height, boilerplate)
        ):
            continue
        seen = [
            d for d in (_line_dir(l) for l in _block_lines(b))
            if d is not None
        ]
        if seen and all(d != upright for d in seen):
            continue
        cands.append(b)
    return cands


def _margin_zone(b, bands):
    """Which margin the candidate sits in: left of the first band, right
    of the last occupied edge, or inside an inter-column gutter."""
    x0, _, x1, _ = b["bbox"]
    if x1 <= bands[0][0] - MARGIN_BAND_TOL:
        return "L"
    if x0 >= bands[-1][1] + MARGIN_BAND_TOL:
        return "R"
    for j in range(len(bands) - 1):
        if (
            x0 >= bands[j][1] + MARGIN_BAND_TOL
            and x1 <= bands[j + 1][0] - MARGIN_BAND_TOL
        ):
            return ("G", j)
    return None


def _margin_groups(ms, lang: LangProfile = EN):
    """Split one page-strip into groups at table-legend openers: each
    ``Table N`` block seeds a group absorbing the strip members below
    it; members ahead of the first opener keep their own group."""
    groups, cur = [], []
    for b in ms:
        if (
            b["type"] == 0
            and lang.table_caption.match(_block_text(b, lang))
            and cur
        ):
            groups.append(cur)
            cur = []
        cur.append(b)
    if cur:
        groups.append(cur)
    return groups


def _margin_voted_edge(pages, edges, boilerplate, lead_re,
                       lang: LangProfile = EN):
    """Mark members of an outermost *voted* margin column, if one exists.

    A margin strip recurring densely enough can reach the column-edge
    quorum — an edge is that column only when outermost, narrow against
    the widest band, and ≥``MARGIN_EDGE_SHARE`` of its non-spanning,
    non-furniture members carry sidebar evidence. Evidence counts on
    pages ≥1 like the outdent read (page 0's front matter can't be told
    from outdented heads), then every page's members mark — a margin
    column's page-0 content is marginalia too. Returns True when any
    edge classified — the caller re-votes the grid once so the lifted
    column surrenders its edge.
    """
    if len(edges) < 2:
        return False
    widths = [edges[i + 1] - edges[i] for i in range(len(edges) - 1)]
    widths.append(median(p.mediabox.width for p, _d in pages) - edges[-1])
    widest = max(widths) or 1.0
    marked = False
    for i in (0, len(edges) - 1):
        if widths[i] >= MARGIN_EDGE_MAX_FILL * widest:
            continue
        members = []
        for pi, (page, pdict) in enumerate(pages):
            for b in pdict["blocks"]:
                if (
                    b["type"] != 0
                    or b.get("aside") is not None
                    or _is_spanning(b, edges)
                    or _assign_column(b, edges) != i
                ):
                    continue
                text = _block_text(b, lang)
                if (
                    not text
                    or _is_furniture(b, page, text, lang)
                    or _is_signature_furniture(
                        b, page.rect.height, boilerplate
                    )
                ):
                    continue
                members.append((pi, b, text))
        evidence = [(pi, b, t) for pi, b, t in members if pi]
        if len(evidence) < 2:
            continue
        sigs: dict[str, set[int]] = {}
        for pi, _b, t in evidence:
            sigs.setdefault(_margin_sig(t), set()).add(pi)
        hit = sum(
            _margin_block_evidence(
                b,
                t,
                len(sigs[_margin_sig(t)]) >= 2
                and re.search(r"[^\w\s#]", _margin_sig(t)) is not None,
                lead_re,
                True,
                lang,
            )
            for _pi, b, t in evidence
        )
        if hit < MARGIN_EDGE_SHARE * len(evidence):
            continue
        for pi, b, _t in members:
            b["aside"] = ("margin-edge", pi, i)
        marked = True
    return marked


def _detect_margins(pages, edges, boilerplate, lang: LangProfile = EN,
                    page_edges=None):
    """Lift marginalia off the body grid into ``block["aside"]`` regions.

    Each page's body bands derive from the grid's occupied x-ranges
    and gutters (``_margin_bands``); text or media wholly outside those
    bands is only a candidate — a margin strip earns its aside with
    spatial coherence (a shared x-strip on the page, split at
    table-legend openers into legend-bound groups) plus sidebar evidence
    (``_margin_group_evidence``: table caption, ``Label:``, grouped
    ``info_labels`` field, or a punctuation-structured fragment
    recurring across pages — margin images ride an evidenced group but
    never justify one), while serial-numbered strips —
    manuscript line numbering — carry no evidence at all. Blocks
    already claimed by ``_detect_asides`` stay claimed — one aside
    region per block — and ``Figure N`` legends never become
    candidates, since a figure's caption belongs to the figure
    machinery. ``_margin_voted_edge`` handles the conditional re-vote
    arm; the caller re-votes the grid once when it fires. Region keys
    are ``("margin", page, group)`` / ``("margin-edge", page, edge)``;
    emission rides the shared ``<aside>`` path.

    With ``page_edges`` (#37's per-page grids) the bands read each
    page's own active grid instead of the doc grid — a margin strip
    that was in-band under the committed grid can surface as a
    candidate once the page's true columns bound the bands. The arm
    only ever adds marks (already-claimed blocks stay claimed), the
    group counter continues from the earlier pass's same-page marks
    so a new region never collides with an existing ``("margin", pi,
    gi)`` key, and the doc-level ``_margin_voted_edge`` re-vote is
    reserved for the first pass, which runs before page grids exist.
    """
    lead_re = _info_lead_re(lang)
    voted = (
        page_edges is None
        and _margin_voted_edge(pages, edges, boilerplate, lead_re, lang)
    )
    sig_pages: dict[str, set[int]] = {}
    per_page = []
    for pi, (page, pdict) in enumerate(pages):
        bands = _margin_bands(
            page,
            pdict,
            edges if page_edges is None else page_edges[pi],
            boilerplate,
            lang,
        )
        cands = _margin_candidates(page, pdict, bands, boilerplate, lang)
        per_page.append((bands, cands))
        for b in cands:
            if b["type"] == 0:
                sig_pages.setdefault(
                    _margin_sig(_block_text(b, lang)), set()
                ).add(pi)
    for pi, (bands, cands) in enumerate(per_page):
        zones = {}
        for b in cands:
            zones.setdefault(_margin_zone(b, bands), []).append(b)
        # a second pass ("page_edges" given) can lift groups the first
        # missed under the doc bands — continue the page's numbering
        # rather than re-issuing ("margin", pi, 0), which would merge
        # the new region with the earlier one in ``_aside_groups``
        gi = 0
        for b in pages[pi][1]["blocks"]:
            a = b.get("aside")
            if (
                isinstance(a, tuple)
                and len(a) == 3
                and a[:2] == ("margin", pi)
            ):
                gi = max(gi, a[2] + 1)
        for zone in sorted(zones, key=str):
            ms = sorted(
                zones[zone], key=lambda b: (b["bbox"][1], b["bbox"][0])
            )
            for group in _margin_groups(ms, lang):
                if not _margin_group_evidence(
                    group, sig_pages, lead_re, lang
                ):
                    continue
                for b in group:
                    b["aside"] = ("margin", pi, gi)
                gi += 1
    return voted


def _assign_column(block, edges):
    """Index of the column edge nearest a block's left position."""
    x0 = block["bbox"][0]
    return min(range(len(edges)), key=lambda i: abs(x0 - edges[i]))


def _is_spanning(block, edges):
    """True if a block crosses a gutter between detected columns."""
    x0, _, x1, _ = block["bbox"]
    return any(x0 < e - 12 < x1 for e in edges[1:])


def _covered_cols(block, edges, page_width):
    """Column indices a block overlaps, based on column edge bands."""
    x0, _, x1, _ = block["bbox"]
    bounds = list(edges) + [page_width]
    return [
        i
        for i in range(len(edges))
        if x0 < bounds[i + 1] and x1 > bounds[i]
    ]


def _overlap_cols(bbox, edges, page_width, tol=12):
    """Columns a rect genuinely occupies: its home column (the edge nearest
    x0) plus any column overlapped by at least ``tol`` points.

    ``_covered_cols`` counts any pixel of overlap, so a block bleeding a
    point across the modal gutter claims both columns — fine for ordering
    but too eager for identity tests like caption attribution, where a
    jittery OCR-layer bbox must not borrow the adjacent column.
    """
    x0, _, x1, _ = bbox
    bounds = list(edges) + [page_width]
    cols = {_assign_column({"bbox": bbox}, edges)}
    for i in range(len(edges)):
        if min(x1, bounds[i + 1]) - max(x0, bounds[i]) >= tol:
            cols.add(i)
    return cols


def _heading_like(block, text):
    """Heading-shaped: one nonempty line of ≤MAX_HEADING_CHARS chars that
    does not end in sentence punctuation.

    The layout survey's per-edge heading-like probe — a single short
    line without a terminal ``.!?…`` is what a section head looks like,
    while body prose either wraps to several lines or terminates its
    sentence. ``_outdent_like`` relaxes it to two lines plus a band-fill
    bound for the actual fold decision. Closing quotes/brackets after
    the punctuation don't disqualify it.
    """
    nonempty = sum(bool(_line_text(line).strip()) for line in _block_lines(block))
    return (
        nonempty == 1
        and 0 < len(text) <= MAX_HEADING_CHARS
        and not text.rstrip("\"'”’»)]} \t\r\n").endswith((".", "!", "?", "…"))
    )


def _outdent_like(block, text, band_end):
    """Strip-shaped: a short unpunctuated fragment too narrow for its band.

    The evidence shape behind `_reading_columns`'s outdent share — what
    the layout survey's per-edge report must match. A strip block is
    ``_heading_like``-adjacent but weaker about lines: two nonempty
    lines still count, because a head squeezed into a narrow outdent
    gutter wraps (MDPI's 'Emotion regulation' emits as two bold lines).
    The price of admitting wrapped fragments is the ``band_end`` clause:
    the block's x-extent must stop well short of the band the edge
    shares with its right column. Wrapped column text — including PDFs
    that emit one block per line, like the Wiley corpus file — reaches
    the band's far edge; an outdented fragment never does.
    """
    nonempty = sum(bool(_line_text(line).strip()) for line in _block_lines(block))
    return (
        0 < nonempty <= 2
        and 0 < len(text) <= MAX_HEADING_CHARS
        and not text.rstrip("\"'”’»)]} \t\r\n").endswith((".", "!", "?", "…"))
        and block["bbox"][2] <= band_end
    )


def _reading_columns(
    pages,
    edges,
    boilerplate: Container[str] = frozenset(),
    lang: LangProfile = EN,
    pi_list=None,
):
    """Edge index → reading-column index, collapsing outdent edges.

    Some templates outdent section heads to the left of the body edge;
    when the outdent x recurs on enough pages to reach the column
    quorum, it yields a real *physical* edge — full-width elements
    genuinely start there — that must not be treated as a reading
    column: column-major order would emit its handful of heads ahead of
    the whole body instead of before their own sections (#43's MDPI
    case). An edge is an outdent edge when, across the document, almost
    all (≥``OUTDENT_HEADING_SHARE``) of the non-spanning blocks assigned
    to it are ``_outdent_like`` — short fragments that stay narrow in
    the band the edge shares with its right column, so PDFs that emit
    wrapped column lines as lone blocks can't masquerade as a strip.
    Spans never count either way — they cover the edge legitimately —
    and an edge with less than ``OUTDENT_MIN_BLOCKS`` of non-spanning
    evidence, or with no reading column to its right, is not an outdent
    edge.

    Evidence comes only from pages ≥1: page 0's front matter (title,
    bylines, disclaimers) clusters at wide left edges and cannot be told
    from outdented heads until #41's aside path exists to claim it.
    ``pi_list``, when given, restricts evidence to those original page
    indices — #37's zone pass calls this once per active grid, so a
    zone's outdent read pools only its own pages' evidence (the caller
    may include page 0; it is still excluded here). Eligibility mirrors
    ``_doc_column_edges`` — furniture-shaped blocks contribute no
    evidence, just as they cast no votes.

    Non-outdent edges keep their own reading column, renumbered left to
    right; an outdent edge maps to the first non-outdent edge on its
    right — transitively, when several outdent edges chain (the last
    edge can never be an outdent, so the chain always lands). Keep this
    mapping separate from ``edges``: geometry (``edges[col]`` indents,
    coverage bands, span tests) stays physical; only ordering and
    column-identity tests read the mapped index.
    """
    if len(edges) < 2:
        return list(range(len(edges)))
    evidence = [[] for _ in edges]
    for pi in range(len(pages)) if pi_list is None else pi_list:
        if pi == 0:
            continue
        page, pdict = pages[pi]
        for b in pdict["blocks"]:
            if (
                b["type"] != 0
                or b.get("aside") is not None
                or _is_spanning(b, edges)
            ):
                continue
            text = _block_text(b, lang)
            if (
                text
                and not _is_furniture(b, page, text, lang)
                and not _is_signature_furniture(
                    b, page.rect.height, boilerplate
                )
            ):
                evidence[_assign_column(b, edges)].append((b, text))
    outdent = [
        i + 1 < len(edges)
        and len(ev) >= OUTDENT_MIN_BLOCKS
        and sum(
            _outdent_like(
                b,
                t,
                edges[i] + (edges[i + 1] - edges[i]) * OUTDENT_BAND_FILL,
            )
            for b, t in ev
        ) / len(ev)
        >= OUTDENT_HEADING_SHARE
        for i, ev in enumerate(evidence)
    ]
    outdent[-1] = False
    own = {}
    for i, is_outdent in enumerate(outdent):
        if not is_outdent:
            own[i] = len(own)
    return [
        own[i]
        if not outdent[i]
        else own[next(j for j in range(i + 1, len(edges)) if not outdent[j])]
        for i in range(len(edges))
    ]


def _grids_agree(a, b, tol=ZONE_EDGE_TOL):
    """Same grid: equal edge count and pairwise positions within ``tol``."""
    return len(a) == len(b) and all(
        abs(x - y) < tol for x, y in zip(a, b)
    )


def _union_length(ivs):
    """Total length of the union of ``(lo, hi)`` intervals."""
    total = 0.0
    lo = hi = None
    for a, b in sorted(ivs):
        if hi is None:
            lo, hi = a, b
        elif a <= hi:
            hi = max(hi, b)
        else:
            total += hi - lo
            lo, hi = a, b
    return total if hi is None else total + hi - lo


def _strong_grid(cand, items, line_h, body_size):
    """Whether a page's own candidate grid carries real column evidence.

    The strength gate behind ``_page_grids`` (#37): a local grid must
    earn the right to override the document-wide prior. Every proposed
    column needs — all clauses, not most —

    * support: ≥``ZONE_COL_MIN_BLOCKS`` non-spanning text blocks
      assigned to it (an isolated x0 cluster is insufficient);
    * spread: those blocks span >``ZONE_COL_MIN_SPREAD``×line_h in y —
      a lone heading/byline cluster shares a y-band and fails;
    * coverage: the union of its support blocks' y-intervals covers
      ≥``ZONE_COL_MIN_COVER`` of the page's text extent — a real column
      flows through most of the page, while an author grid, formula
      fragment, or caption strip occupies a few bands of it;
    * width: median block x-extent ≥``ZONE_COL_MIN_WIDTH``×body_size —
      a column holds ~8em of prose; table cells and margin strips run
      narrower;
    * gutter: at most ``ZONE_GUTTER_GRAZE`` of the blocks it claims
      reach within ``ZONE_GUTTER_TOL`` of the next edge — a real
      column's text stops at its own right bound, while indent
      clusters and hanging reference starts ride right through the
      proposed boundary.

    Spanning blocks stay out of the support set on the first four
    clauses — they legitimately cover the gutter — but count in the
    graze share's claims: the 12pt span window already strips every
    edge-reacher out of support, so measuring ``sup`` alone leaves the
    clause dead for any realistic edge gap. One weak column demotes
    the whole candidate: partial grids reorder nothing safely.
    """
    if len(cand) < 2 or not items:
        return False
    cols: list[list[dict]] = [[] for _ in cand]
    for b in items:
        cols[_assign_column(b, cand)].append(b)
    text_extent = (
        max(b["bbox"][3] for b in items)
        - min(b["bbox"][1] for b in items)
    )
    for i, col in enumerate(cols):
        sup = [b for b in col if not _is_spanning(b, cand)]
        if len(sup) < ZONE_COL_MIN_BLOCKS:
            return False
        if (
            max(b["bbox"][1] for b in sup) - min(b["bbox"][1] for b in sup)
            < ZONE_COL_MIN_SPREAD * line_h
        ):
            return False
        covered = _union_length(
            (b["bbox"][1], b["bbox"][3]) for b in sup
        )
        if covered < ZONE_COL_MIN_COVER * text_extent:
            return False
        if (
            median(b["bbox"][2] - b["bbox"][0] for b in sup)
            < ZONE_COL_MIN_WIDTH * body_size
        ):
            return False
        if i + 1 < len(cand):
            grazers = sum(
                b["bbox"][2] > cand[i + 1] - ZONE_GUTTER_TOL for b in sup
            )
            if grazers > ZONE_GUTTER_GRAZE * len(sup):
                return False
    return True


def _page_grids(pages, doc_edges, boilerplate, line_h, body_size,
                lang: LangProfile = EN):
    """Each page's active column grid: strong local candidates, else the
    document-wide grid as prior.

    ``_doc_column_edges`` commits one grid for the whole document — a
    section whose columns don't recur on ≥half of all pages loses its
    edges and every block collapses onto one column, the class-3
    zipper (#37). This pass recomputes each page's own cluster
    evidence under the same eligibility filter the doc vote uses
    (non-furniture, non-aside, non-signature text blocks) and lets a
    *strong* local grid override the prior — the mirrored-margin case
    (four real edge positions each appearing on <50% of pages) and the
    minority-extra-columns case are both admitted, while heading,
    byline, indent, and table-cell clusters cannot masquerade as
    columns (``_strong_grid``). A single-edge candidate always wins:
    one column can never interleave, and its measured position feeds
    the indent/margin geometry better than the prior's; a page with no
    cluster quorum keeps the prior.

    Returns ``(page_edges, sources, zones)``: ``page_edges[pi]`` is the
    page's active grid; ``sources[pi]`` records *why* — ``"page"`` for
    a strong local candidate, ``"doc"`` when the local cluster already
    agreed with the prior, ``"doc-weak"`` when a differing candidate
    failed the strength gate; ``zones`` maps each distinct active grid
    (``tuple``) to its member page indices — adjacency not required,
    since mirrored margins alternate parity.
    """
    page_edges: list[list[float]] = []
    sources: list[str] = []
    for page, pdict in pages:
        items = [
            b
            for b in pdict["blocks"]
            if b["type"] == 0
            and b.get("aside") is None
            and not _is_furniture(b, page, _block_text(b, lang), lang)
            and not _is_signature_furniture(
                b, page.rect.height, boilerplate
            )
        ]
        cand = _column_edges(items)
        adopt = cand != [0.0] and (
            len(cand) == 1
            or _strong_grid(cand, items, line_h, body_size)
        )
        if _grids_agree(cand, doc_edges):
            page_edges.append(doc_edges)
            sources.append("doc")
        elif adopt:
            page_edges.append(cand)
            sources.append("page")
        else:
            page_edges.append(doc_edges)
            sources.append("doc-weak")
    zones: dict[tuple, list[int]] = {}
    for pi, edges in enumerate(page_edges):
        for key, members in zones.items():
            if _grids_agree(list(key), edges):
                members.append(pi)
                break
        else:
            zones[tuple(edges)] = [pi]
    return page_edges, sources, zones


def _zone_reading_maps(pages, page_edges, zones, boilerplate,
                       lang: LangProfile = EN):
    """Per-page reading-column maps — one ``_reading_columns`` call per
    active grid.

    Outdent evidence pools across the pages sharing each zone's grid
    (``pi_list``), not the whole document: a mirrored-margin layout's
    parity zones each classify their own edges. ``page_edges`` only
    locates each page's zone; every member renders against the zone
    key's grid and map.
    """
    zone_of = [None] * len(page_edges)
    for key, members in zones.items():
        for pi in members:
            zone_of[pi] = key
    zone_maps: dict[tuple, list[int]] = {}
    page_col_map: list[list[int]] = []
    for key in zone_of:
        if key not in zone_maps:
            zone_maps[key] = _reading_columns(
                pages,
                list(key),
                boilerplate,
                lang,
                pi_list=zones[key],
            )
        page_col_map.append(zone_maps[key])
    return page_col_map


def _line_dir(line):
    """Axis direction of a line's reported ``dir``, or None when unreliable.

    ``dir`` is reported in unrotated mediabox space; only near-axis
    vectors count — diagonal, absent, or zero data can neither prove a
    line rotated nor veto the block as upright.
    """
    dx, dy = line.get("dir") or (0, 0)
    if abs(dx) > 0.9 >= abs(dy):
        return (1 if dx > 0 else -1, 0)
    if abs(dy) > 0.9 >= abs(dx):
        return (0, 1 if dy > 0 else -1)
    return None


def _upright_dir(page):
    """The page's upright reading direction in mediabox coordinate space.

    ``page.rotation_matrix`` carries a translation, so the pure rotation
    comes from ``fitz.Matrix(page.rotation)``: displayed-upright (1,0)
    mapped back through the inverse gives the mediabox ``dir`` a normal
    text line reports — (0,-1) on a /Rotate 90 page, (0,1) on /Rotate 270.
    """
    d = fitz.Point(1, 0) * ~fitz.Matrix(page.rotation)
    return (round(d.x), round(d.y))


def _is_margin_strip(block, page, clip=None):
    """Rotated-text strip in the outer 10% margins — rotated sidebar
    furniture (journal titles and preprint stamps running down the page
    edge, like ``arXiv:NNNN.NNNNN`` which overflows the legacy 25pt cap).

    The block must still be tall (>4x height/width) and sit in the outer
    margin; bbox and margin width compare in the same unrotated space
    (``page.mediabox``), because displayed ``page.rect`` swaps dimensions
    on /Rotate pages. A block qualifies when every line's ``dir`` is a
    reliable axis direction differing from the page's upright reading
    direction — any reliably upright line vetoes the classification for
    the whole block, regardless of width, so text that reads upright on
    screen (including on rotated pages) can never be dropped as a strip.
    When direction data is missing or unusable on some line the legacy
    <25pt shape applies as fallback — the veto still holds first.
    ``clip`` exempts blocks inside the rotated-table region ``order``
    builds via ``_strip_clip``: a table's outer cells share the strip's
    geometry but belong to the rasterized grid. The clip's legitimacy is
    established there — only band members reaching the body band seed
    it, so a run of margin strips forms no clip and cannot exempt its
    own members."""
    x0, y0, x1, y1 = block["bbox"]
    width = x1 - x0
    margin = page.mediabox.width
    if not (
        y1 - y0 > 4 * width
        and (x0 > margin * 0.9 or x1 < margin * 0.1)
    ):
        return False
    if clip is not None and fitz.Rect((x0, y0, x1, y1)).intersects(clip):
        return False
    upright = _upright_dir(page)
    dirs = [_line_dir(line) for line in _block_lines(block)]
    if any(d == upright for d in dirs):
        return False
    if dirs and all(d is not None for d in dirs):
        return True
    return width < 25


def _is_pattern_furniture(block, page, text, lang: LangProfile = EN):
    """Pattern furniture or a pure page number in the outer 7% edge —
    content patterns come from the document's language profile, and the
    zone compares displayed ``page.rect`` like the signature layer."""
    _, y0, _, y1 = block["bbox"]
    return (y0 > page.rect.height * 0.93 or y1 < page.rect.height * 0.07) and (
        lang.furniture.search(text)
        or re.fullmatch(r"\d{1,4}", text)
    )


def _is_furniture(block, page, text, lang: LangProfile = EN, clip=None):
    """Edge furniture by shape or content: rotated margin strips in the
    outer 10% margins, or pattern furniture/page numbers in the outer 7%
    edge. Content patterns come from the document's language profile.
    ``clip`` exempts the strip arm only (a rotated table's outer cells);
    edge zones stay on displayed ``page.rect`` like the signature layer."""
    return _is_margin_strip(block, page, clip) or _is_pattern_furniture(
        block, page, text, lang
    )


def _is_signature_furniture(block, height, boilerplate):
    """Edge-zone text whose normalized signature repeats across pages —
    a running head/footer detected by recurrence rather than content."""
    _, y0, _, y1 = block["bbox"]
    near_edge = y1 < height * EDGE_ZONE or y0 > height * (1 - EDGE_ZONE)
    return near_edge and _signature(_block_text(block)) in boilerplate


def _lines_bbox(lines):
    """Union bbox of a line list — the recomputed bounds of a split block."""
    return (
        min(l["bbox"][0] for l in lines),
        min(l["bbox"][1] for l in lines),
        max(l["bbox"][2] for l in lines),
        max(l["bbox"][3] for l in lines),
    )


def _signature_prefix_len(block, height, boilerplate, lang: LangProfile = EN):
    """Leading lines whose joined signature is boilerplate, else 0.

    A running head fused to the block below it (a title, an abstract
    opener) escapes ``_is_signature_furniture`` twice over: the whole
    block's signature never matches and its bbox straddles the edge
    zone. The prefix test fires only when the block's *first* line lies
    in an edge zone — the line-level version of the signature arm's zone
    test — then joins leading lines incrementally, bounded by
    ``SIGNATURE_PREFIX_MAX_LINES`` (the boilerplate set's own line
    bound — a matched join is already a verified recurring signature)
    and never consuming the whole block: a block whose whole signature
    is already boilerplate is the arm's own case and returns 0 outright
    — splitting it would drop the head lines but leak the remainder,
    which is not independently boilerplate, into body flow. The scan
    keeps the *longest* in-zone match, not the first: alternating heads
    nest as prefixes of each other (verso `JournalX`, recto `JournalX /
    Vol Twelve`), and a longer verified signature is always at least as
    correct — stopping at k=1 would leave the real head's tail fused to
    the body block, the same leak the split exists to fix. The matched
    prefix's union bbox must stay inside the zone: the split head
    classifies as signature furniture organically downstream, while a
    prefix straddling the zone boundary would leak into body flow as a
    paragraph. The union only grows with each added line, so leaving
    the zone is monotone and ends the scan.
    """
    lines = _block_lines(block)
    if len(lines) < 2 or not boilerplate:
        return 0
    first = lines[0]["bbox"]
    if not (
        first[3] < height * EDGE_ZONE
        or first[1] > height * (1 - EDGE_ZONE)
    ):
        return 0
    if _signature(_block_text(block, lang)) in boilerplate:
        return 0
    texts = [_line_text(line).strip() for line in lines]
    best = 0
    for k in range(
        1, min(SIGNATURE_PREFIX_MAX_LINES, len(lines) - 1) + 1
    ):
        _, py0, _, py1 = _lines_bbox(lines[:k])
        if not (
            py1 < height * EDGE_ZONE or py0 > height * (1 - EDGE_ZONE)
        ):
            break
        if _signature(_join_lines(texts[:k], lang)) in boilerplate:
            best = k
    return best


def _split_signature_prefixes(blocks, height, boilerplate, lang: LangProfile = EN):
    """Split fused signature prefixes off ``blocks``, returning a new list.

    Each block whose leading lines join into a boilerplate signature
    (see ``_signature_prefix_len``) becomes two blocks: the matched
    prefix — signature furniture everywhere downstream, dropped by
    default and kept verbatim at the page edge under ``--keep-headers``
    — and the remainder, an ordinary block with a recomputed bbox. The
    remainder re-enters the scan, so stacked heads each independently
    recurring (two running heads fused ahead of one title) split off one
    at a time instead of the second leaking into the body block. Runs
    once per page after the boilerplate set is built and before
    ordering, so column-edge voting, the strip clip, and render all see
    the split blocks — a recurring fused head can no longer vote a
    phantom column edge either.
    """
    out = []
    for block in blocks:
        rest = block
        while rest["type"] == 0:
            k = _signature_prefix_len(rest, height, boilerplate, lang)
            if not k:
                break
            lines = _block_lines(rest)
            out.append(
                dict(rest, lines=lines[:k], bbox=_lines_bbox(lines[:k]))
            )
            rest = dict(rest, lines=lines[k:], bbox=_lines_bbox(lines[k:]))
        out.append(rest)
    return out


def _tall_blocks(blocks):
    """Tall-narrow text blocks — the rotated-table seed shape (>4x
    height/width and >60pt tall), shared so the region, band, and clip
    paths can't drift on the literal thresholds."""
    return [
        b
        for b in blocks
        if b["type"] == 0
        and (b["bbox"][3] - b["bbox"][1]) > 4 * max(1, b["bbox"][2] - b["bbox"][0])
        and (b["bbox"][3] - b["bbox"][1]) > 60
    ]


def _rotated_table_bands(tall):
    """Y-ranges where ≥3 ``tall`` blocks overlap — the bands that make
    rotated text columns stand side by side. Events at the same y are
    applied together so a same-y end/start can't split a band."""
    if len(tall) < 3:
        return []
    events = sorted(
        [(b["bbox"][1], 1) for b in tall] + [(b["bbox"][3], -1) for b in tall]
    )
    bands = []
    cov = 0
    start = None
    i = 0
    while i < len(events):
        y = events[i][0]
        while i < len(events) and events[i][0] == y:
            cov += events[i][1]
            i += 1
        if start is None:
            if cov >= 3:
                start = y
        elif cov < 3:
            bands.append((start, y))
            start = None
    return bands


def _rotated_table_seeds(blocks):
    """The tall blocks belonging to a rotated table's shared band, or [].

    A member covers at least one band point — in *any* ≥3-coverage
    range, not only the first — so a band that opens in the margins and
    gains a body-reaching member later still counts, while a tall block
    disjoint from every band never seeds."""
    tall = _tall_blocks(blocks)
    bands = _rotated_table_bands(tall)
    return [
        b for b in tall
        if any(b["bbox"][1] < e and b["bbox"][3] > s for s, e in bands)
    ]


def _rotated_table_region(blocks):
    """Bbox of a rotated (landscape) table: ≥3 tall-narrow text blocks sharing
    a common y-point, i.e. rotated text columns standing side by side."""
    tall = _tall_blocks(blocks)
    if not _rotated_table_bands(tall):
        return None
    return fitz.Rect(
        min(b["bbox"][0] for b in tall),
        min(b["bbox"][1] for b in tall),
        max(b["bbox"][2] for b in tall),
        max(b["bbox"][3] for b in tall),
    )


def _strip_clip(page, blocks, boilerplate, lang: LangProfile = EN):
    """Clip exempting strip-shaped blocks claimed by a rotated table.

    The clip models what ``render`` will rasterize, so its seeds are the
    blocks that can still reach the body flow: anything the signature or
    pattern furniture arm removes is gone before render's region
    detection and cannot seed a clip that exempts strips for it. The
    region counts only when a band member reaches the body band — a
    band contained wholly in the outer margins is a run of strips, not
    a table, and must not exempt its own members."""
    text_blocks = []
    for block in blocks:
        if block["type"] != 0:
            continue
        text = _block_text(block, lang)
        if (
            text
            and not _is_signature_furniture(
                block, page.rect.height, boilerplate
            )
            and not _is_pattern_furniture(block, page, text, lang)
        ):
            text_blocks.append(block)
    margin = page.mediabox.width
    if not any(
        s["bbox"][0] <= margin * 0.9 and s["bbox"][2] >= margin * 0.1
        for s in _rotated_table_seeds(text_blocks)
    ):
        return None
    return _rotated_table_region(text_blocks)


# first column made almost entirely of enumeration/bullet marks is a
# list, not a table — rejecting it keeps list rendering intact
_LIST_CELL_RE = re.compile(r"^\s*(?:\d{1,3}[.)]|[•·\-–—*†‡§])\s*$")


@dataclass
class _TableRegion:
    """An upright grid table detected on a page.

    ``claimed`` holds the ``id()`` of every block the table swallows
    (cells plus caption); ``cols`` are the recovered column x-bounds;
    ``rows`` are emitted in order, each a list of ``(col, span, lines)``
    cells — ``lines`` are raw dict lines so the renderer can reuse
    ``_line_html`` for bold/italic/sup markers. ``cap_ids``/``cap_above``
    record which claimed blocks are caption material and whether the
    legend sits above the run — regions contending for one caption
    block resolve by ownership, not by dropping the weaker grid.
    """

    rect: fitz.Rect
    claimed: set = field(default_factory=set)
    cols: list = field(default_factory=list)
    rows: list = field(default_factory=list)
    header: bool = False
    caption: list | None = None
    cap_ids: set = field(default_factory=set)
    cap_above: bool = False


@dataclass
class _RasterRegion:
    """A page region emitted as one rasterized image.

    ``rect`` is the claim region in mediabox space — the caption-alt
    scan reads it as the figure's true extent. ``ids`` holds the
    ``id()`` of every block the raster swallows (a rotated table's
    cells by intersection; a figure's members by the figure pass's
    whole-block claim). ``kind`` selects the caption label set and
    asset-name prefix — ``"table"`` takes ``Table N`` legends;
    ``"figure"`` joins with the figure-region pass.
    """

    rect: fitz.Rect
    ids: frozenset = field(default_factory=frozenset)
    kind: str = "table"
    # the validated raster crop when it differs from ``rect`` (figure
    # claims rasterize an exact clip; tables pad inside _rasterize)
    clip: fitz.Rect | None = None
    # the raster's alt-donor caption block — a figure's anchor caption
    # stays in body flow; a margin legend bound by ``_bind_legend`` is
    # consumed by the alt and never re-emits as text
    caption: dict | None = None


def _upright_lines(blocks):
    """All ``(line, block_id)`` pairs of text blocks, minus rotated text.

    PyMuPDF reports each line's writing direction: ``dir`` is x-dominant
    for upright text and y-dominant for rotated text (which the
    rotated-table path owns). An aspect-ratio proxy is lossy — a
    legitimate narrow cell (a lone '7' is taller than wide) drops out of
    every band and unclaims its whole block under the straddler rule.
    """
    out = []
    for b in blocks:
        if b["type"] != 0:
            continue
        for l in _block_lines(b):
            dx, dy = l.get("dir", (1.0, 0.0))
            if abs(dx) >= abs(dy):
                out.append((l, id(b)))
    return out


def _line_bands(lines, line_h):
    """Cluster lines into row bands: groups whose y-ranges overlap.

    Bands are the atomic rows of grid analysis — cells in a band share a
    vertical slice regardless of which block they came from.
    """
    bands = []
    for l, bid in sorted(lines, key=lambda lb: (lb[0]["bbox"][1], lb[0]["bbox"][0])):
        y0, y1 = l["bbox"][1], l["bbox"][3]
        if bands and y0 < bands[-1][2] - 0.35 * line_h:
            bands[-1][0].append((l, bid))
            bands[-1][2] = max(bands[-1][2], y1)
        else:
            bands.append([[(l, bid)], y0, y1])
    return bands


def _band_cells(band_lines, min_gutter):
    """Split one band's lines into cells at x-gaps of ≥ ``min_gutter``.

    Returns ``[(x0, x1, [(line, block_id), ...])]`` left to right; lines
    closer than the gutter merge into one cell fragment.
    """
    cells = []
    for l, bid in sorted(band_lines, key=lambda lb: lb[0]["bbox"][0]):
        x0, x1 = l["bbox"][0], l["bbox"][2]
        if cells and x0 - cells[-1][1] >= min_gutter:
            cells.append([x0, x1, [(l, bid)]])
        elif cells:
            cells[-1][1] = max(cells[-1][1], x1)
            cells[-1][2].append((l, bid))
        else:
            cells.append([x0, x1, [(l, bid)]])
    return cells


def _interior_gaps(intervals, lo, hi, min_gutter):
    """Gaps in ``[lo, hi]`` left uncovered by every interval, ≥ min_gutter.

    These are the permanent vertical whitespace strips that delimit table
    columns: no row's text may cross them.
    """
    gaps = []
    cur = lo
    for a, b in sorted(intervals):
        if a - cur >= min_gutter:
            gaps.append((cur, a))
        cur = max(cur, b)
    return gaps


def _union_cols(ivs, min_gutter):
    """Column bounds implied by an interval union.

    Each column spans from the end of one permanent gutter (or the
    union's left edge) to the start of the next (or the right edge).
    Used for the provisional layout inside ``_grid_runs`` and for the
    final one in ``_run_layout`` — single-cell bands never feed the
    union, so a stub or colspan row can never reshape the columns.
    """
    lo = min(u[0] for u in ivs)
    hi = max(u[1] for u in ivs)
    cols = []
    prev = lo
    for g0, g1 in _interior_gaps(ivs, lo, hi, min_gutter):
        cols.append((prev, g0))
        prev = g1
    cols.append((prev, hi))
    return cols


def _grid_runs(bands, min_gutter, max_gap, lang: LangProfile = EN):
    """Group consecutive bands into runs whose union keeps ≥1 gutter.

    A band joins the open run only if its cells preserve a permanent
    interior whitespace strip — a full-width paragraph line kills every
    gutter and so closes the run (body text can't infiltrate a table).
    Single-cell bands are suspect: a narrow one commits only as a
    wrapped-cell continuation of the open row (occupied columns a strict
    subset with column 0 empty — the fold ``_assemble_rows`` applies), so
    a trailing ``Note.``/``n.s.`` line or a prose fragment is never
    absorbed as a fake sparse row; a wide one is held pending as a
    colspan candidate — absorbed if a multi-cell band resumes the grid,
    dropped as ordinary text if not. One pending band is a divider
    candidate; a second consecutive one means paragraph prose is filling
    the gap — the run closes instead of committing a run of colspan
    rows.
    """
    runs = []
    cur = None  # [bands, union, pending, open_row_cols]
    for band in bands:
        lines, y0, _y1 = band
        # a 'Table N …' legend belongs to the caption machinery, never
        # to a row: if it seeds or joins a run it becomes invisible to
        # _table_caption (which only inspects bands outside the run's
        # y-range) while the legend — and whatever shares its band —
        # is mangled into <td> cells; a legend starting in a later
        # column would even pass the wrapped-cell fold. A caption band
        # is a hard boundary: it closes the open run and is skipped.
        # Check every line — band order isn't x-sorted, so a legend
        # sharing its band with stray text may not open the band
        if any(
            lang.table_caption.match(
                "".join(s["text"] for s in l["spans"])
            )
            for l, _ in lines
        ):
            if cur is not None:
                runs.append(cur[0])
                cur = None
            continue
        cells = _band_cells(lines, min_gutter)
        if cur is not None:
            # gap measured from the last *kept* band — pending wide bands
            # count too, or a chain of them drifts the window past
            # max_gap and closes the run early (a spanning row followed
            # by more rows would sever the table in two). Both the last
            # committed and the pending band count — a stub committed
            # after the pending sits lower, and measuring from the
            # pending's bottom overstates the gap by up to a row
            last_y = cur[0][-1][2]
            if cur[2]:
                last_y = max(last_y, cur[2][-1][2])
            if y0 - last_y > max_gap:
                runs.append(cur[0])
                cur = None
        if len(cells) >= 2:
            ivs = [(c[0], c[1]) for c in cells]
            if cur is None:
                cur = [
                    [band], ivs, [],
                    _band_occupied(band, _union_cols(ivs, min_gutter),
                                   min_gutter),
                ]
                continue
            union = sorted(cur[1] + ivs)
            lo = min(u[0] for u in union)
            hi = max(u[1] for u in union)
            if _interior_gaps(union, lo, hi, min_gutter):
                cur[0].extend(cur[2])
                cur[2] = []
                cur[0].append(band)
                cur[1] = union
                cur[3] = _band_occupied(
                    band, _union_cols(union, min_gutter), min_gutter
                )
            else:
                runs.append(cur[0])
                cur = [
                    [band], ivs, [],
                    _band_occupied(band, _union_cols(ivs, min_gutter),
                                   min_gutter),
                ]
        elif cur is not None and cells:
            cx0, cx1 = cells[0][0], cells[0][1]
            lo = min(u[0] for u in cur[1])
            hi = max(u[1] for u in cur[1])
            if cx1 - cx0 < 0.7 * (hi - lo):
                # narrow lone cell — joins only as a wrapped-cell
                # continuation of the open row; anything else is prose
                # adjacent to the table, so it is not even held pending:
                # left out of the run it stays in the body flow instead
                # of surfacing as a fake sparse row, and it never feeds
                # _run_layout where a gutter-straddling interval could
                # kill the whole region
                # pendings stay pending — only a resuming multi-cell
                # band commits a colspan candidate, so a stub can't
                # flush intervening prose into the run as a merged row
                cols = _union_cols(cur[1], min_gutter)
                occ = _band_occupied(band, cols, min_gutter)
                if (
                    occ
                    and 0 not in occ
                    and occ < cur[3]
                    and len(occ) <= max(1, len(cols) - 2)
                ):
                    cur[0].append(band)
                    cur[3] |= occ
            else:
                # a wide band is held pending only while it can still
                # emit as a colspan row: a second consecutive one means
                # prose is filling the gap — close the run — and one
                # covering no recovered column can never emit, so it is
                # left to the body flow instead of claiming its block
                # for a <td> that never materializes
                if cur[2]:
                    runs.append(cur[0])
                    cur = None
                elif _band_occupied(
                    band, _union_cols(cur[1], min_gutter), min_gutter
                ):
                    cur[2].append(band)
    if cur is not None:
        runs.append(cur[0])
    # bands can land out of y order — a pending divider flushed by a
    # resuming band sits after stubs committed while it was held; sort
    # so emitted rows follow reading order
    return [sorted(run, key=lambda b: b[1]) for run in runs]


def _run_layout(bands, min_gutter):
    """Recover column bounds from a run's multi-cell bands.

    Column i spans from the end of the previous permanent gutter (or the
    region's left edge) to the start of the next. Only multi-cell bands
    feed the interval union — a lone-cell band (wrapped-cell stub or
    colspan row) may legitimately sit across a gutter and must not
    reshape the columns; a stub covering the sole surviving gutter used
    to empty the union and reject the whole region. The region rect still
    covers every committed band. Returns ``(region_rect, cols)`` or None
    when no gutter survives.
    """
    ivs = []
    x0s, y0s, x1s, y1s = [], [], [], []
    for lines, y0, y1 in bands:
        cells = _band_cells(lines, min_gutter)
        x0s.append(min(c[0] for c in cells))
        x1s.append(max(c[1] for c in cells))
        y0s.append(y0)
        y1s.append(y1)
        if len(cells) >= 2:
            ivs += [(c[0], c[1]) for c in cells]
    cols = _union_cols(ivs, min_gutter)
    if len(cols) < 2:
        return None
    return (
        fitz.Rect(min(x0s), min(y0s), max(x1s), max(y1s)),
        cols,
    )


def _cells_cols(band, cols, min_gutter):
    """Assign a band's cells to column spans.

    A cell covers a column when it overlaps more than a third of the
    column's width; multi-column coverage becomes a colspan. Cells
    whose covered ranges overlap merge into one assign — a wide
    internal gap splits a logical cell into fragments that must not
    emit as extra ``<td>``s. Returns a list of ``(col_start, span,
    cell)`` sorted by column.
    """
    out = []
    for c in _band_cells(band[0], min_gutter):
        covered = [
            i
            for i, (a, b) in enumerate(cols)
            if min(c[1], b) - max(c[0], a) > 0.35 * (b - a)
            or a - 1 <= c[0] <= b
        ]
        if not covered:
            continue
        col, span = covered[0], covered[-1] - covered[0] + 1
        for i, (pcol, pspan, pc) in enumerate(out):
            if pcol <= col < pcol + pspan or col <= pcol < col + span:
                lo, hi = min(pcol, col), max(pcol + pspan, col + span)
                pc[0] = min(pc[0], c[0])
                pc[1] = max(pc[1], c[1])
                pc[2] += c[2]
                out[i] = (lo, hi - lo, pc)
                break
        else:
            out.append((col, span, c))
    return out


def _band_occupied(band, cols, min_gutter):
    """Column indices a band's cells cover under ``cols`` — the
    'occupied' set ``_assemble_rows`` tests continuation folds against."""
    return {
        c
        for col, span, _cell in _cells_cols(band, cols, min_gutter)
        for c in range(col, col + span)
    }


def _assemble_rows(bands, cols, min_gutter, claimed):
    """Turn run bands into table rows, folding wrapped-cell continuations.

    A band whose occupied columns are a *strict* subset of the previous
    row's, with column 0 empty, and covering at most ``ncols - 2``
    columns is a continuation: each of its cells appends to the covering
    cell above (multi-line cell text). A band missing only column 0 is a
    real sparse row (empty category cell), not a wrap. Only lines of
    claimed blocks contribute cell text — straddlers stay in the body
    flow instead of being swallowed.
    """
    rows = []
    for band in bands:
        assigns = [
            (col, span, [lb for lb in c[2] if lb[1] in claimed])
            for col, span, c in _cells_cols(band, cols, min_gutter)
        ]
        assigns = [a for a in assigns if a[2]]
        if not assigns:
            continue
        occupied = {
            c for col, span, _l in assigns for c in range(col, col + span)
        }
        if (
            rows
            and 0 not in occupied
            and occupied < rows[-1][0]
            and len(occupied) <= max(1, len(cols) - 2)
        ):
            for col, span, lns in assigns:
                for pcol, pspan, plns in rows[-1][1]:
                    if pcol <= col < pcol + pspan:
                        plns += lns
                        break
                else:
                    rows[-1][1].append((col, span, lns))
                    rows[-1][1].sort(key=lambda t: t[0])
            rows[-1][0] |= occupied
            continue
        rows.append([occupied, assigns])
    return [assigns for _occ, assigns in rows]


def _join_cell_text(lns):
    """Plain text of a cell's ``(line, block_id)`` pairs."""
    return " ".join(
        "".join(s["text"] for s in l["spans"]).strip() for l, _ in lns
    ).strip()


def _table_caption(sbands, run, runs, line_h, lang: LangProfile = EN):
    """The band directly above or below the run if it reads as
    ``Table N ...`` — used as the ``<caption>`` and dropped from text.
    Bands iterate top-down, so an above-run match wins over a below one;
    and a below-run band that is itself within the caption window of a
    following run belongs to that table (legends conventionally sit
    above the grid they label — stacked grids share them downward).
    """
    ry0 = min(b[1] for b in run)
    ry1 = max(b[2] for b in run)
    starts = [min(b[1] for b in r) for r in runs if r is not run]
    for band in sbands:
        _l, y0, y1 = band
        text = " ".join(
            "".join(s["text"] for s in l["spans"]) for l, _ in band[0]
        )
        if not lang.table_caption.match(text):
            continue
        if 0 <= ry0 - y1 <= 2.5 * line_h:
            return band
        if 0 <= y0 - ry1 <= 2.5 * line_h and not any(
            0 <= o0 - y1 <= 2.5 * line_h for o0 in starts
        ):
            return band
    return None


def _region_from_run(run, runs, blocks, sbands, edges, min_gutter,
                     line_h, multi_col, lang: LangProfile = EN):
    """Validate one grid run into a ``_TableRegion`` or reject it.

    Guards (precision-first — a miss degrades to ordinary text flow, a
    false positive mangles it): needs ≥3 recovered rows (≥2 when a
    ``Table N`` caption anchors the region), ≥60% grid-dense rows,
    median band fill under 0.88 (prose fills its band), a first
    column that isn't just list markers, and no image overlap. Runs that
    span a document column edge additionally need a ``Table N`` caption
    AND interior gutters that don't merely mirror the page's own column
    edges — the page layout itself is not a table.
    """
    lay = _run_layout(run, min_gutter)
    if lay is None:
        return None
    rect, cols = lay
    if len(cols) < 2:
        return None
    spans_edge = multi_col and any(
        rect.x0 < e < rect.x1 for e in edges[1:]
    )
    if spans_edge and len(cols) - 1 <= len(edges) - 1 and all(
        any(
            cols[i][1] - 12 <= e <= cols[i + 1][0] + 12 for e in edges[1:]
        )
        for i in range(len(cols) - 1)
    ):
        return None
    caption_band = _table_caption(sbands, run, runs, line_h, lang)
    if spans_edge and caption_band is None:
        return None

    claim_rect = fitz.Rect(
        rect.x0 - 4, rect.y0 - 0.4 * line_h,
        rect.x1 + 4, rect.y1 + 0.4 * line_h,
    )
    member_bands = list(run)
    caption_bands = []
    if caption_band is not None:
        # a wrapped 'Table N …' legend puts its continuation in its own
        # band(s) — only bands made purely of the block whose line
        # actually opens with the label may join: a band shared with a
        # foreign block disqualifies the legend (straddler rule), which
        # also keeps the foreign block's own bands out of the caption
        cbid = next(
            (
                bid
                for l, bid in caption_band[0]
                if lang.table_caption.match(
                    "".join(s["text"] for s in l["spans"])
                )
            ),
            caption_band[0][0][1],
        )
        run_ids = {id(band) for band in run}
        caption_bands = [caption_band] + [
            band
            for band in sbands
            if band is not caption_band
            and id(band) not in run_ids
            and all(bid == cbid for _l, bid in band[0])
        ]
        for cb in caption_bands:
            claim_rect.y0 = min(claim_rect.y0, cb[1] - 0.3 * line_h)
            claim_rect.y1 = max(claim_rect.y1, cb[2] + 0.3 * line_h)
    if any(
        b["type"] == 1 and fitz.Rect(b["bbox"]).intersects(rect)
        for b in blocks
    ):
        return None
    # a block joins the table only if it sits inside the claim rect AND
    # every one of its lines lives in a member band — a block whose
    # lines spill into a non-member band is a straddler: it stays in the
    # body flow whole, so no line can be claimed away and then never
    # emitted (the emit loop swallows claimed blocks verbatim)
    member_lines = {
        id(l) for band in member_bands for l, _bid in band[0]
    }
    claimed = set()
    for b in blocks:
        if b["type"] != 0 or fitz.Rect(b["bbox"]) not in claim_rect:
            continue
        lids = {id(l) for l in _block_lines(b)}
        if lids and lids <= member_lines:
            claimed.add(id(b))
    caption = None
    if caption_band is not None:
        # caption bands count toward claiming only when the legend
        # itself validates — every block in its band claimable. If it
        # fails, they stay non-member and the legend remains in the
        # body flow instead of being swallowed un-emitted
        cap_lines = {
            id(l) for band in caption_bands for l, _bid in band[0]
        }
        all_lines = member_lines | cap_lines
        cand = set(claimed)
        for b in blocks:
            if b["type"] != 0 or fitz.Rect(b["bbox"]) not in claim_rect:
                continue
            lids = {id(l) for l in _block_lines(b)}
            if lids and lids <= all_lines:
                cand.add(id(b))
        # the legend must be a single claimable block — a caption band
        # mixing in foreign lines is ambiguous attribution, so it
        # degrades to prose rather than emit a polluted caption
        if {bid for _l, bid in caption_band[0]} == {cbid} and cbid in cand:
            claimed = cand
            caption = sorted(
                (
                    l
                    for band in caption_bands
                    for l, bid in band[0]
                    if bid in claimed
                ),
                key=lambda l: (l["bbox"][1], l["bbox"][0]),
            )
    # an edge-spanning grid needs the *validated* caption, not just a
    # matching band — a straddler legend is no proof this isn't layout
    if spans_edge and caption is None:
        return None
    rows = _assemble_rows(run, cols, min_gutter, claimed)
    if len(rows) < (2 if caption is not None else 3):
        return None
    dense = sum(len(r) >= 2 for r in rows) / len(rows)
    if dense < 0.6:
        return None
    fills = [
        sum(c[1] - c[0] for c in _band_cells(b[0], min_gutter))
        / max(1.0, rect.width)
        for b in run
        if len(_band_cells(b[0], min_gutter)) >= 2
    ]
    if fills and median(fills) > 0.88:
        return None
    # prose guards — uncaptioned only: cells that fill their columns
    # edge-to-edge or read like sentence fragments are wrapped paragraph
    # lines, not table content (real data cells are short and narrow)
    cell_fills = []
    cell_chars = []
    for band in run:
        for col, span, c in _cells_cols(band, cols, min_gutter):
            cw = sum(
                cols[i][1] - cols[i][0] for i in range(col, col + span)
            )
            cell_fills.append((c[1] - c[0]) / max(1.0, cw))
            cell_chars.append(len(_join_cell_text(c[2])))
    if caption is None:
        if cell_fills and median(cell_fills) > 0.9:
            return None
        if cell_chars and median(cell_chars) > 45:
            return None
    col0_texts = [
        _join_cell_text(c[2])
        for band in run
        for c in _band_cells(band[0], min_gutter)
        if c[0] <= cols[0][1] and c[1] > cols[0][0]
    ]
    if col0_texts and sum(
        bool(_LIST_CELL_RE.match(t)) for t in col0_texts
    ) / len(col0_texts) > 0.6:
        return None
    return _TableRegion(
        rect=rect,
        claimed=claimed,
        cols=cols,
        rows=rows,
        header=bool(
            rows
            and all(
                _line_is_boldish(l)
                for _col, _span, lns in rows[0]
                for l, _bid in lns
            )
        ),
        caption=caption,
        cap_ids={
            bid for band in caption_bands for _l, bid in band[0]
        }
        & claimed,
        cap_above=bool(
            caption_band is not None
            and caption_band[2] <= min(b[1] for b in run)
        ),
    )


def _line_is_boldish(line):
    """Every non-empty span bold — header-row signal for tables."""
    spans = [s for s in line["spans"] if s["text"].strip()]
    return bool(spans) and all(_span_is_bold(s) for s in spans)


def _table_regions(blocks, line_h, edges, page_width,
                   lang: LangProfile = EN):
    """Detect upright grid tables; returns a list of ``_TableRegion``.

    Candidate row bands must share permanent interior whitespace (the
    column gutters). Runs are found per document column and once at full
    page width when the document is multi-column.
    """
    bands = _line_bands(_upright_lines(blocks), line_h)
    min_gutter = max(6.0, 0.45 * line_h)
    max_gap = 3.0 * line_h

    bounds = list(edges) + [page_width]
    x_bands = [(e - 20, bounds[i + 1] - 8) for i, e in enumerate(edges)]
    if len(edges) > 1:
        x_bands.append((0.0, page_width))

    regions = []
    for bx0, bx1 in x_bands:
        sel = [
            (l, bid)
            for band in bands
            for l, bid in band[0]
            if l["bbox"][0] >= bx0 and l["bbox"][2] <= bx1
        ]
        sbands = _line_bands(sel, line_h)
        runs = _grid_runs(sbands, min_gutter, max_gap, lang)
        for run in runs:
            region = _region_from_run(
                run, runs, blocks, sbands, edges, min_gutter, line_h,
                len(edges) > 1, lang,
            )
            if region is not None:
                regions.append(region)
    # the same table can surface in both a column-local and the
    # full-width scan — on overlapping claims keep the richer region;
    # a column-local subset of a page-wide grid yields the same row
    # count, so break ties on recovered columns (scan order alone would
    # keep the poorer partial region, which is appended first). When
    # regions contend only for a shared caption block, the legend stays
    # with the run it sits above — the other keeps its grid, caption
    # stripped, rather than the whole region being discarded
    regions.sort(key=lambda r: (-len(r.rows), -len(r.cols)))
    kept = []
    for r in regions:
        drop = False
        for k in kept:
            shared = r.claimed & k.claimed
            if not shared:
                continue
            if shared <= r.cap_ids | k.cap_ids:
                if k.cap_above or not r.cap_above:
                    r.claimed -= shared
                    r.caption = None
                else:
                    k.claimed -= shared
                    k.caption = None
            else:
                drop = True
                break
        if not drop:
            kept.append(r)
    return kept


def _rotate_clip(rect, matrix):
    """Map a mediabox-space rect into the displayed space that
    ``page.get_pixmap(clip=...)`` expects; pass ``page.rotation_matrix``.

    ``get_text`` reports block bboxes in the page's unrotated mediabox
    coordinates, while ``get_pixmap`` interprets the clip in the rotated
    (displayed) coordinate system — on a ``/Rotate`` page the same table
    region sits somewhere else. ``rotation_matrix`` is exactly this map
    (the identity on unrotated pages).
    """
    return rect * matrix


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


def _footnote_tops(
    blocks,
    edges,
    body_size,
    page_height,
    line_h,
    lang: LangProfile = EN,
    seeds=None,
):
    """Map each column index to the y where its footnote region starts.

    Two detection paths: (a) blocks opening with a superscript digit in the
    lower half of the page, and (b) unnumbered footnote zones — a smaller-than
    -body block in the bottom 40% separated from the text above by a gap much
    larger than the line height (author notes sit under a rule or whitespace).

    ``seeds``, when given a dict, collects per-column seed provenance as
    tuples of evidence block ids — ``(block,)`` for the marker and
    note-start arms, ``(prev, cur)`` for a gap seed, whose neighbor is
    half the evidence. The figure pass's label mask compares the
    provenance of masked and unmasked views to tell a label-induced
    zone from a genuine one.
    """
    tops = {}
    cols = {}
    for b in blocks:
        if b["type"] != 0:
            continue
        col = _assign_column(b, edges)
        cols.setdefault(col, []).append(b)
        if b["bbox"][1] >= page_height * 0.45 and _sup_marker(b, body_size) is not None:
            tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
            if seeds is not None:
                seeds.setdefault(col, []).append((id(b),))
    for col, cblocks in cols.items():
        cblocks.sort(key=lambda b: b["bbox"][1])
        for prev, cur in zip(cblocks, cblocks[1:]):
            if (
                cur["bbox"][1] > page_height * 0.6
                and cur["bbox"][1] - prev["bbox"][3] > 2 * line_h
                and _block_median_size(cur) < body_size * 0.95
            ):
                tops[col] = min(tops.get(col, cur["bbox"][1]), cur["bbox"][1])
                if seeds is not None:
                    seeds.setdefault(col, []).append((id(prev), id(cur)))
                break
    for col, cblocks in cols.items():
        for b in cblocks:
            if (
                b["bbox"][1] > page_height * 0.55
                and _block_median_size(b) < body_size * 0.95
                and lang.note_start.match(_block_text(b, lang))
            ):
                tops[col] = min(tops.get(col, b["bbox"][1]), b["bbox"][1])
                if seeds is not None:
                    seeds.setdefault(col, []).append((id(b),))
    return tops


def _is_body_block(block, page, boilerplate, lang: LangProfile = EN,
                   clip=None):
    """True for blocks that reach the body flow — the predicate
    ``_ordered_blocks`` applies: no aside mark, nonempty text or media,
    not signature or pattern furniture. ``clip`` is the strip exemption
    ``_strip_clip`` computes once per page. The figure pass claims only
    body blocks; eval replays the same partition for parity."""
    if block.get("aside") is not None:
        return False
    if block["type"] != 0:
        return True
    text = _block_text(block, lang)
    if not text:
        return False
    return not (
        _is_signature_furniture(block, page.rect.height, boilerplate)
        or _is_furniture(block, page, text, lang, clip)
    )


def _vector_areas(page):
    """Connected vector-drawing regions — drawn artwork without pixels.

    Bound ``page.get_cdrawings()`` rects padded by ``DRAWING_PAD`` so thin
    or touching strokes connect (gaps <2pt join); near-page-size paths
    (width AND height >= ``PAGE_FRAME_RATIO`` of the unrotated page) are
    frames, not artwork, and never seed an area. Connectivity follows the
    padded rects themselves, not the union bbox, so empty space between
    paths can't absorb unrelated text. The compact-drawings call skips
    ``get_drawings``' per-item dict building — only rects matter here,
    and the probe stays on the expensive path long after the cheap
    evidence gates. Shared with ``dev/diagnose_layouts.py`` — one
    clustering feeds the figure pass and the survey's location buckets.
    """
    page_rect = page.rect * page.derotation_matrix
    remaining = []
    for drawing in page.get_cdrawings():
        rect = fitz.Rect(drawing["rect"])
        if not rect.is_valid or (
            rect.width >= PAGE_FRAME_RATIO * page_rect.width
            and rect.height >= PAGE_FRAME_RATIO * page_rect.height
        ):
            continue
        remaining.append(
            rect + (-DRAWING_PAD, -DRAWING_PAD, DRAWING_PAD, DRAWING_PAD)
        )
    regions = []
    while remaining:
        pending = [remaining.pop()]
        region = fitz.Rect(pending[0])
        while pending:
            current = pending.pop()
            for i in range(len(remaining) - 1, -1, -1):
                if current.intersects(remaining[i]):
                    neighbor = remaining.pop(i)
                    pending.append(neighbor)
                    region |= neighbor
        regions.append(region)
    return regions


# sentence-final punctuation with a boundary after it — decimals like
# "4.2 Å" don't read as prose, so in-artwork text carrying this is
# foreign prose, not a label
_SENTENCE_RE = re.compile(r"[.!?…]['\"”’)\]]?(?:\s|$)")


@dataclass
class _FigureCandidate:
    """A geometric figure candidate awaiting claim validation.

    ``rect`` unions the member bboxes — artwork rects and claimed label
    blocks — and is the figure extent the note-zone and table-conflict
    checks read. ``ids`` claims every member block, images included;
    ``text_ids`` is the text-only subset the provisional note mask
    removes. ``caption`` is the adjoining ``figure_caption`` block — it
    stays in the body flow, donates the raster's alt, and is a crop
    barrier like any foreign block. ``clip`` starts as the padded
    member bbox; ``_figure_crop`` validates it against the page's
    barriers before commit. ``short`` counts label members at or under
    ``FIGURE_LABEL_CHARS`` — the density signal.
    """

    rect: fitz.Rect
    ids: frozenset
    text_ids: frozenset
    caption: dict
    clip: fitz.Rect
    short: int


def _figure_caption_near(area, blocks, member_ids, edges, page_width,
                         lang: LangProfile = EN, texts=None):
    """The nearest ``figure_caption`` block adjoining the area.

    The legend below wins (journals set it under the figure), then the
    one above, inside the alt scan's 120pt window and sharing a column
    band via ``_overlap_cols`` — the same geometry ``_figure_alt``
    adopts under. A caption block is never a member — a fused-in
    caption/label mix convicts as a straddler while a ring-only
    caption is skipped outright — so it always reaches this scan, and
    ``Table N`` can't anchor: ``figure_caption`` excludes table labels
    by construction.
    """
    area_cols = _overlap_cols(area, edges, page_width)
    best = None
    for b in blocks:
        if b["type"] != 0 or id(b) in member_ids:
            continue
        if not lang.figure_caption.match(
            (texts or {}).get(id(b)) or _block_text(b, lang)
        ):
            continue
        if not area_cols & _overlap_cols(b["bbox"], edges, page_width):
            continue
        below = b["bbox"][1] - area[3]
        above = area[1] - b["bbox"][3]
        if below >= -10:
            side, gap = 0, below
        elif above >= -10:
            side, gap = 1, above
        else:
            continue
        if gap > 120:
            continue
        if best is None or (side, gap) < best[0]:
            best = ((side, gap), b)
    return best[1] if best else None


def _figure_candidates(page, blocks, edges, page_width,
                       lang: LangProfile = EN):
    """Geometric figure candidates on a page.

    All three signals must hold — a figure area (a raster image block
    or a merged vector-drawing region), at least ``FIGURE_MIN_LABELS``
    short fragments over it or within ``FIGURE_LABEL_PAD``, and an
    adjoining ``figure_caption``. A line lying over the area or
    touching the pad ring is a member line; a block claims only when
    every line is one (pad-ring members must additionally be short —
    panel letters, not wrapped body prose), while a block mixing
    member lines with foreign lines is a straddler and rejects the
    candidate outright. ``blocks`` is the page's body-flow view —
    aside members, furniture, and empty text never reach it, and their
    absence is exactly what keeps them out of figure claims.

    ``get_cdrawings()`` runs only when enough short fragments survive
    unexplained by the image blocks' own pads AND a figure caption
    exists to anchor a candidate — cheap image evidence first, the
    expensive vector probe only when all three signals can still meet.
    """
    pad = FIGURE_LABEL_PAD
    # per-block text/geometry computed once — every group and gate
    # reuses it; on label-heavy pages this is the difference between
    # the pass costing its gates and costing every rect it touches.
    # Line rects/texts stay lazy: only blocks reaching the pad ring
    # ever pay for them
    brects = {id(b): fitz.Rect(b["bbox"]) for b in blocks}
    btexts = {
        id(b): _block_text(b, lang) for b in blocks if b["type"] == 0
    }
    blinfo = {}

    def _lines_of(b):
        info = blinfo.get(id(b))
        if info is None:
            lines = b.get("lines") or []
            info = blinfo[id(b)] = (
                [fitz.Rect(l["bbox"]) for l in lines],
                [_line_text(l).strip() for l in lines],
            )
        return info

    shorts = [
        b
        for b in blocks
        if b["type"] == 0 and len(btexts[id(b)]) <= FIGURE_LABEL_CHARS
    ]
    if len(shorts) < FIGURE_MIN_LABELS:
        return []
    images = [
        b
        for b in blocks
        if b["type"] == 1 and len(b.get("image") or b"") >= MIN_IMAGE_BYTES
    ]
    padded_imgs = [
        brects[id(b)] + (-pad, -pad, pad, pad) for b in images
    ]
    areas = [(fitz.Rect(b["bbox"]), {id(b)}) for b in images]
    # short fragments outside every image pad need the vector pass to
    # explain them — but only when a figure caption exists to anchor a
    # candidate; without all three signals nearby the expensive
    # get_cdrawings probe can never commit
    if any(
        not any(
            p.intersects(brects[id(b)] + (-pad, -pad, pad, pad))
            for p in padded_imgs
        )
        for b in shorts
    ) and any(
        b["type"] == 0
        and lang.figure_caption.match(btexts[id(b)])
        for b in blocks
    ):
        areas += [(r, set()) for r in _vector_areas(page)]

    # merge areas a small gap apart — adjacent panels and images inside
    # a drawn frame read as one figure. Each new area absorbs every
    # existing group its growing union touches, transitively — a vector
    # area must fold all the image panels inside it, not just the first
    # it hits. The gap stays tighter than the label pad so edge
    # decoration (logos, rule marks) can't bridge into the artwork
    merged = []
    mgap = FIGURE_AREA_MERGE
    for rect, img_ids in areas:
        rect_u, ids_u = fitz.Rect(rect), set(img_ids)
        hits = [
            m
            for m in merged
            if (rect_u + (-mgap, -mgap, mgap, mgap)).intersects(m[0])
        ]
        while hits:
            m = hits.pop()
            rect_u |= m[0]
            ids_u |= m[1]
            merged.remove(m)
            hits = [
                m
                for m in merged
                if (rect_u + (-mgap, -mgap, mgap, mgap)).intersects(m[0])
            ]
        merged.append([rect_u, ids_u])

    candidates = []
    for area, img_ids in merged:
        label_pad = area + (-pad, -pad, pad, pad)
        members, short_n, straddler = [], 0, False
        for b in blocks:
            if b["type"] != 0 or id(b) in img_ids:
                continue
            br = brects[id(b)]
            if not label_pad.intersects(br):
                continue  # no line of it can reach the area or the ring
            text = btexts[id(b)]
            short_b = len(text) <= FIGURE_LABEL_CHARS
            lrects, ltexts = _lines_of(b)
            # a member line lies over the area; for a short block the
            # pad ring counts too — panel letters hang just outside the
            # artwork, but a long pad-ring block is prose, not a label
            mline = [
                area.intersects(lr)
                or (short_b and label_pad.intersects(lr))
                for lr in lrects
            ]
            if mline and all(mline):
                if lang.caption.match(text):
                    # a caption fused into the artwork is the ambiguous
                    # mix the straddler rule rejects; one reaching only
                    # the pad ring is the claim's own anchor or a crop
                    # barrier — never a label member — so leave it to
                    # those gates instead of vetoing itself
                    if area.intersects(br):
                        straddler = True
                        break
                    continue
                # fully enclosed by the artwork → figure text outright
                # (stacked legends, abbreviations); at the edge or in
                # the pad ring, only label-like text joins — sentence-
                # bearing blocks stay foreign barriers
                if area.contains(br) or not _SENTENCE_RE.search(
                    text
                ):
                    members.append(b)
                    short_n += short_b
            elif mline and any(
                m and len(lt) <= FIGURE_LABEL_CHARS
                for lt, m in zip(ltexts, mline)
            ):
                # a label-shaped line fused to foreign lines — the
                # ambiguous mix the claim rule rejects; a pure-prose
                # block grazing the pad (a wrapped legend) is only a
                # barrier, which the crop stage can shrink around
                straddler = True
                break
        if straddler or short_n < FIGURE_MIN_LABELS:
            continue
        member_ids = img_ids | {id(b) for b in members}
        caption = _figure_caption_near(
            area, blocks, member_ids, edges, page_width, lang, btexts
        )
        if caption is None:
            continue
        union = fitz.Rect(area)
        for b in members:
            union |= brects[id(b)]
        mb = page.mediabox
        clip = union + (
            -FIGURE_CLIP_PAD, -FIGURE_CLIP_PAD,
            FIGURE_CLIP_PAD, FIGURE_CLIP_PAD,
        )
        clip = fitz.Rect(
            max(0, clip[0]),
            max(0, clip[1]),
            min(mb.width, clip[2]),
            min(mb.height, clip[3]),
        )
        candidates.append(
            _FigureCandidate(
                rect=union,
                ids=frozenset(member_ids),
                text_ids=frozenset(id(b) for b in members),
                caption=caption,
                clip=clip,
                short=short_n,
            )
        )
    return candidates


def _figure_crop(union, clip, barriers):
    """Shrink ``clip`` past ``barriers``, or return None to reject.

    A barrier crossing the member union can't be excluded without
    cutting artwork or a label — reject. One inside only the padded
    margin yields on the axis where it clears the union, keeping every
    member inside. Shrinks are monotone, so a single pass leaves a clip
    that still contains the union and touches no barrier — foreign
    prose, the anchor caption, kept furniture, lifted asides, note
    blocks, accepted tables, and unclaimed images all stay out of the
    pixels as they stay in the text flow.
    """
    clip = fitz.Rect(clip)
    for r in barriers:
        if r.intersects(union):
            return None
        if not clip.intersects(r):
            continue
        if r[2] <= union[0]:
            clip[0] = max(clip[0], r[2])
        elif r[0] >= union[2]:
            clip[2] = min(clip[2], r[0])
        elif r[3] <= union[1]:
            clip[1] = max(clip[1], r[3])
        elif r[1] >= union[3]:
            clip[3] = min(clip[3], r[1])
        else:
            return None
    if clip.is_empty or not clip.contains(union):
        return None
    return clip


def _seeds_consistent(full, masked, mask_ids):
    """True when masking label blocks explains every note-seed change.

    A removed seed is legitimate when any of its evidence blocks is a
    figure member — a marker/note-start seed whose source was a label,
    or a gap seed whose neighbor was: the gap that made it was
    label-induced and collapses honestly once labels claim. A seed
    surviving on non-member evidence keeps its zone even when its gap
    neighbor changes (seeds compare by source block, the tuple's last
    id). A masked-only seed is always suspect — the mask itself carved
    the gap that made it, which is ambiguity, not a new note.
    """
    for col, seeds in full.items():
        keep = {s[-1] for s in masked.get(col, [])}
        for s in seeds:
            if s[-1] not in keep and not any(e in mask_ids for e in s):
                return False
    for col, seeds in masked.items():
        have = {s[-1] for s in full.get(col, [])}
        for s in seeds:
            if s[-1] not in have:
                return False
    return True


@dataclass
class _PageClaims:
    """A page's claim composition: note zones, raster regions, grids.

    ``foot_tops``/``foot_ids`` are the operative note view — the masked
    one when figure claims committed, the unmasked one otherwise.
    ``rasters`` holds ``_RasterRegion``s in no particular order — the
    render loop anchors each at its first member in reading order.
    ``grids`` are the accepted ``_TableRegion``s.
    """

    foot_tops: dict
    foot_ids: set
    rasters: list
    grids: list


def _page_claims(page, body_blocks, all_blocks, edges, page_width, line_h,
                 body_size, lang: LangProfile = EN):
    """Compose figure candidates with the page's note/table analysis.

    Figure candidates are detected on geometry alone, then validated
    against everything they could distort: provisional label members
    mask out of ``_footnote_tops`` only when every dismissed seed
    traces to a member and no masked-only seed appears; the surviving
    tops sweep *all* body blocks (labels included) into note zones the
    candidates must clear; the rotated-table and grid passes run on the
    provisional note view with figure members still visible, so an
    established table always wins the overlap. Crop barriers are every
    non-member source block — furniture and aside bboxes included even
    when they never reached the body flow — plus the accepted table
    regions and the sibling candidates themselves, so committed regions
    can't overlap. Any post-mask rejection discards the page's figure
    claims wholesale: note and table analysis recompute once with all
    blocks restored and figures are not retried on the page.
    """
    page_height = page.rect.height
    candidates = _figure_candidates(
        page, body_blocks, edges, page_width, lang
    )
    seeds_full: dict = {}
    tops_full = _footnote_tops(
        body_blocks, edges, body_size, page_height, line_h,
        lang=lang, seeds=seeds_full,
    )
    foot_tops = tops_full
    if candidates:
        mask = set().union(*(c.text_ids for c in candidates))
        masked = [b for b in body_blocks if id(b) not in mask]
        seeds_masked: dict = {}
        tops_masked = _footnote_tops(
            masked, edges, body_size, page_height, line_h,
            lang=lang, seeds=seeds_masked,
        )
        if _seeds_consistent(seeds_full, seeds_masked, mask):
            foot_tops = tops_masked
        else:
            candidates = []

    def _foot_ids(tops):
        return {
            id(b)
            for b in body_blocks
            if b["type"] == 0
            and (fc := _assign_column(b, edges)) in tops
            and b["bbox"][1] >= tops[fc] - 5
        }

    foot_ids = _foot_ids(foot_tops)
    # rotated tables keep their existing precedence over note zones —
    # a cell inside a zone still rasterizes with the table
    rasters = []
    if (tbl := _rotated_table_region(body_blocks)) is not None:
        rasters.append(
            _RasterRegion(
                rect=tbl,
                kind="table",
                ids=frozenset(
                    id(b)
                    for b in body_blocks
                    if fitz.Rect(b["bbox"]).intersects(tbl)
                ),
            )
        )
    raster_ids = set().union(*(r.ids for r in rasters)) if rasters else set()

    def _grids(fids):
        return _table_regions(
            [b for b in body_blocks if id(b) not in raster_ids | fids],
            line_h,
            edges,
            page_width,
            lang,
        )

    grids = _grids(foot_ids)
    if candidates:
        # genuine note zones: a horizontal band per column from its
        # surviving top down — physical edges, not reading columns
        zones = [
            (
                edges[c],
                top,
                edges[c + 1] if c + 1 < len(edges) else page_width,
                page_height,
            )
            for c, top in foot_tops.items()
        ]
        # barriers are blocks that produce foreign output: text blocks
        # (prose, captions, furniture, notes, asides — all still in
        # all_blocks) and images big enough to emit. Sub-threshold
        # images never emit, so painting them into the raster is the
        # point, not a leak
        foreign = {
            id(b): fitz.Rect(b["bbox"])
            for b in all_blocks
            if b["type"] == 0
            or len(b.get("image") or b"") >= MIN_IMAGE_BYTES
        }
        committed = []
        rejected = False
        for cand in candidates:
            if (
                any(
                    z[0] < cand.rect[2]
                    and cand.rect[0] < z[2]
                    and z[1] < cand.rect[3]
                    and cand.rect[1] < z[3]
                    for z in zones
                )
                or any(
                    r.rect.intersects(cand.rect)
                    or cand.ids & set(r.ids)
                    for r in rasters
                )
                or any(
                    g.rect.intersects(cand.rect) or cand.ids & g.claimed
                    for g in grids
                )
            ):
                rejected = True
                break
            barriers = (
                [
                    rect
                    for bid, rect in foreign.items()
                    if bid not in cand.ids
                ]
                + [r.rect for r in rasters]
                + [g.rect for g in grids]
                + [c.rect for c in candidates if c is not cand]
            )
            clip = _figure_crop(cand.rect, cand.clip, barriers)
            if clip is None:
                rejected = True
                break
            cand.clip = clip
            committed.append(cand)
        if rejected:
            # discard every provisional claim on the page — notes and
            # tables recompute once with all blocks restored
            foot_tops = tops_full
            foot_ids = _foot_ids(foot_tops)
            grids = _grids(foot_ids)
        else:
            for cand in committed:
                rasters.append(
                    _RasterRegion(
                        rect=cand.rect,
                        ids=cand.ids,
                        kind="figure",
                        clip=cand.clip,
                        caption=cand.caption,
                    )
                )
    return _PageClaims(
        foot_tops=foot_tops,
        foot_ids=foot_ids,
        rasters=rasters,
        grids=grids,
    )
