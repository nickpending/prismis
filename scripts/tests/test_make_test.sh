#!/usr/bin/env bash
# shellcheck disable=SC2015  # `ok` never fails, so `test && ok || fail` is this file's idiom
# Proves `make test` and macos/scripts/test.sh with real uv, ruff, mypy, pytest, go and swift
# in a throwaway tree holding the real Makefile and a copy of the real macos/ package beside
# small real units:
#   - a clean run exits 0 having run every unit's checks, the menu-bar app's through test.sh on
#     Darwin, where every @Test in macos/Tests expands and runs (the count swift test reports
#     matches `grep -rc '@Test'` over them);
#   - with an unused import planted in cli and a failing test in packaging/homebrew, one run
#     exits non-zero and names exactly lint(cli) and tests(packaging/homebrew);
#   - `make test HOST_OS=Linux` reports macos skipped, runs every other unit's checks, never
#     runs swift (a stub swift first on PATH leaves a marker when run, and does on Darwin), and
#     exits 0;
#   - test.sh's flag derivation gives a developer directory named CommandLineTools exactly the
#     framework, rpath and plugin-path flags, and one named like Xcode's none, over the same
#     Testing layout.
# Whether swift test fails without the flags is not asserted: the toolchain reuses earlier
# builds of the same sources on the machine, so a flagless run passes or fails by what was
# built before it.
# The Python units copy packaging/homebrew's manifest and lock, so `uv run --locked` installs
# the tools that unit already pins; tui copies the real tui/go.mod and go.sum, for the
# staticcheck tool directive. The Darwin-only cases report SKIP on any other host. macOS bash
# 3.2 safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
T="$WORK/tree"
FAIL=0
DARWIN=""
[ "$(uname -s)" = Darwin ] && DARWIN=1

fail() {
	echo "FAIL: $1"
	[ -n "${2:-}" ] && printf '%s\n' "$2" | sed 's/^/  | /'
	FAIL=1
}
ok() { echo "OK: $1"; }

# The tree: the real Makefile, three small Python units, a small Go unit, and a copy of the
# real macos/ package with its scripts/test.sh.
mkdir -p "$T/tui"
cp "$ROOT/Makefile" "$T/Makefile"
for u in daemon cli packaging/homebrew; do
	mkdir -p "$T/$u/tests"
	cp "$ROOT/packaging/homebrew/pyproject.toml" "$ROOT/packaging/homebrew/uv.lock" \
		"$ROOT/packaging/homebrew/.python-version" "$T/$u/"
	printf 'def double(value: int) -> int:\n    return value * 2\n' >"$T/$u/probe.py"
	printf 'from probe import double\n\n\ndef test_double() -> None:\n    assert double(2) == 4\n' >"$T/$u/tests/test_probe.py"
done
cp "$ROOT/tui/go.mod" "$ROOT/tui/go.sum" "$T/tui/"
printf 'package main\n\nfunc main() {}\n' >"$T/tui/main.go"
# What git would track under macos/, so SwiftPM's .build output never comes along.
while IFS= read -r -d '' f; do
	mkdir -p "$T/$(dirname "$f")"
	cp "$ROOT/$f" "$T/$f"
done < <(git -C "$ROOT" ls-files -z --cached --others --exclude-standard macos)
TESTS="$(grep -rc '@Test' "$T/macos/Tests" | awk -F: '{ n += $2 } END { print n }')"

# A swift stub, first on PATH only where a case puts it there, that records each call.
mkdir -p "$WORK/stub"
printf '#!/bin/sh\necho "$*" >>"%s"\nexit 0\n' "$WORK/swift-ran" >"$WORK/stub/swift"
chmod +x "$WORK/stub/swift"

# run_make OUT [ARGS...] — make test in the tree; prints its exit status.
run_make() {
	local out="$1" rc=0
	shift
	make -C "$T" test "$@" >"$out" 2>&1 </dev/null || rc=$?
	echo "$rc"
}

CHECKS='lint(daemon) format(daemon) typecheck(daemon) tests(daemon) lint(cli) format(cli) typecheck(cli) tests(cli) lint(packaging/homebrew) format(packaging/homebrew) typecheck(packaging/homebrew) tests(packaging/homebrew) format(tui) vet(tui) staticcheck(tui) tests(tui)'

# ran_all OUT — every non-swift check printed its header.
ran_all() {
	local c
	for c in $CHECKS; do grep -qxF -- "--- $c" "$1" || return 1; done
}

# 1. A clean run passes, and on Darwin runs every @Test in macos/Tests through test.sh.
rc="$(run_make "$WORK/clean.out")"
if [ "$rc" = 0 ] && ran_all "$WORK/clean.out" && grep -qx 'make test: PASS' "$WORK/clean.out"; then
	ok "a clean run runs every unit's checks and exits 0"
else fail "the clean run exited $rc or skipped a check" "$(cat "$WORK/clean.out")"; fi
if [ -n "$DARWIN" ]; then
	if grep -qxF -- '--- swift(macos)' "$WORK/clean.out" && grep -qx 'macos test: PASS' "$WORK/clean.out" &&
		grep -q "Test run with $TESTS tests" "$WORK/clean.out"; then
		ok "on Darwin test.sh runs swift format, build and test, and all $TESTS @Test run"
	else fail "on Darwin test.sh did not pass with all $TESTS @Test run" "$(cat "$WORK/clean.out")"; fi
else
	echo "SKIP: the Darwin clean run needs a Mac; this host is $(uname -s)"
fi

# 2. Two failures planted at once are both named by one run.
printf 'import os\n' >"$T/cli/planted.py"
printf 'def test_planted() -> None:\n    assert 1 == 2\n' >"$T/packaging/homebrew/tests/test_planted.py"
rc="$(run_make "$WORK/planted.out")"
failed="$(grep '^make test: FAILED ' "$WORK/planted.out" || true)"
if [ "$rc" != 0 ] && [ "$failed" = "$(printf 'make test: FAILED lint(cli)\nmake test: FAILED tests(packaging/homebrew)')" ]; then
	ok "one run names both planted failures, lint(cli) and tests(packaging/homebrew), and exits non-zero"
else fail "the planted run exited $rc naming: ${failed:-nothing}" "$(cat "$WORK/planted.out")"; fi
rm -f "$T/cli/planted.py" "$T/packaging/homebrew/tests/test_planted.py"

# 3. A Linux host skips macos, runs everything else, never runs swift, and passes.
rm -f "$WORK/swift-ran"
rc="$(PATH="$WORK/stub:$PATH" run_make "$WORK/linux.out" HOST_OS=Linux)"
if [ "$rc" = 0 ] && ran_all "$WORK/linux.out" && [ ! -e "$WORK/swift-ran" ] &&
	grep -qx -- '--- macos skipped: its swift checks need macOS, and this host is Linux' "$WORK/linux.out" &&
	grep -qx 'make test: PASS' "$WORK/linux.out"; then
	ok "HOST_OS=Linux reports macos skipped, runs every other check, runs no swift, and exits 0"
else fail "HOST_OS=Linux exited $rc, ran swift ($(cat "$WORK/swift-ran" 2>/dev/null)) or skipped a check" "$(cat "$WORK/linux.out")"; fi
if [ -n "$DARWIN" ]; then
	rm -f "$WORK/swift-ran"
	rc="$(PATH="$WORK/stub:$PATH" run_make "$WORK/darwin-stub.out" HOST_OS=Darwin PY_UNITS=)"
	[ -s "$WORK/swift-ran" ] && ok "the swift stub records a call when HOST_OS is Darwin, so its absence above is evidence" ||
		fail "the swift stub saw no call with HOST_OS=Darwin" "$(cat "$WORK/darwin-stub.out")"
fi

# 4. test.sh's flag derivation: two developer directories with the same Testing layout, one
# named like an Xcode developer directory and one named CommandLineTools.
if [ -n "$DARWIN" ]; then
	for dev in "$WORK/dev/Xcode.app/Contents/Developer" "$WORK/dev/CommandLineTools"; do
		mkdir -p "$dev/Library/Developer/Frameworks/Testing.framework" "$dev/usr/lib/swift/host/plugins/testing"
	done
	xcode_flags="$(DEVELOPER_DIR="$WORK/dev/Xcode.app/Contents/Developer" "$T/macos/scripts/test.sh" --print-test-flags)"
	[ -z "$xcode_flags" ] && ok "an Xcode developer directory gets no swift test flags" ||
		fail "an Xcode developer directory got flags" "$xcode_flags"
	clt="$WORK/dev/CommandLineTools"
	clt_flags="$(DEVELOPER_DIR="$clt" "$T/macos/scripts/test.sh" --print-test-flags | tr '\n' ' ')"
	want="-Xswiftc -F$clt/Library/Developer/Frameworks -Xlinker -rpath -Xlinker $clt/Library/Developer/Frameworks -Xlinker -rpath -Xlinker $clt/Library/Developer/usr/lib -Xswiftc -plugin-path -Xswiftc $clt/usr/lib/swift/host/plugins/testing "
	[ "$clt_flags" = "$want" ] && ok "a CommandLineTools developer directory gets the framework, rpath and plugin-path flags" ||
		fail "a CommandLineTools developer directory got the wrong flags" "$clt_flags"
else
	echo "SKIP: the flag derivation needs xcode-select; this host is $(uname -s)"
fi

[ "$FAIL" -eq 0 ] || {
	echo "make test test: FAIL"
	exit 1
}
echo "make test test: PASS"
