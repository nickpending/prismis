#!/usr/bin/env bash
# shellcheck disable=SC2015,SC2016  # `ok` never fails, so `test && ok || fail` is this file's idiom; the single-quoted $(...) are scripts it writes
# Proves `make release VERSION=X.Y.Z` with real git, make and uv in throwaway clones of a
# local bare remote (no network, no mocks):
#   - a malformed version, a dirty tree, a branch other than main, and an existing tag
#     (local or on origin) each exit non-zero and leave files, HEAD, tags and the remote
#     exactly as they were, without reaching the push;
#   - a valid version sets both pyproject versions, relocks both units, commits, makes an
#     annotated tag and pushes main and the tag in one push, so the clone's pre-push hook
#     runs exactly once;
#   - a pre-push hook that fails refuses the push: origin holds neither the release commit
#     nor the tag, the clone holds both, and the output names the undo line;
#   - a push origin rejects, whole or only for the tag, exits non-zero and leaves origin
#     untouched;
#   - the real release_notes.py writes the notes and they are handed to `gh release create`
#     for the new tag, and a failing publish exits non-zero after the push.
# Each clone's pre-push hook stands in for the gate ember's hook runs: it appends one line to
# the clone's gate log in WORK and exits 1 when GATE_FAIL is set. `gh`, the one external
# service a release reaches, is a stub first on PATH that records its calls; the repos carry
# two dependency-free units so `uv lock` runs for real. macOS bash 3.2 safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
FAIL=0

fail() {
	echo "FAIL: $1"
	[ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/  | /'
	FAIL=1
}
ok() { echo "OK: $1"; }

# The gh stub logs "<clone> <args>" to GH_LOG, copies the --notes-file it was handed to
# GH_NOTES_DIR/<clone>.md, and exits 1 when GH_FAIL is set.
mkdir -p "$WORK/bin" "$WORK/published"
cat >"$WORK/bin/gh" <<'STUB'
#!/bin/sh
printf '%s %s\n' "$(basename "$PWD")" "$*" >>"$GH_LOG"
[ -n "${GH_FAIL:-}" ] && exit 1
while [ $# -gt 0 ]; do
  [ "$1" = "--notes-file" ] && cp "$2" "$GH_NOTES_DIR/$(basename "$PWD").md"
  shift
done
exit 0
STUB
chmod +x "$WORK/bin/gh"
export PATH="$WORK/bin:$PATH" GH_LOG="$WORK/gh.log" GH_NOTES_DIR="$WORK/published"

export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null

# mk_remote NAME — a bare remote seeded with main holding the real Makefile and release
# script and two units at version 0.1.0 with committed locks.
mk_remote() {
	local bare="$WORK/$1.git" seed="$WORK/$1-seed"
	git init -q --bare -b main "$bare"
	git init -q -b main "$seed"
	mkdir -p "$seed/scripts" "$seed/daemon" "$seed/cli" "$seed/packaging/homebrew"
	cp "$ROOT/Makefile" "$seed/Makefile"
	cp "$ROOT/scripts/release.sh" "$seed/scripts/release.sh"
	cp "$ROOT/packaging/homebrew/release_notes.py" "$seed/packaging/homebrew/release_notes.py"
	chmod +x "$seed/scripts/release.sh"
	for u in daemon cli; do
		printf '3.13\n' >"$seed/$u/.python-version"
		printf '[project]\nname = "fake-%s"\nversion = "0.1.0"\nrequires-python = ">=3.13"\ndependencies = []\n' "$u" >"$seed/$u/pyproject.toml"
		(cd "$seed/$u" && uv lock -q)
	done
	git -C "$seed" add -A
	git -C "$seed" commit -q -m seed
	git -C "$seed" remote add origin "$bare"
	git -C "$seed" push -q origin main
}

# mk_clone REMOTE NAME — a clean clone on main whose pre-push hook appends one line to
# WORK/NAME.gate.log per push and refuses the push when GATE_FAIL is set.
mk_clone() {
	git clone -q "$WORK/$1.git" "$WORK/$2"
	printf '#!/bin/sh\necho "pre-push $1" >>"%s"\n[ -z "${GATE_FAIL:-}" ]\n' "$WORK/$2.gate.log" >"$WORK/$2/.git/hooks/pre-push"
	chmod +x "$WORK/$2/.git/hooks/pre-push"
}

# gate_runs NAME — how many pushes the clone's pre-push hook saw.
gate_runs() {
	if [ -e "$WORK/$1.gate.log" ]; then wc -l <"$WORK/$1.gate.log" | tr -d ' '; else echo 0; fi
}

# state DIR — everything a refused release must leave alone.
state() {
	git -C "$1" rev-parse HEAD
	git -C "$1" rev-parse --abbrev-ref HEAD
	git -C "$1" tag -l
	git -C "$1" status --porcelain
	git -C "$1" ls-remote origin
	cat "$1/daemon/pyproject.toml" "$1/cli/pyproject.toml" "$1/daemon/uv.lock" "$1/cli/uv.lock"
}

# refused DESC NAME VERSION — release must exit non-zero, change nothing and never push.
refused() {
	local desc="$1" dir="$WORK/$2" version="$3" before after out rc=0
	before="$(state "$dir")"
	out="$(cd "$dir" && make release VERSION="$version" 2>&1)" || rc=$?
	after="$(state "$dir")"
	if [ "$rc" -eq 0 ]; then
		fail "$desc: exited zero" "$out"
	elif [ "$before" != "$after" ]; then
		fail "$desc: changed the clone or the remote" "$out"
	elif [ "$(gate_runs "$2")" != 0 ]; then
		fail "$desc: reached the push" "$out"
	else ok "$desc"; fi
}

mk_remote r1

mk_clone r1 malformed
refused "a malformed version is refused" malformed "1.2"
refused "an empty version is refused" malformed ""
refused "a v-prefixed version is refused" malformed "v1.2.3"

mk_clone r1 dirty
printf 'x\n' >"$WORK/dirty/untracked.txt"
refused "a dirty tree is refused" dirty "1.2.3"

mk_clone r1 offmain
git -C "$WORK/offmain" checkout -q -b topic
refused "a branch other than main is refused" offmain "1.2.3"

mk_clone r1 localtag
git -C "$WORK/localtag" tag v1.2.3
refused "an existing local tag is refused" localtag "1.2.3"

mk_clone r1 remotetag
git -C "$WORK/r1.git" tag v1.2.4 main
refused "an existing tag on origin is refused" remotetag "1.2.4"

# Valid release, with one change since the seed for the notes to carry.
mk_clone r1 good
printf 'x\n' >"$WORK/good/change.txt"
git -C "$WORK/good" add change.txt
git -C "$WORK/good" commit -q -m "api: make it work"
out="$(cd "$WORK/good" && make release VERSION=1.2.3 2>&1)" ||
	{ fail "a valid release exited non-zero" "$out"; }
G="$WORK/good"
if grep -qx 'version = "1.2.3"' "$G/daemon/pyproject.toml" && grep -qx 'version = "1.2.3"' "$G/cli/pyproject.toml"; then
	ok "both pyproject versions are set"
else fail "pyproject versions not set" "$out"; fi
if grep -q 'version = "1.2.3"' "$G/daemon/uv.lock" && grep -q 'version = "1.2.3"' "$G/cli/uv.lock"; then
	ok "both units are relocked"
else fail "locks not relocked" "$out"; fi
[ "$(gate_runs good)" = 1 ] && ok "main and the tag went out in one push through the pre-push hook" ||
	fail "the pre-push hook ran $(gate_runs good) times, not once" "$out"
[ "$(git -C "$G" cat-file -t v1.2.3 2>/dev/null)" = "tag" ] && ok "v1.2.3 is an annotated tag" || fail "v1.2.3 is not an annotated tag"
[ "$(git -C "$G" log -1 --format=%s)" = "Release v1.2.3" ] && ok "the release commit is the tip" || fail "tip is not the release commit"
[ -z "$(git -C "$G" status --porcelain)" ] && ok "the tree is clean after the release" || fail "tree dirty after release"
[ "$(git -C "$WORK/r1.git" rev-parse main)" = "$(git -C "$G" rev-parse HEAD)" ] &&
	ok "origin main holds the release commit" || fail "origin main does not hold the release commit"
[ "$(git -C "$WORK/r1.git" rev-parse 'v1.2.3^{commit}' 2>/dev/null)" = "$(git -C "$G" rev-parse HEAD)" ] &&
	ok "origin holds v1.2.3 at the release commit" || fail "origin does not hold v1.2.3"
grep -qE '^good release create v1\.2\.3 --title v1\.2\.3 --notes-file \S+ --verify-tag$' "$GH_LOG" &&
	ok "the notes are published as the v1.2.3 release" || fail "gh release create was not called for v1.2.3" "$(cat "$GH_LOG" 2>/dev/null)"
if grep -q '^- api: make it work ' "$WORK/published/good.md" && ! grep -q 'Release v1.2.3' "$WORK/published/good.md"; then
	ok "the published notes list the change and not the release commit"
else fail "the published notes are wrong" "$(cat "$WORK/published/good.md" 2>/dev/null)"; fi

# A failing pre-push hook refuses the push: the release stays local and the output says how
# to undo it.
mk_remote r2
mk_clone r2 badgate
BEFORE_REMOTE="$(git -C "$WORK/r2.git" for-each-ref)"
rc=0
out="$(cd "$WORK/badgate" && GATE_FAIL=1 make release VERSION=1.2.3 2>&1)" || rc=$?
B="$WORK/badgate"
if [ "$rc" -ne 0 ] && [ "$(gate_runs badgate)" = 1 ] && [ "$(git -C "$WORK/r2.git" for-each-ref)" = "$BEFORE_REMOTE" ] &&
	[ -z "$(git -C "$WORK/r2.git" tag -l)" ]; then
	ok "a failing pre-push hook refuses the push and origin holds neither the commit nor the tag"
else fail "a failing pre-push hook did not stop the push" "$out"; fi
if [ "$(git -C "$B" log -1 --format=%s)" = "Release v1.2.3" ] && [ "$(git -C "$B" cat-file -t v1.2.3 2>/dev/null)" = "tag" ]; then
	ok "the refused release keeps its commit and tag in the clone"
else fail "the refused release lost its commit or tag" "$out"; fi
if grep -qF 'git tag -d v1.2.3' <<<"$out" && grep -qF 'git reset --hard HEAD~1' <<<"$out"; then
	ok "the refused push names the undo line"
else fail "the refused push does not name the undo line" "$out"; fi

# A push origin rejects outright exits non-zero and pushes nothing.
mk_remote r3
mk_clone r3 rejected
printf '#!/bin/sh\nexit 1\n' >"$WORK/r3.git/hooks/pre-receive"
chmod +x "$WORK/r3.git/hooks/pre-receive"
BEFORE_REMOTE="$(git -C "$WORK/r3.git" for-each-ref)"
rc=0
out="$(cd "$WORK/rejected" && make release VERSION=1.2.3 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && [ "$(git -C "$WORK/r3.git" for-each-ref)" = "$BEFORE_REMOTE" ]; then
	ok "a rejected push exits non-zero and pushes neither main nor the tag"
else fail "rejected push not caught" "$out"; fi

# A push origin rejects only for the tag lands nothing either: main and the tag go together.
mk_remote r4
mk_clone r4 tagrejected
printf '#!/bin/sh\ncase "$1" in refs/tags/*) exit 1 ;; esac\n' >"$WORK/r4.git/hooks/update"
chmod +x "$WORK/r4.git/hooks/update"
BEFORE_REMOTE="$(git -C "$WORK/r4.git" for-each-ref)"
rc=0
out="$(cd "$WORK/tagrejected" && make release VERSION=1.2.3 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && [ "$(git -C "$WORK/r4.git" for-each-ref)" = "$BEFORE_REMOTE" ]; then
	ok "a rejected tag exits non-zero and main does not reach origin without it"
else fail "a rejected tag left origin changed or exited zero" "$out"; fi

# A failing publish exits non-zero after main and the tag are pushed, and says how to retry.
mk_remote r5
mk_clone r5 ghfail
rc=0
out="$(cd "$WORK/ghfail" && GH_FAIL=1 make release VERSION=1.2.3 2>&1)" || rc=$?
if [ "$rc" -ne 0 ] && [ "$(git -C "$WORK/r5.git" rev-parse 'v1.2.3^{commit}' 2>/dev/null)" = "$(git -C "$WORK/ghfail" rev-parse HEAD)" ] &&
	grep -q 'publishing the release notes failed; main and v1.2.3 are pushed (retry: gh release create' <<<"$out"; then
	ok "a failing publish exits non-zero after the push and names the retry"
else fail "failing publish not caught" "$out"; fi

[ "$FAIL" -eq 0 ] || {
	echo "make release test: FAIL"
	exit 1
}
echo "make release test: PASS"
