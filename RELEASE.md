# Releasing

Releases are cut locally via `scripts/bump.sh` + `scripts/release.sh` —
there is no CI. Full walkthrough: [dev/releasing.md](dev/releasing.md).

## Prerequisites

- On `master`, clean tree, synced with `origin/master`
- `gh` CLI authenticated (`gh auth login`)
- Runtime deps + dev tools installed:
  `pip3 install -r requirements.txt -r requirements-dev.txt`

## Steps

1. Run `scripts/bump.sh X.Y.Z` — bumps `__version__`, seeds a
   `CHANGELOG.md` entry from the commits since the last tag, regenerates
   `docs/report/`, and commits the prep (unpushed).
2. Review `CHANGELOG.md`; amend the prep commit if needed
   (`git add CHANGELOG.md && git commit --amend`), then `git push`.
3. Run `scripts/release.sh X.Y.Z`.

The script verifies the branch, tree cleanliness, sync with
`origin/master`, `gh` auth, that the argument matches `__version__`,
and that `CHANGELOG.md` has a `## [X.Y.Z]` entry; runs `ruff check` and
the non-slow pytest suite; then tags `vX.Y.Z`, pushes the tag, and
creates the GitHub Release with generated notes.
