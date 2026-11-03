#!/usr/bin/env bash
# Prepare a release: bump __version__, seed a CHANGELOG entry from the
# commits since the last tag, regenerate docs/report/, and commit the
# prep — unpushed, so the changelog can be reviewed and amended before
# scripts/release.sh ships it.
#
# Usage: scripts/bump.sh X.Y.Z
set -euo pipefail
cd "$(dirname "$0")/.."

die() { echo "bump: $*" >&2; exit 1; }

[ $# -eq 1 ] || die "usage: scripts/bump.sh <version>"
ver="${1#v}"
[[ "$ver" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "version must look like X.Y.Z"

[ "$(git rev-parse --abbrev-ref HEAD)" = "master" ] \
    || die "must be on master"
[ -z "$(git status --porcelain)" ] || die "working tree not clean"

git fetch origin master --quiet
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/master)" ] \
    || die "master is not in sync with origin/master — pull or push first"

pkg_ver="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' exegesis/__init__.py)"
[ -n "$pkg_ver" ] || die "no __version__ found in exegesis/__init__.py"
[ "$ver" != "$pkg_ver" ] || die "already at $ver"

# reject a downgrade too — a smaller arg would seed its entry above the
# current release and could ship as 'latest' (sort -t. -k is the
# portable form; BSD sort has no -V)
newest="$(printf '%s\n%s\n' "$pkg_ver" "$ver" \
    | sort -t. -k1,1n -k2,2n -k3,3n | tail -n1)"
[ "$newest" = "$ver" ] \
    || die "version $ver is not newer than $pkg_ver"

# build_report.py needs the root *.pdf corpus — gitignored, so absent
# from worktrees and fresh clones; fail here rather than mid-prep
shopt -s nullglob
pdfs=(*.pdf)
[ ${#pdfs[@]} -gt 0 ] \
    || die "no *.pdf corpus — docs/report cannot be regenerated"

# same for the python env — a worktree can carry the corpus but lack a
# `pip3 install -r requirements.txt`; `import exegesis` pulls the whole
# pymupdf/ebooklib chain build_report.py will need
python3 -c 'import exegesis' >/dev/null 2>&1 \
    || die "python deps missing — pip3 install -r requirements.txt"

# seed changelog bullets from the commits since the last tag — merge
# commits excluded so the list reads as changes, not integration noise
prev="$(git describe --tags --abbrev=0 2>/dev/null || true)"
bullets="$(git log --no-merges --format='- %s' "${prev:+$prev..}HEAD")"
[ -n "$bullets" ] || bullets="- TODO: describe changes"

# sed -i.bak is the portable in-place form across GNU and BSD sed
sed -i.bak "s/^__version__ = \".*\"$/__version__ = \"$ver\"/" \
    exegesis/__init__.py
rm exegesis/__init__.py.bak

# insert the new entry above the first existing '## [' section — file
# handoff keeps awk free of the bullets' quoting
entry_file="$(mktemp)"
{
    printf '## [%s] — %s\n\n' "$ver" "$(date +%F)"
    printf '%s\n\n' "$bullets"
} > "$entry_file"
awk -v f="$entry_file" '
    /^## \[/ && !done {
        while ((getline l < f) > 0) print l
        close(f); done=1
    }
    { print }
    END {
        if (!done) { while ((getline l < f) > 0) print l; close(f) }
    }
' CHANGELOG.md > CHANGELOG.md.new
mv CHANGELOG.md.new CHANGELOG.md
rm "$entry_file"

python3 scripts/build_report.py

git add exegesis/__init__.py CHANGELOG.md docs/report/
git commit -m "Release $ver"

cat <<MSG
bump: prepared $ver (was $pkg_ver${prev:+, since $prev})
Next: review CHANGELOG.md — amend the prep commit if needed
  git add CHANGELOG.md && git commit --amend
Then: git push && scripts/release.sh $ver
MSG
