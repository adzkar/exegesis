# Changelog

All notable changes to exegesis are documented here. Versions follow
[Semantic Versioning](https://semver.org); per-release notes are
generated on each GitHub Release (see `RELEASE.md`).

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
