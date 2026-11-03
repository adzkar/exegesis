# Release process

exegesis publishes GitHub Releases tagged `vX.Y.Z`, cut locally — there
is no CI. Two scripts split the work, with a human checkpoint between
them:

- `scripts/bump.sh X.Y.Z` — **prep**: version bump, CHANGELOG entry,
  report regeneration, prep commit (left unpushed)
- `scripts/release.sh X.Y.Z` — **ship**: guards + lint/test gates, tag,
  push, GitHub Release with generated notes

## Prerequisites

- On `master`, clean tree (`git status` empty), synced with
  `origin/master` (`git pull --ff-only`)
- `gh` CLI authenticated: `gh auth login`
- Tooling installed: `pip3 install -r requirements.txt -r requirements-dev.txt`
- Corpus PDFs at the repo root (`bump.sh` always runs `build_report.py`,
  which needs `*.pdf` locally — absent in fresh clones/worktrees)

## Walkthrough

### 1. Pick a version

SemVer against the last tag:

```bash
git describe --tags --abbrev=0      # last release, e.g. v0.1.0
git log v0.1.0..HEAD --oneline      # what ships in the new one
```

- fixes/docs → bump the patch (`0.1.0` → `0.1.1`)
- backward-compatible features → bump the minor (`0.1.0` → `0.2.0`)
- breaking changes → bump the major (`0.x.0` → `1.0.0`)

### 2. Prep

```bash
./scripts/bump.sh 0.2.0
```

This validates the argument (semver shape, newer than
`__version__`), rewrites `exegesis/__init__.py`, inserts a dated
`## [0.2.0]` section into `CHANGELOG.md` seeded with the non-merge
commit subjects since the last tag, regenerates `docs/report/` for the
Pages site, and commits it all as `Release 0.2.0` — locally only.

### 3. Review + push

Read the seeded `CHANGELOG.md` — rewrite any bullets that describe the
change poorly, then fold your edit into the prep commit:

```bash
git add CHANGELOG.md && git commit --amend
git push
```

### 4. Ship

```bash
./scripts/release.sh 0.2.0
```

The script re-verifies every invariant (branch, clean tree, origin
sync, `gh` auth, argument == `__version__`, `## [X.Y.Z]` present in
`CHANGELOG.md`, tag unused), runs `ruff check` and the non-slow pytest
suite, then tags `v0.2.0`, pushes it, and creates the GitHub Release
with generated notes.

### 5. Post-release

- `gh release view v0.2.0` — verify; `gh release edit v0.2.0` to polish
  the generated notes
- Confirm the report redeployed: https://adzkar.github.io/exegesis/report/
  (Pages rebuilds on every push to `master`, ~1–2 min)

## Troubleshooting the guards

| Error | Meaning / fix |
|---|---|
| `must be on master` | `git checkout master` |
| `working tree not clean` | commit or stash the changes first |
| `not in sync with origin/master` | `git pull --ff-only` (behind) or `git push` (ahead) |
| `gh not authenticated` | `gh auth login` |
| `argument != __version__` | run `bump.sh` first, or fix the version arg |
| `CHANGELOG.md has no '## [X.Y.Z]'` | keep the entry `bump.sh` seeded |
| `tag vX.Y.Z already exists` | pick a new version; retagging is manual |
| `already at X.Y.Z` (bump) | the bump is already done — go to step 4 |
| `not newer than` (bump) | the version must be above `__version__` |
| `version must look like X.Y.Z` | pass a semver, e.g. `0.2.0` |
| `no *.pdf corpus — docs/report cannot be regenerated` (bump) | place the corpus PDFs at the repo root |
| `python deps missing` (bump) | `pip3 install -r requirements.txt` |

## Notes

- The CHANGELOG entry is required even though GitHub notes are
  generated — the file is the human-edited record, the release page is
  the machine-generated one.
- Deleting or moving a pushed tag is possible but avoid it — once a
  release is published, cut a new version instead.
- `bump.sh` never pushes, so aborting a prepared release is just
  `git reset --hard HEAD~1` before you `git push`.
