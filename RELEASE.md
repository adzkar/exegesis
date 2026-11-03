# Releasing

Releases are cut locally via `scripts/release.sh` — there is no CI.

## Prerequisites

- On `master`, clean tree, synced with `origin/master`
- `gh` CLI authenticated (`gh auth login`)
- Dev tools installed: `pip3 install -r requirements-dev.txt`

## Steps

1. Bump `__version__` in `exegesis/__init__.py` and update `CHANGELOG.md`.
2. Commit the bump.
3. Run `scripts/release.sh X.Y.Z`.

The script verifies the branch, tree cleanliness, sync with
`origin/master`, `gh` auth, and that the argument matches
`__version__`; runs `ruff check` and the non-slow pytest suite; then
tags `vX.Y.Z`, pushes the tag, and creates the GitHub Release with
generated notes.
