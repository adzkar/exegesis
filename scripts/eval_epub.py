#!/usr/bin/env python3
"""Evaluate a PDF -> EPUB conversion and print a severity-ranked report.

Usage: python3 scripts/eval_epub.py <input.pdf> <output.epub> [--lang CODE]

The report grades one conversion the same way every time so regressions
are visible: container validity, metadata sanity, classified word
coverage, TOC/headings, reading-order splices, table disposition,
figure coverage, notes integrity, and whether odd artifacts came from
the source text layer or the converter. Output is markdown; paste it
into an issue or read it inline. Exit code is `1` when any
High-severity finding exists (usable as a CI gate), `0` otherwise —
`2` marks operational failures (unreadable inputs, or a language the
pipeline has no `LangProfile` for — grading a `ja` conversion on
English patterns would assert a confident wrong verdict) — a broken
or ungradeable run can't masquerade as a gate failure.

Language: every language-bound pattern (legend labels, furniture
vocabulary, the notes-chapter title, the table-alt literal) resolves
through `exegesis.lang`'s `LangProfile` — `--lang` overrides the
EPUB's `dc:language`, which overrides the `en` default. Still
English-scoped (documented limits, not graded wrong — just blind
spots): the affiliation/contamination vocabulary, the references-zone
anchor, and the numbered-heading shape, plus `WORD_RE`'s ASCII word
model which fragments non-ASCII text identically on both sides of the
coverage diff so the bias mostly cancels.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from exegesis.const import BOILERPLATE_MAX_CHARS, BOILERPLATE_MAX_LINES
from exegesis.lang import LangProfile, get_lang
from exegesis.text import _block_lines, _block_text, _join_lines

WORD_RE = re.compile(r"[A-Za-z0-9]+")
TAG_RE = re.compile(r"<[^>]+>")
# Caption numbers mirror the pipeline's `_CAP_NUM`: `s?` supplementary
# prefix, letter suffix (`1a`) or parenthesized letter (`2(b)`); the
# range tail sits outside the capture so `Table 3–4.` keys on `3`.
CAP_NUM = r"s?\d+(?:[a-z]|\s*\([a-zA-Z]\))?"
CAP_RANGE = r"(?:\s*[–-]\s*s?\d+)?"


def _num_pat(num: str) -> str:
    """Escape a collected caption number for re-search: the pipeline's
    `\\s*\\(` means `Table 2 (b).` in the source may emit fused as
    `Table 2(b).` — spaces inside the number become `\\s*`."""
    return re.escape(num).replace("\\ ", r"\s*").replace(" ", r"\s*")


# Caption terminators mirror `lang._CAP_TAIL` — dash/.:/)/2+-space/EOL —
# so a sentence-initial 'Figure 3 shows…' (the results-section norm)
# reads as prose, never as a legend, on either side.
_CAP_TAIL_TXT = r"(?:\s+[—–-]|\s*[.:)]|\s{2,}|\n|$)"
# Markup-side equivalent for raw xhtml: same arms, plus a tag that
# encloses a terminator (`3<b>.</b>`), an element close — `Fig.
# 3</p>` is the markup analog of line-end — and a capital word:
# a bare-number caption fused at a paragraph start emits `Fig. 3
# More text…`, while sentence callouts continue lowercase after the
# number (`Figure 3 shows…`), so `\s+[A-Z]` separates them — `(?-i:)`
# keeps it case-sensitive inside the IGNORECASE searches.
_CAP_TAIL_XML = (r"(?:\s+[—–-]|\s*[.:)]|\s{2,}|\s+(?-i:[A-Z])|"
                 r"\s*<[^>]+>\s*[—–.:)-]|\s*</|$)")
# Collection uses the same `Table N` shape the disposition search
# requires — a bare citation `(see Table 5)` must not produce a
# 0-coverage "legend+data absent" High and fail the gate — and the same
# legend position: anchored at an extracted block start (blocks are
# flattened one-per-line before matching) so a wrapped `…in\nTable 5.
# The…` sentence or a cite-only `Table S3.` can't collect either. The
# full `_CAP_TAIL` set is accepted — `Table 5:`, `Table 5 —`, the
# Springer `Table 5  Title` two-space style, and bare-EOL `Table 5`
# labels all grade; only the single-space periodless style (`Table 5
# Descriptive`) stays unchecked — documented limit. The label
# alternations ride the resolved `LangProfile`'s
# `table_labels`/`figure_labels` so a `de` conversion collects
# `Tabelle`/`Abb.` legends with identical anchoring; the eval keeps its
# own capture/tail shapes (`(num)` group, `(?:^|\n)` block anchor, the
# wider TXT/XML terminators) because the profile's compiled
# `table_caption`/`figure_caption` are per-text, group-less, and use
# the narrower pipeline `_CAP_TAIL`.
def _table_legend_re(lang: LangProfile) -> re.Pattern:
    """`Table N`-equivalent source-side collector for `lang`'s table
    labels — see the collection comment above."""
    return re.compile(
        rf"(?:^|\n)\s*(?:{lang.table_labels})\b\s*\.?\s*"
        rf"({CAP_NUM}){CAP_RANGE}{_CAP_TAIL_TXT}",
        re.IGNORECASE)


def _fig_legend_re(lang: LangProfile) -> re.Pattern:
    """Same collector for `lang`'s figure-label set."""
    return re.compile(
        rf"(?:^|\n)\s*(?:{lang.figure_labels})\b\s*\.?\s*"
        rf"({CAP_NUM}){CAP_RANGE}{_CAP_TAIL_TXT}",
        re.IGNORECASE)


# A caption label glued mid-paragraph: "25+ chars of sentence text, then
# a bold Table/Fig N. legend inside the same <p>" — a legend that belongs
# to a floating object was spliced into body flow. The run-up excludes
# `<`/`>` so it can never cross or eat into a tag boundary — a bold
# caption opening its own <p> has no preceding text at all, and a
# `class="listitem"`-style attribute can't lend its characters to the
# 25-char threshold either; text sharing the paragraph still matches.
# The fig-and-table label union keeps parity
# with the figure collector — a spelled-out `Figure N.` splice counts
# too. A `,`/`;` right after `</b>` is sentence grammar — a bolded
# in-text citation (`…as shown in <b>Table 2.</b>`, we…`), not a
# splice. The label→number junction loop takes one dot, space, or
# `</b><b>` per step: an empty-able `\.?\s*` inside the repeat
# backtracks exponentially in the length of a whitespace run.
def _splice_re(lang: LangProfile) -> re.Pattern:
    """Mid-paragraph glued-legend detector — see the comment above."""
    return re.compile(
        rf"([^<>]{{25,}}<b>(?:{lang.figure_labels}|{lang.table_labels})"
        rf"(?:[.\s]|</b><b>)*" + CAP_NUM + r"(?:</b><b>\s*)*"
        + CAP_RANGE + r"\.)</b>(?!\s*[,;])", re.IGNORECASE)


# 'center'/'centre'/'college' are plausible surnames — flag them only in
# affiliation context (after a comma or `of`); the rest are never names,
# so a bare word match suffices (`laborator` is a stem, left unbound).
# One token may intervene — `Smith J, Boston College` and `Roe, Mayo
# Center` are the natural `Name, Place Institution` tail. English-only:
# `profile.affiliation` can't substitute — its bare `\s+Center\b` shape
# exists to *split* a byline tail, and on creator strings would flag
# surnames the name-context gate here was built to spare.
AFFIL_NAME_RE = re.compile(
    r"(?:,|\bof)\s+(?:\w+\s+)?(?:center|centre|college)\b",
    re.IGNORECASE)
AFFIL_WORD_RE = re.compile(
    r"\b(?:university|department|institute|hospital|school of)\b|"
    r"\blaborator", re.IGNORECASE)


def _creator_caption_re(lang: LangProfile) -> re.Pattern:
    """The byline merge can absorb a caption block whole — `Table 1.`
    has reached dc:creator — and it clears every affiliation/title
    gate. Names never carry a caption label + number, so it matches
    anywhere: a legend glued after the names is the same
    contamination."""
    return re.compile(
        rf"\b(?:{lang.figure_labels}|{lang.table_labels})\b\s*\.?\s*"
        rf"{CAP_NUM}",
        re.IGNORECASE)


def _flatten(block_text: str) -> str:
    """One extracted block → one line: newline runs collapse to a single
    space so `\\n` anchors mean block starts, but intra-line spacing is
    kept verbatim — the 2+-space caption tail (`Fig. 1  Title`, the
    Springer style `lang._CAP_TAIL` exists to catch) must survive for
    the `\\s{2,}` terminator arm to see it."""
    return re.sub(r"\s*\n\s*", " ", block_text)


def epub_members(path: Path) -> list[str]:
    """List archive members; a BadZipFile means the EPUB isn't a zip at all."""
    with zipfile.ZipFile(path) as z:
        return z.namelist()


def read_member(path: Path, name: str) -> str:
    """Decode one archive member. The zip is re-opened per call so each
    member read is an independent failure unit for the exit-2 net —
    a KeyError names the missing member, not a stale handle."""
    with zipfile.ZipFile(path) as z:
        return z.read(name).decode("utf-8", "replace")


def xhtml_text(raw: str) -> str:
    """Strip markup to plain text for word-level comparisons. `<head>`
    is dropped whole: every chapter `<title>` repeats the doc title,
    and counting it would inflate the emitted-word side once per
    chapter — enough to mask real loss near the >1% gate."""
    body = re.sub(r"<head\b.*?</head>", " ", raw,
                  flags=re.DOTALL | re.IGNORECASE)
    return html.unescape(TAG_RE.sub(" ", body))


def notes_text(raw: str, lang: LangProfile) -> str:
    """`xhtml_text` for notes.xhtml additionally drops the injected
    `<h1>{notes_title}</h1>` chapter title — emit machinery, not PDF
    words, so it can't count toward coverage either. The title comes
    from `lang` (`Anmerkungen`, `Notes`, `Notas`) so a localized notes
    heading isn't counted as lost source vocabulary."""
    return xhtml_text(re.sub(
        rf"<h1[^>]*>\s*{re.escape(lang.notes_title)}\s*</h1>", " ", raw,
        flags=re.IGNORECASE))


def opf_metadata(opf: str) -> dict[str, str]:
    """Pull dc:title/creator/language out of content.opf by tag name."""
    out = {}
    for tag in ("title", "creator", "language"):
        m = re.search(rf"<dc:{tag}[^>]*>(.*?)</dc:{tag}>", opf, re.DOTALL)
        out[tag] = html.unescape(m.group(1)).strip() if m else ""
    return out


def looks_like_junk_title(title: str) -> bool:
    """Heuristic: real titles are mostly letters. Pagination/ID junk like
    'OP-SCAN150067 1758..1768' carries '..'-ranges, dominant digit runs,
    or outright digit dominance — a real title keeps digits in the
    minority ('Cancer trends 1990-2019', 'COVID-19 in 2020'), so a bare
    ratio flags honest work, and real titles carry accession IDs
    ('Association of rs1137101 with obesity', 'The 100000 Genomes
    Project') so a 5+-digit run only counts when digits dominate the
    whole title ('OP-SCAN150067': 6 digits vs 6 letters). The pipeline
    only checks for the presence of letters, which these strings still
    pass."""
    if not title:
        return True
    if re.search(r"\d+\s*\.\.\s*\d+", title):
        return True
    digits = sum(c.isdigit() for c in title)
    letters = sum(c.isalpha() for c in title)
    if re.search(r"\d{5,}", title) and digits >= letters:
        return True
    return digits > letters and digits >= 10


SIG_RE = re.compile(r"[^a-z]+")


def boilerplate_signatures(pdf_pages) -> set[str]:
    """Running heads by recurrence, mirroring
    `analyze._boilerplate_signatures`: letters-only sigs of text blocks
    inside the outer 20% edge zones recurring on >= max(2, 40%*pages)
    pages — the zone restriction keeps body text repeated mid-page from
    being excused, and the 2-page floor stops a 1-page doc from calling
    every block 'repeated'. The dict pass supplies the line count the
    bound needs — block tuples can't."""
    counts: Counter = Counter()
    for page in pdf_pages:
        h = page.rect.height
        seen = set()
        for b in page.get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            if (b["bbox"][3] < h * 0.20
                    or b["bbox"][1] > h * 0.80):
                sig = SIG_RE.sub("", _block_text(b).lower())
                if (3 <= len(sig) <= BOILERPLATE_MAX_CHARS
                        and len(_block_lines(b)) <= BOILERPLATE_MAX_LINES):
                    seen.add(sig)
        counts.update(seen)
    return {s for s, n in counts.items()
            if n >= max(2, int(len(pdf_pages) * 0.4))}


def _page_ctx(page, boilerplate: set[str], lang: LangProfile):
    """Per-page context `_dropped` needs to mirror
    `order._ordered_blocks` exactly: the displayed rect (edge zones),
    the mediabox width and upright reading direction the margin-strip
    arm compares in unrotated space, dict blocks keyed by bbox (block
    tuples supply neither line dirs nor counts), and the same exemption
    clip `_ordered_blocks` builds through `_strip_clip` — seeds reaching
    the body band that still survive the signature/pattern arms — so a
    margin-adjacent cell isn't excused here as a strip while the
    converter rightfully rasterizes it, and a margin-strips-only band
    can't self-exempt on either side. `splits` mirrors the
    `_split_signature_prefixes` pre-pass `convert` runs before ordering —
    iterated like the pre-pass itself so stacked heads split one at a
    time: for each fused-head block, the split-off head lines' words
    (the furniture side) and the remainder's text/bbox (the kept
    side). The strip clip reads the same post-split block stream
    `_ordered_blocks` does — a fused block's head lines can't inflate
    the clip's tall-seed shape or stretch its region union."""
    from types import SimpleNamespace

    # lazy like main(): pulling analyze pulls fitz — an ImportError must
    # reach the exit-2 net, so it can't sit at module top
    from exegesis.analyze import (
        _lines_bbox,
        _signature_prefix_len,
        _strip_clip,
        _upright_dir,
    )
    from exegesis.text import _line_text

    pdict = page.get_text("dict")
    dmap = {tuple(b["bbox"]): b for b in pdict["blocks"]}
    splits = {}
    post = []
    for b in pdict["blocks"]:
        if b["type"] != 0:
            post.append(b)
            continue
        heads, rest = [], b["lines"]
        while len(rest) >= 2:
            k = _signature_prefix_len(
                dict(b, lines=rest), page.rect.height, boilerplate, lang)
            if not k:
                break
            heads.append(rest[:k])
            rest = rest[k:]
        if not heads:
            post.append(b)
            continue
        kept_bbox = _lines_bbox(rest)
        splits[tuple(b["bbox"])] = SimpleNamespace(
            head_words=WORD_RE.findall(
                " ".join(
                    _line_text(l) for group in heads for l in group
                ).lower()),
            kept_text="".join(_line_text(l) + "\n" for l in rest),
            kept_bbox=kept_bbox,
        )
        # the remainder is a synthetic block — give `_strip` its real
        # line dirs like a dict hit, not a miss that falls back to <25pt
        dmap[kept_bbox] = {"lines": rest}
        # and keep the same post-split stream ordering sees — each
        # matched head its own block, then the remainder
        post.extend(dict(b, lines=g, bbox=_lines_bbox(g)) for g in heads)
        post.append(dict(b, lines=rest, bbox=kept_bbox))
    return SimpleNamespace(
        page=page,
        rect=page.rect,
        mb_w=page.mediabox.width,
        upright=_upright_dir(page),
        dmap=dmap,
        splits=splits,
        rtbl=_strip_clip(page, post, boilerplate, lang),
    )


def _kept(block, ctx, boilerplate: set[str], lang: LangProfile):
    """The block's emitted `(bbox, text)`, or None when convert drops it.

    The emitted view is the post-split remainder when `convert`'s
    pre-pass detaches leading signature lines, else the tuple itself;
    the drop arms then apply to *that* view, not the pre-split tuple —
    a fused head's remainder can itself be signature furniture (stacked
    heads fused on one page split off one at a time, so each kept view
    re-enters the same checks), and expecting its text would report
    genuine loss on a correct conversion. `_dropped` consumers read
    text through this so a head's words can't seed legends, headings, or
    coverage windows the converter never emitted them to."""
    s = ctx.splits.get(tuple(block[:4]))
    bb, text = ((s.kept_bbox, s.kept_text) if s
                else (block[:4], block[4]))
    if _dropped((*bb, text), ctx, boilerplate, lang):
        return None
    return bb, text


def _strip(block, ctx) -> bool:
    """Mirror of `analyze._is_margin_strip` over the tuple's bbox plus the
    ctx's dict line data — the same mediabox-space margin test, rotated-
    table clip exemption, upright veto, all-rotated qualifier, and <25pt
    fallback when direction data is missing or unusable (a dict lookup
    miss counts as missing)."""
    import fitz  # lazy like main(): an ImportError must reach the exit-2 net

    from exegesis.analyze import _line_dir

    x0, y0, x1, y1 = block[:4]
    width = x1 - x0
    if not (y1 - y0 > 4 * width
            and (x0 > ctx.mb_w * 0.9 or x1 < ctx.mb_w * 0.1)):
        return False
    clip = ctx.rtbl
    if (clip is not None
            and fitz.Rect((x0, y0, x1, y1)).intersects(clip)):
        return False
    dblock = ctx.dmap.get((x0, y0, x1, y1))
    dirs = ([_line_dir(l) for l in dblock.get("lines", [])]
            if dblock else [])
    if any(d == ctx.upright for d in dirs):
        return False
    if dirs and all(d is not None for d in dirs):
        return True
    return width < 25


def _dropped(block, ctx, boilerplate: set[str],
             lang: LangProfile) -> bool:
    """Does `order._ordered_blocks` drop this `get_text("blocks")` tuple?
    Mirrors its rule exactly — `analyze._is_signature_furniture` (an
    edge-zone sig in the boilerplate set) or `analyze._is_furniture`:
    pattern furniture and pure page numbers only inside the outer 7%
    edge (a wider or unconditional zone would excuse real edge content
    as stripped), and `_strip` for the rotated margin strips in the
    outer 10% horizontal margins. One predicate per block, so a block
    qualifying under several arms (a content-matching head that also
    recurs — the norm on journal corpora) is dropped, and counted,
    once."""
    _, y0, _, y1, text = block[:5]
    h = ctx.rect.height
    joined = " ".join(text.split())
    # _is_furniture searches `_block_text` — `copy-`/`right` and
    # `D`/`oi:` heads read `copyright`/`Doi:` to the converter — so
    # the gate runs the pipeline's own `_join_lines` on the block's
    # lines rather than re-approximating its sequential per-fragment
    # arms (hyphen joins before lone-uppercase drop caps, both after
    # per-line strip, honoring `lang`'s `single_letter_words`), and
    # the furniture vocabulary is `lang.furniture` itself, not a copy —
    # a verbatim re-list is exactly the drift surface AGENTS.md warns
    # about when the profile's vocabulary next changes.
    dehyph = _join_lines(text.split("\n"), lang)
    return bool(
        ((y1 < h * 0.20 or y0 > h * 0.80)
         and SIG_RE.sub("", text.lower()) in boilerplate)
        or ((y0 > h * 0.93 or y1 < h * 0.07)
            and (lang.furniture.search(dehyph)
                 or re.fullmatch(r"\d{1,4}", joined)))
        or _strip(block, ctx))


def furniture_words(ctxs, boilerplate: set[str],
                    lang: LangProfile) -> Counter:
    """Counted vocabulary of words the converter *should* have stripped
    — the words of every block `_dropped` holds for, so the excuse
    mirrors the pipeline's own signals rather than approximating them.
    Counted, not a set: a running-head word excuses at most its own
    stripped occurrences, so `of`/`vol`/page digits can't
    blanket-excuse genuine body loss. Raw tokens are collected, not
    the dehyphenated gate text — the missing-side words are the
    unjoined fragments. A fused-head block contributes its split-off
    head's words, plus the remainder's when the drop arms hold for the
    remainder too (stacked heads whose own signatures recur — convert
    strips both halves, so both excuse)."""
    furn: Counter = Counter()
    for ctx in ctxs:
        for b in ctx.page.get_text("blocks"):
            if b[6] != 0:
                continue
            if s := ctx.splits.get(tuple(b[:4])):
                furn.update(s.head_words)
                if _dropped((*s.kept_bbox, s.kept_text),
                            ctx, boilerplate, lang):
                    furn.update(WORD_RE.findall(s.kept_text.lower()))
            elif _dropped(b, ctx, boilerplate, lang):
                furn.update(WORD_RE.findall(b[4].lower()))
    return furn


def rasterized_blocks(ctxs, boilerplate: set[str],
                      lang: LangProfile):
    """Words the rotated-table path carries only as pixels. Mirrors
    `analyze._rotated_table_region` over the page's body blocks
    (non-empty text blocks `_dropped` keeps, each read through its
    post-split `_kept` view so a fused head's lines can't seed or
    supply a cell): >=3 tall-narrow blocks
    (height > 4x width and > 60pt) sharing a y-point, region = the
    union bbox of every tall block. Then `render._render_page`'s
    `tbl_ids`: every body block whose bbox intersects the region leaves
    the text flow and lives only in an `images/table_*.png`. Returns
    the counted words of those blocks, the `Table N.`/`Fig. N` legends
    opening any of them (a legend inside the region is rasterized with
    its grid), the region count — at most one per page, as in the
    pipeline — and the claimed blocks' (page, index) positions so the
    F-check's coverage windows can mask cell text that can only ever
    survive as pixels (a word-multiset can't: `cell`/`0` are also
    common body words)."""
    import fitz  # lazy like main(): an ImportError must reach the exit-2 net
    words: Counter = Counter()
    legends: set[str] = set()
    fig_legends: set[str] = set()
    claimed: set[tuple[int, int]] = set()
    regions = 0
    for pi, ctx in enumerate(ctxs):
        raw = ctx.page.get_text("blocks")
        body = [(bi, *v) for bi, b in enumerate(raw)
                if b[6] == 0 and b[4].strip()
                and (v := _kept(b, ctx, boilerplate, lang))]
        tall = [bb for _, bb, _t in body
                if bb[3] - bb[1] > 4 * max(1, bb[2] - bb[0])
                and bb[3] - bb[1] > 60]
        cov, shared = 0, False
        for _, d in sorted([(bb[1], 1) for bb in tall]
                           + [(bb[3], -1) for bb in tall]):
            cov += d
            if cov >= 3:
                shared = True
                break
        if not shared:
            continue
        region = fitz.Rect(min(bb[0] for bb in tall),
                           min(bb[1] for bb in tall),
                           max(bb[2] for bb in tall),
                           max(bb[3] for bb in tall))
        regions += 1
        for bi, bb, bt in body:
            if fitz.Rect(bb).intersects(region):
                claimed.add((pi, bi))
                words.update(WORD_RE.findall(bt.lower()))
                legends.update(
                    n.upper() for n in _table_legend_re(lang).findall(
                        _flatten(bt)))
                fig_legends.update(
                    n.upper() for n in _fig_legend_re(lang).findall(
                        _flatten(bt)))
    return words, frozenset(legends), frozenset(fig_legends), regions, claimed


def classify_missing(missing: Counter, extra: Counter, furniture: Counter,
                     epub_text: str = "", pdf_text: str = ""):
    """Sort missing words into stripped furniture, rejoined-hyphenation
    (a fragment that is a prefix/suffix of an extra EPUB word), superscript
    splits (source token `Kral1` emits as `Kral` + superscript `1` —
    the halves gain adjacency vs the source, so a `covid19` hiding
    behind a `covid 19` that already recurs in the PDF stays
    genuine), or genuine loss.
    Furniture is
    checked first — a running-head word that happens to prefix an extra
    word is still furniture. Dehyphenation only ever concatenates a
    fragment *pair*, so the rejoined claim requires the complement to
    be missing too — a lone `the` prefixing an unrelated `them` is
    coincidence, not a join. Every excuse class is count-capped —
    furniture excuses at most its own stripped occurrences, a rejoined
    fragment at most `min(own, complement, merges)` instances, and a
    superscript split at most the adjacency the EPUB *gained* over the
    source — so unpaired
    excess stays genuine and feeds the >1% gate."""
    extra_words = list(extra)
    rejoined, furn, supers, genuine = Counter(), Counter(), Counter(), Counter()
    for w, n in missing.items():
        cap, cls = 0, genuine
        if furniture.get(w):
            cap, cls = min(n, furniture[w]), furn
        elif len(w) >= 2:  # `co-`/`re-`/`de-` halves are real joins
            # Sum per-target mins — a fragment can join several words
            # (`re`×2 → `rejoin`+`rework`), and `max` would under-excuse
            # the extras into genuine loss.
            merges = 0
            for e in extra_words:
                if e == w:
                    continue
                if e.startswith(w) and len(e) - len(w) >= 2:
                    merges += min(n, missing.get(e[len(w):], 0), extra[e])
                if e.endswith(w) and len(e) - len(w) >= 2:
                    merges += min(n, missing.get(e[:-len(w)], 0), extra[e])
            cap = min(n, merges)
            if cap:
                cls = rejoined
        if not cap and (m := re.match(r"^([a-z]+)(\d+)$|^(\d+)([a-z]+)$", w)):
            pair = [p for p in m.groups() if p]
            pat = re.compile(rf"(?<![0-9A-Za-z]){re.escape(pair[0])}\s+"
                             rf"{re.escape(pair[1])}(?![0-9A-Za-z])",
                             re.IGNORECASE)
            # `\s+` both sides: xhtml_text maps every tag (incl. <sup>)
            # to a space, so a real split always reads 'kral 1' — a
            # fused 'kral1' survivor is verbatim text, not adjacency —
            # while a fused 'Kral1' in the PDF is one word and can't
            # self-cancel the gain.
            adj = len(pat.findall(epub_text)) - len(pat.findall(pdf_text))
            cap = min(n, max(0, adj))
            if cap:
                cls = supers
        cls[w] += cap
        if n - cap:
            genuine[w] = n - cap
    return rejoined, furn, supers, genuine


# Abbreviation arms (df/CI/SD/SE) match case-insensitively — real header
# rows print them lowercase — but the word forms stay case-sensitive:
# 'Mean and max heart rate' is a heading, 'Mean SD Min Max' is a header
# row pretending to be one.
STAT_HDR_RE = re.compile(r"\b(?:(?i:df|CI|SD|SE)|Min|Max|Mean|Median)\b")
# A numbered heading keeps number and title on ONE extracted line — a
# `\s+` separator would let a numeric table cell adopt the label on the
# next line (`6.14\nGender` is not a section). One sublevel max so
# `6.14` decimals can't pass as `6.1` + stray digit; `Surname, I` tails
# are excluded so numbered references can't pose as sections either.
# No optional trailing period: numbered list items (`1. Collect
# samples.`, `2. Wash twice with PBS.`) share the `N. Title` shape and
# never reach the TOC, but real numbered headings almost never end in a
# sentence period — the line-end anchor discriminates for free. A
# leading dot is mandatory on single-level numbers only:
# footnote-style numbering (`1 The authors thank`, `2 Corresponding
# author`) carries none, so a periodless `2 Methods` style goes
# unchecked — but `N.N` subsections drop it freely (`2.1
# Participants`), the `\.?` tolerating both. The title's first word may
# be a lone capital (`2. A Bayesian Model`). Captured group 1 keeps
# the separator dot — callers rstrip it before arithmetic.
SECTION_RE = re.compile(
    r"^\s*(\d{1,2}\.\d\.?|\d{1,2}\.)[ \t]+"
    r"([A-Z][A-Za-z &,\-]{2,60})\s*$", re.MULTILINE)
# Vancouver-style numbered references survive the numbered-heading shape
# (`14. Kessler RC, Berglund P, Demler`, `15. Wang PS,`): reject a title
# carrying a surname + periodless-initials fragment, any title ending
# mid-list on a comma, or the `Surname, I` tail of an unwrapped entry.
REF_TAIL_RE = re.compile(r"([A-Z][a-z]+\s+[A-Z]{1,3}\s*,|,\s*[A-Z]{1,3}$|,$)")
# …and belt-and-suspenders: nothing numbered follows the references
# heading in a real paper, so drop every SECTION_RE hit inside the zone.
REF_ZONE_RE = re.compile(
    r"^\s*(?:\d{1,2}\.?\s+)?"
    r"(?:References(?: and Notes)?|Notes and References|Bibliography"
    r"|Literature Cited|Works Cited)\s*$",
    re.MULTILINE | re.IGNORECASE,
)


def header_like_titles(entries: list[str]) -> list[str]:
    """Nav titles built from table column headers (`Group M SD Min Max`,
    `Right df SE CI`) — a header row promoted to h2 both pollutes the
    TOC and explodes the table into pseudo-chapters."""
    return [e for e in entries if len(STAT_HDR_RE.findall(e)) >= 3]


def missing_sections(pdf_text: str, entries: list[str],
                     notes: str, lang: LangProfile,
                     has_headings: bool = True) -> list[str]:
    """Numbered headings (`2. Methods`, `3.1. Participants`) present in the
    source but absent from the TOC — the converter emits them as plain
    paragraphs, so the reader gets no way into the body. The alphabetic
    core is matched against each nav entry with its own leading number
    stripped — `2. Methods` finds a nav entry titled `Methods`, while an
    entry merely containing the word (`Materials and Methods`) does NOT
    count as coverage. Coverage is consumptive: a numbered `7. Methods`
    nav entry satisfies only a `7.` candidate and each entry covers one
    section, so `2. Methods` can't hide behind a same-titled sibling —
    multi-study papers reuse `Method`/`Results` per study. Two more
    discriminators keep numbered non-headings
    out: a candidate whose own numbered line survives in notes.xhtml
    (marker then title at a note-paragraph start) was a numbered note —
    a note merely containing the word 'results' must not silence a
    missing `2. Results` — and a candidate followed by a
    lowercase-starting line is a wrapped list item/sentence — real
    headings are followed by a new sentence. Scanning is per text line,
    matching the pipeline's per-line heading classification — an OCR
    block that merged `2. Methods\nWe recruited…` yields title
    `Methods`, not the sentence — and a candidate whose title carries a
    2+ lowercase-word run (heading fused to body with no line break at
    all) is rejected as sentence contamination. A candidate flanked by
    `N±1.`/`N±1)` lines with no body between is a numbered-list run
    (`1. Collect`, `2. Wash`) — periodless items share the `N. Title`
    shape but never reach the TOC. A heading can also wrap mid-title
    (`2. Materials and\nMethods`) — it counts as covered when a nav
    title continues with the next line's first word.

    Documented limit: the next/previous lines are read in extraction
    order (per-line text with `_dropped` blocks already out), not
    reading order, and the lowercase-run guard sacrifices recall on
    sentence-case heading styles (`4. Strengths and limitations of
    this study`) — a missing one is never reported. And the sublevel
    pattern caps at one digit after the dot: `2.10`/`10.11` subsections
    never collect at all (the anti-decimal trade — `6.14` must stay a
    number, not become `6.1` + stray digit). On scrambled-order
    or column-interleaved sources — the condition `order.py` exists to
    fix — the 'next line' can be an adjacent column's line or a
    footer, and its lowercase start (or a coincidental `N±1.` line)
    silences a genuinely missing heading. Mostly false negatives —
    though interleaving also breaks the *exclusion* arms: a column-
    interleaved numbered reference list loses the `N±1` adjacency that
    marks it as a list run, and surname-free entries
    (`15. Journal of Testing`) pass `REF_TAIL_RE`, so unanchored
    numbered references can surface as false findings. The lone-`N.`
    line is the same hole at size one: a single-item list
    (`1. Collect samples`) or a run whose neighbors interleaved away
    passes every discriminator and reports as a missing heading — text
    alone can't separate it from a demoted one, and two such lines
    trip the High gate on a correct conversion. A positional
    lookup over block bboxes is the upgrade path."""
    raw_pairs = []
    for e in entries:
        mm = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[.)]?\s*", e)
        raw_pairs.append((mm.group(1) if mm else None,
                          re.sub(r"^\s*\d+(?:\.\d+)?\s*[.)]?\s*", "",
                                 e.lower()).strip()))
    # Nav entries split by whether they carry a leading number: a
    # numbered `7. Methods` covers ONLY a `7.` candidate — a `2.
    # Methods` candidate can't hide behind its sibling's entry — while
    # unnumbered entries are consumed first-come. Each entry covers
    # exactly one section (Counter decrements on coverage), so a
    # single surviving `methods` can't mask a demoted same-title
    # sibling the way set-membership did.
    nav_numbered = Counter((n, t) for n, t in raw_pairs if n)
    nav_generic = Counter(t for n, t in raw_pairs if not n)
    # Subtract the synthesized entries: the notes-chapter link (one
    # exists iff notes content did — its title is `lang.notes_title`)
    # and a lone headingless-doc "Start" seed — all unnumbered. Each
    # subtracts one occurrence, so a real covered heading titled
    # `Notes` — a second identical entry — still masks; an *uncovered*
    # one doesn't. (Untitled-chapter `Section N` titles never reach
    # book.toc — they live on the EpubHtml `<title>` — so no
    # subtraction exists for them; masking a real entry titled that
    # would fabricate a finding.)
    if notes.strip():
        nav_generic.subtract({lang.notes_title.lower(): 1})
    if len(entries) == 1 and not has_headings:
        # The seed only fires on an EMPTY toc — a lone entry beside
        # real <h1>/<h2> markup is a legitimate single-heading TOC and
        # must keep masking. (It links the doc title, not 'Start'.)
        n0, t0 = raw_pairs[0]
        if n0:
            nav_numbered[(n0, t0)] -= 1
        else:
            nav_generic[t0] -= 1
    notes_paras = [" ".join(xhtml_text(p).split()).lower()
                   for p in re.findall(r"<p\b[^>]*>(.*?)</p>", notes,
                                       re.DOTALL)]
    # Anchor at the LAST line-shaped references heading — an early
    # stray (a verso running head) mustn't silence the whole check;
    # headings before the real bibliography still get examined.
    zones = list(REF_ZONE_RE.finditer(pdf_text))
    zone = zones[-1] if zones else None
    out = []
    for m in SECTION_RE.finditer(pdf_text):
        if zone and m.start() > zone.end():
            continue
        title = m.group(2).strip()
        num = m.group(1).rstrip(".")
        # A 2+ lowercase-word run is sentence grammar fused to the
        # heading — an OCR layer that merged heading and body on one
        # physical line (`2. Methods We recruited participants…`).
        # Lone connectors (`and`/`of`) never form a run; the cost is
        # recall on sentence-case heading styles, documented below.
        if re.search(r"\b[a-z]+\s+[a-z]", title):
            continue
        nxt = next((ln.strip() for ln in pdf_text[m.end():].split("\n")
                    if ln.strip()), "")
        # The note marker precedes its text — `<sup>2</sup>Results`
        # emits fused after tag-stripping, so separators are optional.
        note_re = (rf"[*†‡§\s]*{re.escape(num)}[.\s)]*"
                   rf"{re.escape(title.lower())}")
        if (any(re.match(note_re, p) for p in notes_paras)
                or (nxt and nxt[0].islower())):
            continue
        # Adjacent `N±1.`/`N±1)` lines with no body between are a
        # numbered-list run (`1. Collect`, `2. Wash`) — list items
        # never reach the TOC, so only an isolated numbered line can
        # be a demoted heading.
        seq = num.split(".")
        nxt_n = ".".join(seq[:-1] + [str(int(seq[-1]) + 1)])
        prv_n = ".".join(seq[:-1] + [str(int(seq[-1]) - 1)])
        prv = next((ln.strip() for ln in reversed(
                    pdf_text[:m.start()].split("\n")) if ln.strip()), "")
        if (re.match(rf"{re.escape(nxt_n)}[.)]\s", nxt)
                or re.match(rf"{re.escape(prv_n)}[.)]\s", prv)):
            continue
        # Two distinct sections can share a title (`3. Methods` and
        # `7. Methods`) — dedupe on (number, title), not title alone,
        # and each nav entry satisfies exactly one candidate.
        if (num, title) in out or REF_TAIL_RE.search(title):
            continue
        key = title.lower()
        if nav_numbered[(num, key)] > 0:
            nav_numbered[(num, key)] -= 1
            continue
        if nav_generic[key] > 0:
            nav_generic[key] -= 1
            continue
        # A numbered heading can wrap mid-title (`2. Materials
        # and\nMethods`) — covered when a nav title continues the
        # first word of the next line; a numbered entry only covers
        # its own number here too.
        if nxt:
            joined = f"{title} {nxt.split()[0]}".lower()
            hit = next((t for t, n in nav_generic.items()
                        if n > 0 and t.startswith(joined)), None)
            if hit is None:
                hit = next((k for k, n in nav_numbered.items()
                            if n > 0 and k[0] == num
                            and k[1].startswith(joined)), None)
            if hit is not None:
                (nav_generic if isinstance(hit, str)
                 else nav_numbered)[hit] -= 1
                continue
        out.append((num, title))
    return out


def nav_entries(nav: str) -> list[str]:
    """Nav titles scraped by regex rather than XML parsing — the nav is
    machine-generated by emit (predictable `<a href>` shape), and regex
    keeps the eval dependency-light on malformed-but-parseable input."""
    return [html.unescape(t).strip() for t in re.findall(r'<a href="[^"]*">(.*?)</a>', nav, re.DOTALL)]


# Block-level openers a legend can start inside — a `>` emitted by an
# inline tag (<b>/<i>/<a>/<sup>…) is a mid-sentence position, so
# `…in <b>Table 3.</b>` citations stay citations while `<p><b>Table
# 3.</b>` still counts. The repeated group also skips whitespace,
# `*†‡§` note markers, empty-position tags, and whole inline elements
# carrying text — restricted to `<sup>` so a styled lead-in
# (`<p><i>see</i> Table 3.`) stays a citation: a numbered note emits
# `<p class="fn"><sup>3</sup>Table 4.` where the marker element carries
# real text between the block opener and the legend. Every step
# consumes one unambiguous unit — the sup body a char or a tag that
# isn't `</sup>`, the generic arm any tag but a `<sup` opener (that's
# the sup arm's alone) — because py39 `re` has no atomic groups:
# nesting an empty-able `[^<]*` inside the repeat made a failing match
# retry every split of the sup text (2^n), and a 107-char `<sup>` at a
# block start hung the eval on a corpus paper.
_BLOCK_OPEN = (r"<(?:p|td|th|caption|figcaption|h[1-6]|blockquote|"
               r"dt|dd|li|div|section|thead|tbody)\b[^>]*>")
_LEGEND_AT = (rf"(?:^|\n|{_BLOCK_OPEN})(?:[\s*†‡§]|<(?!sup\b)[^>]+>|"
              rf"<sup\b[^>]*>(?:[^<]|<(?!/sup>)[^>]*>)*</sup>)*")


def _num_key(n: str) -> tuple:
    """Sort key for caption numbers: supplementary `S`-prefixed labels
    after regular ones, then numeric core, then suffix."""
    return (n.startswith("S"), int(re.sub(r"\D", "", n) or 0), n)


def _legend_present(raw: str, num: str, lang: LangProfile) -> bool:
    """A `Table N.` only counts as *the legend* in legend position — the
    start of a block element or extracted line. `results appear in
    <b>Table 3.</b>` ends a sentence with the same shape and the same
    tags, so an unanchored search (or one that accepts any tag end)
    lets a citation fake the landing spot — a swept or dropped table
    reporting 'prose' instead of 'notes'/'missing'."""
    return bool(
        re.search(rf"{_LEGEND_AT}(?:{lang.table_labels})\b\s*\.?\s*"
                  rf"{_num_pat(num)}{CAP_RANGE}{_CAP_TAIL_XML}",
                  raw, re.IGNORECASE)
    )


def _legend_in_notes(notes: str, lang: LangProfile,
                     num: str | None = None) -> bool:
    """A `Table N.` in notes.xhtml — legend-position anchored first.
    With `num` None it answers 'any table legend at all' and stays
    anchored-only: a sentence-ending citation (`…appear in Table 5.`)
    must not count as swept content. The per-num loose fallback exists
    for a legend fused mid-`<p class="fn">` after a numbered note
    (emit's `num is None` continuation merge) — it requires the match
    be followed by non-terminal content in the same paragraph, since a
    swept legend is followed by its cell/caption text while a citation
    ends the sentence at `</p>`. Documented bound: a citation that is
    NOT the paragraph's last sentence (`See data in Table 4. The
    authors…`) is textually identical to the fused-continuation case —
    legend-text follows either way — so a stray mid-paragraph citation
    can escalate a 'missing' table to 'notes'/High. Narrow coincidence
    (absent legend + same-number note citation), accepted for now."""
    if num is None:
        return bool(
            re.search(rf"{_LEGEND_AT}(?:{lang.table_labels})\b\s*\.?\s*"
                      rf"{CAP_NUM}{CAP_RANGE}{_CAP_TAIL_XML}",
                      notes, re.IGNORECASE))
    if _legend_present(notes, num, lang):
        return True
    return bool(
        re.search(rf"\b(?:{lang.table_labels})\b\s*\.?\s*"
                  rf"{_num_pat(num)}{CAP_RANGE}"
                  rf"(?:\s*[.:—–-]|\s{{2,}})\s*[^\s<]",
                  notes, re.IGNORECASE))


def _legend_near(chap: str, num: str, needle: str, lang: LangProfile,
                 radius: int = 1500) -> float:
    """Distance to the nearest `needle` occurrence within `radius` chars
    of this table's legend inside one chapter — `inf` when none. A
    detached caption (`<p>Table 3.</p>` beside its `<table>`) and a
    rasterized grid (`images/table_x.png` beside the in-flow legend)
    both keep the grid near its legend. Distances, not booleans: when
    a chapter mixes markup and raster tables, the *nearest* floating
    object owns the legend — a foreign `<table>` 1400 chars away
    can't shadow the `table_*.png` the legend belongs to. Ownership is
    checked, not just distance: a `<table>` already carrying a
    `Table M.` caption belongs to M — it can't also be this legend's
    stray grid — and a `table_*.png` sitting nearer to a different
    collected legend is that table's raster. Without the ownership
    pass a prose-flattened legend beside a foreign table misdiagnoses
    as 'detached'/'rasterized' (a Low) when the truth is 'prose'
    (a Med)."""
    m = re.search(rf"{_LEGEND_AT}(?:{lang.table_labels})\b\s*\.?\s*"
                  rf"{_num_pat(num)}{CAP_RANGE}{_CAP_TAIL_XML}",
                  chap, re.IGNORECASE)
    if not m:
        return float("inf")
    lo, hi = max(0, m.start() - radius), m.end() + radius
    best = float("inf")
    for mm in re.finditer(re.escape(needle), chap):
        p = mm.start()
        if not (lo <= p <= hi):
            continue
        if needle == "<table":
            end = chap.find("</table>", p)
            seg = xhtml_text(chap[p:end if end != -1 else len(chap)])
            if re.search(rf"(?:{lang.table_labels})\b\s*\.?\s*{CAP_NUM}"
                         rf"{CAP_RANGE}{_CAP_TAIL_TXT}",
                         seg, re.IGNORECASE):
                continue
        elif needle == "images/table_" and any(
                lm.group(1).upper() != num.upper()
                and abs(lm.start() - p) < abs(p - m.start())
                for lm in re.finditer(
                    rf"{_LEGEND_AT}(?:{lang.table_labels})\b\s*\.?\s*"
                    rf"({CAP_NUM}){CAP_RANGE}{_CAP_TAIL_XML}",
                    chap, re.IGNORECASE)):
            continue
        best = min(best, abs(p - m.start()))
    return best


def table_dispositions(blocks_text: str, win_text: str,
                       chapters: dict[str, str],
                       notes: str, furniture: Counter,
                       lang: LangProfile,
                       raster_legends: frozenset[str] = frozenset()):
    """For each `Table N` legend in the source, decide where it landed:
    inside <table> markup, beside <table> markup it failed to join
    (detached caption), beside a rasterized `images/table_*.png` (the
    intended rotated-table path — cells live in the image, not text),
    flattened into prose/blockquote, swept into notes.xhtml, or
    missing. A legend absent from the text whose own source block sat
    inside a rotated-table region (`raster_legends`) was rasterized
    with its grid by design — 'rasterized', not 'missing'. Legends
    require a trailing period
    (`Table N.`) at a block start (the caller passes block-flattened
    text — a wrapped sentence breaking at `Table 5.` stays mid-line),
    so in-text citations `(Table N)` don't fake a hit, and
    the EPUB side must match in legend position so a sentence-ending
    citation can't stand in for the legend. Content survival is scored
    with 4-grams — a lone digit or cell word coincidentally matching
    somewhere proves nothing, but a contiguous cell run can't fake its
    way in. Every `Table N.` occurrence gets its own window — iterated
    by position, not a greedy consume, so a citation <700 chars before
    the real legend can't swallow the grid's window, and the window is
    truncated at the *next* table legend so a surviving sibling can't
    lend its content — built from the first 55 tokens. Furniture words
    keep their stream positions: deleting them would fabricate
    n-gram adjacency that exists in neither text (`group0 12 0 3 0`
    reads `group0 0 0` once digits drop). Instead, grams containing a
    furniture word leave the denominator — stripped heads are
    correctly absent, so their grams are unassessable, not missing —
    and a window left with <5 furniture-free grams is skipped. Windows
    read `win_text` — the same block-flattened stream with
    region-claimed cell blocks masked out (rasterized text can only
    survive as pixels, so it can't drag a neighbor's coverage) —
    while legend collection keeps the unmasked `blocks_text`. The
    worst surviving window decides."""
    epub_tokens = []
    for c in chapters.values():
        epub_tokens += WORD_RE.findall(xhtml_text(c).lower())
    epub_tokens += WORD_RE.findall(notes_text(notes, lang).lower())
    grams = {tuple(epub_tokens[i:i + 4]) for i in range(len(epub_tokens) - 3)}
    legend_collect = _table_legend_re(lang)
    bounds = sorted(m.start() for m in legend_collect.finditer(win_text))
    out = []
    for num in sorted({n.upper() for n in legend_collect.findall(blocks_text)},
                      key=_num_key):
        legend_re = (rf"(?:^|\n)\s*(?:{lang.table_labels})\b\s*\.?\s*"
                     rf"{_num_pat(num)}{CAP_RANGE}{_CAP_TAIL_TXT}")
        coverages = []
        for m in re.finditer(legend_re, win_text, re.IGNORECASE):
            nxt = next((b for b in bounds if b > m.start()), m.end() + 700)
            toks = WORD_RE.findall(
                win_text[m.end():min(m.end() + 700, nxt)].lower())[:55]
            win = {tuple(toks[i:i + 4]) for i in range(len(toks) - 3)
                   if all(t not in furniture for t in toks[i:i + 4])}
            if len(win) < 5:
                continue
            coverages.append(sum(g in grams for g in win) / len(win))
        coverage = min(coverages) if coverages else None  # unassessed
        in_markup = any(_legend_present(seg, num, lang)
                        for c in chapters.values()
                        for seg in re.findall(r"<table\b.*?</table>", c, re.DOTALL))
        # Anchored notes presence beats the raster claim: if the legend
        # really emitted to notes, the mirror's `raster_legends` intersect
        # was wrong, not the pipeline. The loose fallback comes after —
        # a rasterized legend whose number a footnote merely *cites*
        # must not resolve 'notes'/High over 'rasterized'/Low.
        notes_anchored = _legend_present(notes, num, lang)
        in_notes = _legend_in_notes(notes, lang, num)
        in_chaps = any(_legend_present(c, num, lang)
                       for c in chapters.values())
        if in_markup:
            where = "table"
        else:
            # Attribute the *nearest* floating object — a foreign
            # `<table>` can't label a legend `detached` when the
            # legend's own `table_*.png` sits closer.
            tbl_d = min((_legend_near(c, num, "<table", lang)
                         for c in chapters.values()), default=float("inf"))
            img_d = min((_legend_near(c, num, "images/table_", lang)
                         for c in chapters.values()), default=float("inf"))
            if img_d != float("inf") and img_d <= tbl_d:
                # the intended rotated-table path: legend stays in
                # flow, cells live inside a rendered PNG — coverage
                # can't assess
                where = "rasterized"
                coverage = None
            elif tbl_d != float("inf"):
                # grid markup sits beside the legend but didn't absorb
                # it as a caption — linkage failed, not flattening
                where = "detached"
            elif notes_anchored and not in_chaps:
                where = "notes"
            elif in_chaps:
                where = "prose"
            elif num in raster_legends:
                # the legend block itself sat inside the rotated
                # region — rasterized along with the cells, only the
                # PNG carries it
                where = "rasterized"
                coverage = None
            elif in_notes and not in_chaps:
                where = "notes"
            else:
                where = "missing"
        out.append((num, where, coverage))
    return out


def main() -> int:
    """Run checks A–I and emit the severity-ranked report. Returns the
    CI contract: 0 clean, 1 when High findings exist, 2 for operational
    failures — this function's early returns (unreadable inputs,
    non-zip EPUB, unopenable PDF) or the `__main__` catch-all for
    anything else (zip/XML/pymupdf internals). Checks that can't run
    report High rather than crashing."""
    ap = argparse.ArgumentParser(description="Evaluate a PDF->EPUB conversion.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("epub", type=Path)
    ap.add_argument("--lang", default=None, metavar="CODE",
                    help="language profile to grade against (en/de/fr/es); "
                         "overrides the EPUB's dc:language")
    args = ap.parse_args()
    if not args.pdf.exists() or not args.epub.exists():
        print("error: input file not found", file=sys.stderr)
        return 2

    import fitz  # pymupdf — same dependency as the pipeline

    findings: list[tuple[str, str, str]] = []  # (severity, check, detail)

    # ---- A. container ----
    try:
        members = epub_members(args.epub)
    except zipfile.BadZipFile:
        print("FATAL: epub is not a zip archive", file=sys.stderr)
        return 2
    names = {Path(m).name for m in members}
    required = {"mimetype", "container.xml", "content.opf", "nav.xhtml", "toc.ncx"}
    absent = required - names
    chapters = {m: read_member(args.epub, m) for m in members
                if m.endswith(".xhtml") and "chap_" in Path(m).name}
    notes = next((read_member(args.epub, m) for m in members if m.endswith("notes.xhtml")), "")
    opf = next((read_member(args.epub, m) for m in members if m.endswith("content.opf")), "")
    nav = next((read_member(args.epub, m) for m in members if m.endswith("nav.xhtml")), "")
    if absent:
        findings.append(("High", "A", f"missing required members: {sorted(absent)}"))
    xml_bad = []
    for m in members:
        if m.endswith((".xhtml", ".opf", ".ncx", ".xml")):
            try:
                ET.fromstring(read_member(args.epub, m))
            except ET.ParseError as e:
                xml_bad.append(f"{Path(m).name}: {e}")
    if xml_bad:
        findings.append(("High", "A", f"XML parse failures: {xml_bad[:3]}"))
    # The mimetype member is a magic string, not just a name — a
    # truncated or wrong payload fails every reader the same way.
    if "mimetype" in names and read_member(
            args.epub, "mimetype").strip() != "application/epub+zip":
        findings.append(("High", "A", "mimetype payload is not application/epub+zip"))
    manifest_ids = set(re.findall(r'<item[^>]*id="([^"]+)"', opf))
    spine_refs = set(re.findall(r'<itemref[^>]*idref="([^"]+)"', opf))
    if not spine_refs <= manifest_ids:
        findings.append(("High", "A", f"spine refs not in manifest: {spine_refs - manifest_ids}"))

    # ---- B. metadata ----
    meta = opf_metadata(opf)
    # Language-bound patterns (legend labels, furniture vocabulary, the
    # notes/nav title literals) resolve through the pipeline's own
    # LangProfile so a `de` conversion grades `Tabelle`/`Abb.` legends
    # instead of silently skipping checks F/G. `--lang` wins, then
    # dc:language, then the en default; a code `get_lang` can't resolve
    # — or a hand-built profile that never declared its caption-label
    # vocabulary — is ungradeable, so bail as operational rather than
    # assert a verdict against English patterns. Findings collected so
    # far still print — container checks are language-agnostic. Missing
    # `dc:language` assumes en, same as before.
    lang_code = (args.lang or meta.get("language") or "en")
    lang_code = lang_code.split("-")[0].strip().lower()
    try:
        lang = get_lang(lang_code)
    except ValueError as e:
        for sev, chk, detail in findings:
            print(f"| {sev} | {chk} | {detail.replace('|', '/')} |")
        print(f"NOTE: {e} — eval can't grade")
        return 2
    if lang.table_labels is None or lang.figure_labels is None:
        for sev, chk, detail in findings:
            print(f"| {sev} | {chk} | {detail.replace('|', '/')} |")
        print(f"NOTE: profile {lang.code!r} declares no caption labels — "
              "eval can't grade")
        return 2
    if looks_like_junk_title(meta["title"]):
        findings.append(("High", "B", f"dc:title looks like junk: {meta['title']!r}"))
    creator = meta["creator"]
    # Contamination is about content, not length — a 10-author byline
    # clears 150 chars legitimately. Flag an affiliation tail
    # (`Smith J, Department of X`) or the title itself absorbed into the
    # byline (>=3 of its long words present — needs several so a surname
    # colliding with one title word can't fake it).
    title_words = {w for w in WORD_RE.findall(meta["title"].lower()) if len(w) > 3}
    absorbed = len(title_words & set(WORD_RE.findall(creator.lower()))) >= 3
    # A caption-shaped creator or one without a single letter (`1, 2`,
    # `* †`) names nobody at all.
    nameless = bool(creator) and not re.search(r"[^\W\d_]", creator)
    if (AFFIL_NAME_RE.search(creator) or AFFIL_WORD_RE.search(creator)
            or absorbed or nameless
            or _creator_caption_re(lang).search(creator)):
        findings.append(("High", "B", f"dc:creator contaminated (len={len(creator)}): {creator[:80]!r}…"))

    # ---- C. coverage ----
    try:
        pdf = fitz.open(str(args.pdf))
    except (RuntimeError, ValueError) as e:  # fitz.FileDataError et al.
        print(f"FATAL: cannot open pdf: {e}", file=sys.stderr)
        return 2
    # Pages join with \n so a legend/heading opening a page's text sits
    # at a line boundary — a space join would defeat the line-start
    # anchors used for legend collection below.
    pdf_text = "\n".join(p.get_text("text") for p in pdf)
    boilerplate = boilerplate_signatures(pdf)
    ctxs = [_page_ctx(p, boilerplate, lang) for p in pdf]
    raster_vocab, raster_legends, fig_raster_legends, n_regions, \
        raster_claimed = rasterized_blocks(ctxs, boilerplate, lang)
    # Table and figure legends collect on block-joined text: each
    # extracted block flattens to one line, so `\n` anchors mean block
    # starts — a real caption opens its own block, while a sentence
    # that merely wraps at `Table 5.`/`Figure 3` (or a cite-only
    # `Table S3.`) stays mid-line and can't fake a disposition row.
    # `_dropped` blocks are excluded: furniture and margin-strip text
    # was never expected in the EPUB — a rotated caption beside a
    # landscape table mustn't open a disposition row.
    kept_text = [v[1]
                 for p, ctx in zip(pdf, ctxs)
                 for b in p.get_text("blocks")
                 if b[6] == 0 and (v := _kept(b, ctx, boilerplate, lang))]
    pdf_blocks_text = "\n".join(
        _flatten(t)
        for t in kept_text)
    # `missing_sections` scans real text lines, not flattened blocks:
    # the pipeline classifies headings per *line*, so an OCR-merged
    # block's heading line must keep its boundary — flattening would
    # glue the following sentence into the 'title' (`2. Methods We
    # recruited participants…`) and every discriminator would miss it.
    # Region-claimed blocks mask out like `win_text` — a numbered cell
    # line inside a rotated region can only ever emit as pixels and
    # mustn't fake a missing heading.
    pdf_lines_text = "\n".join(
        v[1]
        for pi, p in enumerate(pdf) for bi, b in enumerate(p.get_text("blocks"))
        if b[6] == 0 and (v := _kept(b, ctxs[pi], boilerplate, lang))
        and (pi, bi) not in raster_claimed)
    # Coverage windows additionally mask region-claimed cell blocks:
    # rasterized text can only survive as pixels, so its grams could
    # never match and would drag a neighboring upright table's window
    # under the 0.6 flag on a correct conversion.
    win_text = "\n".join(
        _flatten(v[1])
        for pi, p in enumerate(pdf) for bi, b in enumerate(p.get_text("blocks"))
        if b[6] == 0 and (v := _kept(b, ctxs[pi], boilerplate, lang))
        and (pi, bi) not in raster_claimed)
    epub_text = (" ".join(xhtml_text(c) for c in chapters.values())
                 + " " + notes_text(notes, lang))
    pw, ew = Counter(WORD_RE.findall(pdf_text.lower())), Counter(WORD_RE.findall(epub_text.lower()))
    if not pw:
        findings.append(("High", "C", ("no extractable text in PDF — scanned/"
                                      "image-only input, eval is blind")))
    furn_vocab = furniture_words(ctxs, boilerplate, lang)
    # The rotated-table path drops its text blocks in every mode and
    # emits only an `images/table_*.png`, so those words were never
    # expected in the text. They leave the expected side *before* the
    # diff: Counter subtraction nets a word's losses against its gains,
    # so a rasterized `resilience` would cancel a body `resil-ience`
    # merge and hide the rejoin evidence. Furniture stays a post-diff
    # excuse — `--keep-headers` emits it, and subtracting kept heads
    # would invent extras. The excuse needs evidence: one table raster
    # *reference* per mirrored region — `state.images` dedupes PNGs by
    # pixmap sha1, so two identical regions share one member and a
    # member count would withhold the excuse on a correct conversion —
    # else a missing raster means the table is genuinely gone and its
    # words (and legends) count as loss.
    n_table_refs = sum(len(re.findall(r'<img\b[^>]*src="[^"]*images/table_', c))
                       for c in chapters.values())
    if n_table_refs < n_regions:
        findings.append(("Med", "C", (f"rotated-table regions: {n_regions} in PDF vs "
                                      f"{n_table_refs} table rasters referenced in "
                                      "EPUB — rasterized-cell excuse withheld")))
        raster_vocab, raster_legends = Counter(), frozenset()
        fig_raster_legends = frozenset()
    expected = pw - raster_vocab
    missing, extra = expected - ew, ew - expected
    rejoined, furn, supers, genuine = classify_missing(
        missing, extra, furn_vocab, epub_text, pdf_text)
    # The denominator is words that *could* survive — rasterized cells
    # and stripped furniture never can — and the numerator counts
    # surviving occurrences of that same population (per-word
    # min(survivable, emitted)); measuring verbatim on `expected`
    # would let a furniture-word's body occurrences push coverage
    # past 100%.
    survivable = expected - furn_vocab
    verbatim = sum((survivable - (survivable - ew)).values())
    coverage_pct = 100 * verbatim / max(sum(survivable.values()), 1)
    n_genuine = sum(genuine.values())
    if n_genuine > sum(pw.values()) * 0.01:
        findings.append(("High", "C", (f"genuine word loss {n_genuine} "
                                    f"(>1%): {genuine.most_common(10)}")))
    elif n_genuine >= 10:
        findings.append(("Med", "C", f"genuine word loss {n_genuine}: {genuine.most_common(10)}"))
    elif n_genuine:
        findings.append(("Low", "C", f"genuine word loss {n_genuine}: {genuine.most_common(10)}"))

    # ---- D. TOC / headings ----
    entries = nav_entries(nav)
    dup = [t for t, n in Counter(entries).items() if n > 1]
    if dup:
        findings.append(("Low", "D", f"duplicate TOC titles: {dup}"))
    hdr = header_like_titles(entries)
    if hdr:
        findings.append(("High", "D", (f"table header rows promoted to TOC headings: "
                                      f"{hdr[:3]}{'…' if len(hdr) > 3 else ''}")))
    # Candidates scan `_dropped`-filtered per-line text — a numbered
    # running head or rotated-sidebar line was never expected in the
    # EPUB and can't be "missing" from its TOC; per-line granularity
    # keeps a heading fused into an OCR-merged paragraph block from
    # adopting the sentence after it.
    missing_secs = missing_sections(
        pdf_lines_text, entries, notes, lang,
        has_headings=any(re.search(r"<h[12]\b", c)
                         for c in chapters.values()))
    shown_secs = [f"{n}. {t}" for n, t in missing_secs]
    if len(missing_secs) >= 2:
        findings.append(("High", "D", (f"numbered section headings missing from TOC "
                                      f"({len(missing_secs)}): {shown_secs[:5]}{'…' if len(shown_secs) > 5 else ''}")))
    elif missing_secs:
        findings.append(("Low", "D", f"numbered section heading missing from TOC: {shown_secs}"))
    all_chap = "".join(chapters.values())
    n_h1 = len(re.findall(r"<h1[\s>]", all_chap))
    n_h2 = len(re.findall(r"<h2[\s>]", all_chap))
    if n_h1 == 0 and n_h2 > 3:
        findings.append(("Med", "D", "no <h1> emitted — title never promoted, flat TOC"))

    # ---- E. mid-paragraph splices ----
    splice_re = _splice_re(lang)
    for name, raw in sorted(chapters.items()):
        for m in splice_re.finditer(raw):
            frag = xhtml_text(m.group(1))[-70:].strip()
            findings.append(("Med", "E", f"{Path(name).name}: legend spliced mid-sentence: …{frag}"))

    # ---- F. tables ----
    for num, where, cov in table_dispositions(pdf_blocks_text, win_text,
                                              chapters, notes,
                                              furn_vocab, lang,
                                              raster_legends):
        if where == "missing":
            # A dropped legend on an intact grid scores high coverage —
            # the data survived, so 'legend+data absent' would misstate
            # the defect; an unassessed window (cov None) proves
            # nothing either way and can't carry a High.
            if cov is not None and cov >= 0.6:
                findings.append(("Med", "F", f"Table {num}: legend absent; grid data survives ({cov:.0%} coverage)"))
            elif cov is None:
                findings.append(("Med", "F", f"Table {num}: legend absent; content window unassessable — data presence unknown"))
            else:
                findings.append(("High", "F", f"Table {num}: legend+data absent from EPUB"))
        elif where == "detached":
            findings.append(("Low", "F", f"Table {num}: legend detached from grid markup (caption linkage failed)"))
        elif where == "rasterized":
            findings.append(("Low", "F", f"Table {num}: rasterized to image (cells not text-checkable)"))
        elif where == "notes":
            cov_s = "n/a" if cov is None else f"{cov:.0%}"
            findings.append(("High", "F", f"Table {num}: swept into notes.xhtml (coverage {cov_s})"))
        elif where == "prose":
            findings.append(("Med", "F", f"Table {num}: flattened to prose, no grid markup"))
        if where != "missing" and cov is not None and cov < 0.6:
            findings.append(("High", "F", f"Table {num}: content largely absent ({cov:.0%} 4-gram coverage)"))

    # ---- G. figures ----
    # The pipeline embeds one `images/img_N.ext` per unique *rendered*
    # image — dict type-1 blocks ≥2048B, sha1-deduped — so count the
    # same population: get_images() lists /Resources instead, missing
    # XObject-nested draws and counting never-rendered entries.
    raster_digests = set()
    for p in pdf:
        for b in p.get_text("dict")["blocks"]:
            if b["type"] == 1:
                data = b.get("image") or b""
                if len(data) >= 2048:
                    raster_digests.add(hashlib.sha1(data).digest())
    pdf_rasters = len(raster_digests)
    # Figure embeds only — `images/table_<digest>.png` members are
    # pipeline-rendered tables, not source rasters; counting them here
    # would let a dropped figure hide behind a rasterized grid.
    epub_imgs = [m for m in members
                 if Path(m).parent.name == "images"
                 and Path(m).name.startswith("img_")]
    vec_pages = [i for i, p in enumerate(pdf) if len(p.get_drawings()) > 30]
    # Same citation-vs-legend split as the table side: collect on the
    # block-flattened text and require legend position in the raw xhtml
    # — an in-text 'as shown in Figure 3' can't satisfy a dropped
    # caption.
    for num in sorted({n.upper()
                       for n in _fig_legend_re(lang).findall(pdf_blocks_text)},
                      key=_num_key):
        fig_re = (rf"{_LEGEND_AT}(?:{lang.figure_labels})\b"
                  rf"\s*\.?\s*{_num_pat(num)}{CAP_RANGE}{_CAP_TAIL_XML}")
        if not re.search(fig_re, all_chap, re.IGNORECASE):
            if re.search(fig_re, notes, re.IGNORECASE):
                # present but mislocated — a bottom-zone caption block
                # swept by `_footnote_tops`, the same displacement the
                # table side reports as the 'notes' disposition
                findings.append(("High", "G",
                                 f"Fig. {num}: legend swept into notes.xhtml"))
            elif num in fig_raster_legends:
                # the caption block sat inside a rotated-table region —
                # rasterized along with the cells, only the PNG carries
                # it (same disposition the table side reports)
                findings.append(("Low", "G", (f"Fig. {num}: legend rasterized "
                                              "with rotated-table region")))
            else:
                findings.append(("Med", "G",
                                 f"Fig. {num}: legend absent from EPUB"))
    # >30 vector ops usually means a ruled table grid, not a figure — a
    # `Table N.` legend decides it, and a digit-heavy page is a table
    # continuation; the two have different remedies (grid cells still
    # emit as flat text vs nothing emitted).
    def _is_grid_page(i: int) -> bool:
        """A >30-drawing page is a ruled grid, not a figure, when a
        `Table N.` legend sits on it or digits dominate its text —
        grid cells still emit as flat text (so they belong in C's
        diff), while a real figure page emits nothing."""
        # Legend search on the same `_dropped`-filtered, block-flattened
        # stream F collects from: raw page text would let a sentence
        # that merely wraps at `Table 5.` misclassify a figure page as
        # a ruled grid.
        t = "\n".join(_flatten(v[1])
                      for b in pdf[i].get_text("blocks")
                      if b[6] == 0 and (v := _kept(b, ctxs[i],
                                                 boilerplate, lang)))
        if _table_legend_re(lang).search(t):
            return True
        toks = WORD_RE.findall(t)
        return bool(toks) and sum(x.isdigit() for x in toks) / len(toks) > 0.5
    grid_pages = [i for i in vec_pages if _is_grid_page(i)]
    fig_vec = [i for i in vec_pages if i not in grid_pages]
    if grid_pages:
        findings.append(("Low", "G", (f"vector-ruled table grids on pages "
                                      f"{[i + 1 for i in grid_pages]} — "
                                      "cells emit as flat text, structure lost")))
    if fig_vec:
        findings.append(("Low", "G", (f"vector graphics on pages "
                                      f"{[i + 1 for i in fig_vec]} not capturable — "
                                      "verify content survived as text")))
    if pdf_rasters > len(epub_imgs):
        findings.append(("Med", "G", f"raster images: {pdf_rasters} in PDF vs {len(epub_imgs)} embedded"))
    for name, raw in chapters.items():
        for img in re.findall(r"<img[^>]*>", raw):
            # The pipeline emits alt="" for uncaptioned figures and the
            # `table_alt` fallback ("table") for uncaptioned grids —
            # those are the meaningful misses; a short real caption
            # (alt="Fig. 1") is fine.
            alt = re.search(r'alt="([^"]*)"', img)
            if not alt or alt.group(1) in ("", lang.table_alt):
                findings.append(("Low", "G", f"{Path(name).name}: <img> missing caption alt text"))

    # ---- H. notes ----
    if notes and _legend_in_notes(notes, lang):
        findings.append(("Med", "H", "notes.xhtml contains table legends — notes zone swallowing grid content"))
    # Unguarded: links pointing at #fnN with no notes.xhtml (or no
    # matching anchor) are dead by construction — that's the defect.
    fn_ids = set(re.findall(r'id="fn(\d+)"', notes))
    fn_links = set(re.findall(r'href="[^"]*#fn(\d+)"', all_chap + notes))
    if fn_links - fn_ids:
        findings.append(("Med", "H", f"dead footnote links: {sorted(fn_links - fn_ids)}"))

    # ---- I. artifacts ----
    fffd = epub_text.count("\ufffd")
    if fffd:
        kind = "source-faithful" if "\ufffd" in pdf_text or "\x04" in pdf_text else "converter-introduced"
        sev = "OK" if kind == "source-faithful" else "Med"
        findings.append((sev, "I", f"{fffd} U+FFFD replacement chars ({kind})"))

    # ---- report ----
    rank = {"High": 0, "Med": 1, "Low": 2, "OK": 3}
    findings.sort(key=lambda f: rank[f[0]])
    n_high = sum(1 for f in findings if f[0] == "High")
    print(f"# epub-eval: {args.pdf.name}\n")
    print(f"**Verdict:** {n_high} high-severity findings — "
          f"{'needs attention' if n_high else 'clean'}; "
          f"verbatim word coverage {coverage_pct:.1f}% ({verbatim}/{sum(survivable.values())}, "
          f"epub emitted {sum(ew.values())} words after rejoins)\n")
    print("## Checks")
    print(f"- A container: {'PASS' if not absent and not xml_bad and spine_refs <= manifest_ids else 'FAIL'} — "
          f"{len(members)} members, {len(chapters)} chapters, {len(epub_imgs)} images")
    print(f"- B metadata: title={meta['title'][:60]!r}")
    print(f"- C coverage: rejoined {sum(rejoined.values())}, furniture {sum(furn.values())}, "
          f"rasterized table cells {sum(raster_vocab.values())}, "
          f"superscript splits {sum(supers.values())}, genuine loss {n_genuine}")
    print(f"- D toc: {len(entries)} entries, h1={n_h1} h2={n_h2}, dupes={dup or 'none'}")
    print(f"- G figures: pdf rasters={pdf_rasters}, embedded={len(epub_imgs)}, "
          f"vector pages={[i + 1 for i in vec_pages]}\n")
    print("## Findings (severity-ranked)")
    print("| Sev | Check | Finding |")
    print("|-----|-------|---------|")
    for sev, chk, detail in findings:
        print(f"| {sev} | {chk} | {detail.replace('|', '/')} |")
    if not findings:
        print("| OK | — | no findings |")
    print("\n## Classified word loss")
    print("| class | words | top examples |")
    print("|-------|-------|--------------|")
    for label, c in (("rejoined hyphenation", rejoined), ("stripped furniture", furn),
                     ("rasterized table cells", raster_vocab),
                     ("superscript splits", supers), ("genuine loss", genuine)):
        ex = ", ".join(w for w, _ in c.most_common(8))
        print(f"| {label} | {sum(c.values())} | {ex} |")
    return min(n_high, 1)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:  # noqa: BLE001 — any operational failure is exit 2
        # Mid-run operational failures (missing fitz, corrupt PDF/zip
        # members) must not exit 1 — that code means "High findings",
        # not "the eval itself broke".
        print(f"FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
