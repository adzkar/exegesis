#!/usr/bin/env bash
# Cut a GitHub Release for exegesis.
#
# Guards the release invariants a manual checklist would carry — clean
# tree on master, master synced with origin, gh authenticated, and the
# version argument matching exegesis/__init__.py's __version__ — then
# runs the lint/test gates, tags vX.Y.Z, pushes it, and creates the
# release with generated notes.
#
# Usage: scripts/release.sh X.Y.Z
set -euo pipefail
cd "$(dirname "$0")/.."

die() { echo "release: $*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: scripts/release.sh <version>"
ver="${1#v}"

[ "$(git rev-parse --abbrev-ref HEAD)" = "master" ] \
    || die "must be on master"
git diff --quiet && git diff --cached --quiet || die "working tree not clean"

git fetch origin master --quiet
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] \
    || die "master is not in sync with origin/master — push first"

gh auth status >/dev/null 2>&1 \
    || die "gh not authenticated — run gh auth login"

pkg_ver="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' exegesis/__init__.py)"
[ -n "$pkg_ver" ] || die "no __version__ found in exegesis/__init__.py"
[ "$ver" = "$pkg_ver" ] \
    || die "argument '$ver' != __version__ '$pkg_ver' — bump it first"

git rev-parse -q --verify "refs/tags/v$ver" >/dev/null \
    && die "tag v$ver already exists"

echo "release: gating v$ver"
ruff check .
python3 -m pytest -m "not slow"

git tag -a "v$ver" -m "exegesis v$ver"
git push origin "v$ver"
gh release create "v$ver" --title "v$ver" --generate-notes
echo "release: v$ver published"
