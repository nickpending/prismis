#!/usr/bin/env bash
# Cut a release: `make release VERSION=X.Y.Z`.
#
# Refuses (before changing anything) a malformed version, a dirty tree, a branch other than
# main, or an existing tag. Then sets both pyproject versions, relocks daemon and cli, runs
# the verification gate, commits, tags, and pushes main and then the tag. Every step's exit
# status is checked directly, never through a pipe; the first failure stops the release.
# The tag push is what starts .github/workflows/release.yml.
#
# RELEASE_TEST=1 lets scripts/tests/test_make_release.sh replace the gate with
# RELEASE_VERIFY_CMD, since running the real gate inside a throwaway repo proves nothing.
# Without RELEASE_TEST the override is ignored.
set -euo pipefail

VERSION="${1:-}"
cd "$(git rev-parse --show-toplevel)"

die() { echo "release: $*" >&2; exit 1; }

printf '%s' "$VERSION" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+$' \
  || die "VERSION must look like X.Y.Z, got '${VERSION}'"
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
  awk -v v="$VERSION" '!done && /^version = "/ { print "version = \"" v "\""; done = 1; next } { print }' "$f" >"$tmp" \
    || { rm -f "$tmp"; die "could not rewrite $f"; }
  cat "$tmp" >"$f"
  rm -f "$tmp"
  grep -qx "version = \"$VERSION\"" "$f" || die "no version line to set in $f"
}

for unit in daemon cli; do
  set_version "$unit/pyproject.toml"
  (cd "$unit" && uv lock) || die "uv lock failed in $unit"
done

if [ -n "${RELEASE_TEST:-}" ] && [ -n "${RELEASE_VERIFY_CMD:-}" ]; then
  bash -c "$RELEASE_VERIFY_CMD" || die "verification failed; the version bump is left uncommitted in the tree"
else
  ./.specify/verify.sh || die "verification failed; the version bump is left uncommitted in the tree"
fi

git add daemon/pyproject.toml daemon/uv.lock cli/pyproject.toml cli/uv.lock
git commit -m "Release $TAG" || die "git commit failed"
git tag -a "$TAG" -m "Release $TAG" || die "git tag failed"
git push --no-verify origin main || die "pushing main failed; $TAG is tagged locally only"
git push --no-verify origin "$TAG" || die "pushing $TAG failed; main was pushed"

echo "release: $TAG pushed; .github/workflows/release.yml publishes it to the tap"
