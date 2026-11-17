# Changelog

All notable changes to exegesis are documented here. Versions follow
[Semantic Versioning](https://semver.org); per-release notes are
generated on each GitHub Release (see `RELEASE.md`).

## [0.3.0] — 2026-11-17

Single-block-page sources (book scans): a scanned book page fused into
one PyMuPDF block no longer emits furniture, heads, notes, and outlines
as run-in prose. On the Trivers motivating scan the pipeline recovers
28 of 28 section heads, files its footnote tail to notes.xhtml, strips
the recurring running heads, and emits Table 7.1 as a real nested list.

- Running heads: folio-anchored line-level split inside page-dominant
  blocks — page edge + page-index quorum + lowercase veto; same-row
  siblings ride along; `--keep-headers` keeps them verbatim (#72)
- Section heads: body-size isolated-head units recover on geometry —
  gaps above (and below for Title Case), all-caps, or profile label —
  with byline/front-matter/needle vetoes (#74)
- Heading runs: same-style adjacent heading lines merge into one
  heading — wrapped titles and chapter heads stay whole (#70)
- Note tails: a whole-line underscore rule + smaller-size tail inside
  the page block splits into a `rule_note` block and files to
  notes.xhtml unnumbered — no invented callout links (#75)
- Rule-framed outlines: a rule pair bounding a marker-grammar
  (`I.`/`A.`/`(n)`) interior emits a nested `<ul>` — depth by
  marker-class first appearance (#86)
- Evaluator: independent running-head leak oracle (severity-ranked,
  `--keep-headers` aware) confirms converter recovery rather than
  re-deriving it (#73)
- Metadata: a trailing `.pdf` extension strips from PDF-derived and
  CLI titles (#71)
- Docs: AGENTS.md, dev/pipeline.md, and the conversion report cover
  the new arms; the Trivers scan joins the report corpus

## [0.2.1] — 2026-11-10

- test: re-seat the root corpus on the journals/ survey set — 18 samples, goldens regenerated

## [0.2.0] — 2026-11-10

Layout-robustness milestone: 17 issue-driven fixes from the journal-corpus
survey, plus the epub-eval tooling used to measure them.

### Reading order & columns

- Per-page/zone column grids for mixed column counts — the committed doc grid
  is now only the prior; pages recover their own geometry (#37)
- Marginalia bands classify into `<aside>` regions (#36); front-matter info
  boxes lift through the same path (#41)
- Outdented section heads fold into their reading column (#43); fused running
  heads split off title blocks (#40); direction-aware margin strips and a
  bounded signature cap (#34)
- Banded sections emit band-major across column seams — references and
  front matter no longer split around a later column's section (#61, #60)
- Fragment-dominated x0 clusters fold out of the reading map — math-paper
  equation/table fragments no longer seed phantom columns (#62); wrap strips
  beside in-column images can't vote phantom edges either (#57)
- Synthetic layout baselines + committed diagnostics (#33)

### Classification & emit

- Front matter kept out of the heading gates — spaced labels and author
  lines no longer promote to h1/h2 (#59)
- Spans painted near-white on a light ground drop at extraction (#63)
- The full XML 1.0 Char complement is stripped at extraction and title/author
  rebind through the same sanitize — control characters can't crash EPUB
  emit (#56, #58)
- Figure regions rasterize instead of emitting labels as prose (#35);
  kept-furniture blocks never seed table regions

### Tooling

- `scripts/eval_epub.py` — severity-ranked conversion quality report with
  `--lang en|de|fr|es` profiles, golden-corpus tests, localized legends
- `dev/diagnose_layouts.py` — corpus survey probes: per-page grid sources,
  fold shares, wrap claims, band seams

## [0.1.2] — 2026-11-04

- Pages: publish only the report — dev docs move to dev/

## [0.1.1] — 2026-11-03

- Docs: releasing.md — corpus unconditional, real die strings
- Review: bump.sh — reject non-forward versions
- Docs: convention covers the python-env pre-flight too
- Review: bump.sh — pre-flight python deps beside the corpus guard
- Docs: scripts guard every precondition before mutating
- Review: bump.sh — pre-flight *.pdf corpus, sync msg covers push
- Docs: releasing.md — full walkthrough of the two-script release flow
- Release: scripts/bump.sh automates release prep
- Report: regenerate conversion report
- Review: error-row parity — wrap pdf/title cells, Sec label from _COLUMNS
- Docs: branch conventions — feat/fix PRs, direct master for small tweaks, worktree caveat for the gitignored corpus
- Report: responsive table — viewport meta, scroll wrap, card layout

## [0.1.0] — 2026-11-03

First tagged release: multi-column academic PDF → reflowable EPUB with
column-aware reading order, nested heading TOC, linked footnotes,
furniture stripping, upright grid and rotated tables, and caption-driven
figure/table alt text.
