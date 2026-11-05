---
name: epub-eval
description: Evaluate a PDF->EPUB conversion against its source PDF and produce a severity-ranked report (structure, text coverage, TOC, reading order, tables, figures, notes, fidelity)
argument-hint: "<path to PDF> <path to EPUB>"
allowed-tools:
  - exec
  - read
  - glob
permissions:
  allow:
    - Exec(python3 *)
    - Exec(pip3 *)
    - Exec(unzip *)
---

# EPUB Eval

Grade a PDF -> EPUB conversion the same way every time, so quality is
comparable across documents and regressions are visible. Runs
`scripts/eval_epub.py` in this project (root: the directory containing
`.devin/`), then augments its automated checks with a short manual pass.

## Usage

```bash
python3 scripts/eval_epub.py <input.pdf> <output.epub>
```

Prints a markdown report. Exit code is `1` when any High-severity
finding exists (usable as a CI gate), `0` otherwise; `2` marks
operational failures (unreadable inputs).

## What the script checks

- **A. Container** — required members (`mimetype`, `container.xml`,
  `content.opf`, `nav.xhtml`, `toc.ncx`); every `.xhtml`/`.opf`/`.ncx`
  parses as XML; every spine `itemref` resolves to a manifest item.
- **B. Metadata** — `dc:title` flagged when it looks like pagination/ID
  junk (`OP-SCAN150067 1758..1768`: `..`-ranges, ≥5-digit runs, or digit
  dominance over letters) that passes the converter's letter-check;
  `dc:creator` flagged when it absorbs title text or affiliations
  (University/Department/Center…).
- **C. Coverage** — word-multiset diff of PDF vs EPUB text (`Counter`
  subtraction), then *classified* before being counted as loss:
  - *rejoined hyphenation* — fragment is a prefix/suffix of an extra
    EPUB word whose complement is also missing (`mind`+`fulness` →
    `mindfulness`, both halves absent) — a positive, not loss;
  - *stripped furniture* — word appears in running-head signatures
    (block text repeated on ≥40% of pages), the outer 7% edge zones
    (page numbers / pattern furniture, gated on dehyphenated block
    text like `_block_text` so `copy-`/`right` reads `copyright`), or
    margin strips — correct stripping;
  - *superscript splits* — source token `Kral1` emits as `Kral` +
    superscript `1`; a word splitting at a letter↔digit boundary into
    two parts that exist *adjacent* in the EPUB lost nothing (a
    `covid19` hiding behind free-floating halves stays genuine);
  - *genuine loss* — everything else; >1% of source words → High,
    ≥10 words → Med, fewer → Low.
- **D. TOC/headings** — nav entries counted; duplicate titles flagged;
  nav titles shaped like table column headers (`df SE CI`, `M SD
  Min Max` — ≥3 stat tokens → High, a header row promoted to h2 both
  pollutes the TOC and explodes the table into pseudo-chapters);
  numbered source headings (`2. Methods`, `2.1. Participants` — number
  and title must share one extracted line with no trailing period;
  wrapped items ending in lowercase continuations, numbered notes whose
  own number+title line starts a notes.xhtml paragraph (a note merely
  mentioning 'results' doesn't silence `2. Results`), `N±1.`-flanked
  lines (a numbered-list run like `1. Collect`/`2. Wash`), `Surname, I`
  reference tails, and
  everything past the References heading are excluded; a title wrapping
  across two source lines counts as covered when a nav entry continues
  it) absent from the
  TOC (per-entry match on the number-stripped label — a nav entry
  merely containing the title word, `Materials and Methods`, does not
  cover `2. Methods`) → High when ≥2 go missing; zero `<h1>` in a multi-heading doc means
  the title never promoted and the TOC is flat.
- **E. Reading-order splices** — a bold `Table|Fig(ure)|Plate|Scheme|
  Chart N.` legend
  appearing after >25 chars of running text inside the same `<p>` is a
  floating-object caption glued into a sentence (e.g. `over 2 <b>Table
  2.</b>`); a `,`/`;` right after `</b>` reads as a bolded in-text
  citation, not a splice.
- **F. Tables** — every `Table N.` legend in the source (trailing period
  and *block*-start position required — blocks are flattened one-per-line
  so a sentence wrapping at `Table 5.` or a cite-only `Table S3.` can't
  collect — `Table S1.`/`Table 1A.`/`TABLE 2.`/
  `Table 2 (b).`/`Table 3–4.` collect, ranges key on the first number;
  `(Table N)`
  citations and sentence-ending `…in Table 5.` don't) is located in the
  EPUB: inside `<table>` markup / flattened to
  prose / swept into notes.xhtml / missing — plus *detached* (grid
  markup sits beside a legend it failed to absorb as `<caption>`, Low)
  and *rasterized* (legend in flow beside an `images/table_*.png` — the
  intended rotated-table path, cells not text-checkable, Low) — with
  the match required in
  legend position (block-element/line start — inline-tag-adjacent
  citations can't stand in, but `*†‡§` note markers and text-carrying
  inline elements like a numbered note's `<sup>3</sup>` may sit between
  the opener and the legend). Content survival is scored
  with 4-grams over a 55-content-token window after each legend —
  order-sensitive, so scattered digits can't fake coverage; furniture
  words are discounted (correctly stripped, not absent), windows left
  with <8 tokens are skipped, and the worst remaining window is
  reported. <60% → content largely absent.
- **G. Figures** — unique PDF raster count (deduped by content bytes,
  2048-byte floor — a logo repeated per page counts once) vs embedded
  `img_*` images (rasterized `table_*` members excluded); `Fig. N`/
  `Plate`/`Scheme`/`Chart` legends (block-start collected like tables)
  must appear in legend position in the raw xhtml — an in-text
  `as shown in Figure 3` can't satisfy a dropped caption;
  pages with
  >30 vector drawings are split into *vector-ruled table grids* (a
  `Table N.` legend on the page, or a digit-heavy continuation — cells
  still emit as flat text) vs *vector figures* (nothing capturable —
  verify content survived as text); every `<img>` should carry
  caption-derived `alt`.
- **H. Notes** — `notes.xhtml` shouldn't contain `Table N.` legends
  (notes zone swallowing grids); `href="#fnN"` links must resolve to
  `id="fnN"` anchors — checked even when notes.xhtml is absent (links
  without a notes member are dead by construction) and including links
  inside notes.xhtml itself.
- **I. Fidelity** — `U+FFFD` replacement chars are verified against
  the PDF's raw text layer: present in source → source-faithful (OK),
  absent → converter-introduced (Med).

## How the comparison works

1. `fitz` per-page `get_text` → normalized `[A-Za-z0-9]+` word multiset;
   `Counter` diff both directions — the *extra* side exposes rejoined
   hyphenations, the *missing* side gets classified.
2. Artifact attribution — grep the PDF raw text for the same string
   (`Accepted: 2 June 2006`, `\x04` glyph); if it's there, the EPUB is
   faithful, not buggy.
3. Structural greps on raw XHTML — `<table`, `<img`, `id="fn`, legend
   patterns — never on tag-stripped text (markup checks need the tags).
4. Page forensics — `get_drawings()` counts expose vector-only figures;
   block bboxes in edge zones build the furniture vocabulary.

## Interpreting the report

- **Verdict** = count of High findings + verbatim coverage % (words
  surviving unchanged; the rejoined/furniture classes explain the gap
  between that and the emitted word count).
- Swept-to-notes tables keep their data in `notes.xhtml` — the finding
  means *location* is wrong; check the coverage % for whether content
  also survived.
- `coverage ~70–90%` on a table window usually means trailing prose
  after the grid, not loss — only <60% is flagged.
- Always end with "verify against the PDF" recommendations for any High
  finding (e.g., vector figures, orphaned table cells).

## Manual pass (after the script)

The script can't judge semantics — open the first chapter's XHTML and
confirm column reading order on multi-column sources, and eyeball one
table/figure chapter. Report anything the script missed under the same
severity scheme (High/Med/Low/OK).

## Example output

```text
**Verdict:** 4 high-severity findings — needs attention; verbatim word
coverage 95.6% (8421/8813, epub emitted 8635 words after rejoins)
| High | B | dc:title looks like junk: 'OP-SCAN150067 1758..1768' |
| High | F | Table 3: swept into notes.xhtml (coverage 100%) |
| Med  | E | chap_6.xhtml: legend spliced mid-sentence: …over 2  Table 2. |
```
