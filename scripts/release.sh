#!/usr/bin/env bash
# Cut a release: `make release VERSION=X.Y.Z`.
#
# Refuses (before changing anything) a malformed version, a dirty tree, a branch other than
# main, or an existing tag. Then sets both pyproject versions, relocks daemon and cli,
# commits, tags, writes the release notes, pushes main and the tag in one atomic push, and
# publishes the notes as the tag's GitHub Release. Every step's exit status is checked
# directly, never through a pipe; the first failure stops the release. The tag push is what
# starts .github/workflows/release.yml.
#
# The gate is the pre-push hook ember installs, which runs ember verify over the release
# commit during that one push. This script runs no gate of its own and never skips the
# hook: a refused push leaves origin untouched and the release commit and tag local.
#
# The notes come from packaging/homebrew/release_notes.py (the previous v* tag, or the root
# commit for a first release, up to the new tag), run on the Python uv provides rather than
# whatever python3 the shell finds, and are published with the local `gh`, so the release
# workflow keeps a read-only GITHUB_TOKEN.
set -euo pipefail

VERSION="${1:-}"
cd "$(git rev-parse --show-toplevel)"

die() {
	echo "release: $*" >&2
	exit 1
}

printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' ||
	die "VERSION must look like X.Y.Z, got '${VERSION}'"
TAG="v$VERSION"

[ -z "$(git status --porcelain)" ] || die "the working tree is dirty; commit or stash first"
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
[ "$BRANCH" = "main" ] || die "releases are cut from main, this is '${BRANCH}'"
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
	die "tag $TAG already exists locally"
fi
rc=0
git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1 || rc=$?
case "$rc" in
0) die "tag $TAG already exists on origin" ;;
2) ;;
*) die "could not ask origin whether $TAG exists (git ls-remote exited $rc)" ;;
esac

set_version() { # set_version PYPROJECT — rewrites the first top-level version line
	local f="$1" tmp
	tmp="$(mktemp)"
	awk -v v="$VERSION" '!done && /^version = "/ { print "version = \"" v "\""; done = 1; next } { print }' "$f" >"$tmp" ||
		{
			rm -f "$tmp"
			die "could not rewrite $f"
		}
	cat "$tmp" >"$f"
	rm -f "$tmp"
	grep -qx "version = \"$VERSION\"" "$f" || die "no version line to set in $f"
}

for unit in daemon cli; do
	set_version "$unit/pyproject.toml"
	(cd "$unit" && uv lock) || die "uv lock failed in $unit"
done

PREV="$(git describe --tags --abbrev=0 --match 'v*' HEAD 2>/dev/null)" ||
	PREV="$(git rev-list --max-parents=0 HEAD | tail -n 1)"

git add daemon/pyproject.toml daemon/uv.lock cli/pyproject.toml cli/uv.lock
git commit -m "Release $TAG" || die "git commit failed"
git tag -a "$TAG" -m "Release $TAG" || die "git tag failed"

REPO_SLUG="$(git remote get-url origin | sed -nE 's#^(git@github\.com:|https://github\.com/)([^/]+/[^/]+)$#\2#p')"
REPO_SLUG="${REPO_SLUG%.git}"
NOTES="$(mktemp)"
uv run --quiet --no-project --python 3.13 python packaging/homebrew/release_notes.py "$PREV" "$TAG" ${REPO_SLUG:+--repo "$REPO_SLUG"} >"$NOTES" ||
	die "writing the release notes failed; nothing was pushed (undo with: git tag -d $TAG && git reset --hard HEAD~1)"

# --atomic: origin takes both refs or neither, so a refused push never leaves main released
# without its tag.
git push --atomic origin main "$TAG" ||
	die "the push was refused, nothing reached origin; undo with: git tag -d $TAG && git reset --hard HEAD~1"
gh release create "$TAG" --title "$TAG" --notes-file "$NOTES" --verify-tag ||
	die "publishing the release notes failed; main and $TAG are pushed (retry: gh release create $TAG --title $TAG --notes-file $NOTES --verify-tag)"
rm -f "$NOTES"

echo "release: $TAG pushed and its notes published; .github/workflows/release.yml publishes it to the tap"
