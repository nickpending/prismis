#!/usr/bin/env bash
# Proves the gate's Swift unit by running the real `.specify/verify.sh` in throwaway repos that hold a
# copy of the real macos/ package:
#   1. on macOS, VERIFY_COVERED names swift format(macos), swift build(macos) and swift test(macos), none
#      of them fails, and the gate passes. On Command Line Tools that swift test passes only with the
#      Testing flags the gate computes, so dropping them turns this case red;
#   2. with `uname -s` answering Linux through a stub on PATH, no swift command runs (a stub swift would
#      leave a marker), VERIFY_COVERED names no swift step, and VERIFY_UNCOVERED names swift(macos).
# Case 1 needs a Mac; on any other host it reports SKIP and only case 2 runs, beside a gate that already
# reports the macos unit uncovered there.
# Each repo also carries the real tui/go.mod, for the scan's `go tool gitleaks`, and stub self-tests, so
# a nested gate does not recurse into this script. Output is matched through a here-string, never
# `printf | grep -q`: under pipefail grep's early exit breaks printf's pipe and a match reads as a miss.
# macOS bash 3.2 safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

GATE_PATH="$(go env GOROOT)/bin:/usr/bin:/bin"
FAIL=0

fail() {
	echo "FAIL: $1"
	printf '%s\n' "$2" | sed 's/^/  | /'
	FAIL=1
}
ok() { echo "OK: $1"; }

# mk_tree DIR — a repo with the real gate, the real macos/ package, a Go unit at tui/ holding the
# real tool directives, and stub self-tests.
mk_tree() {
	local t="$1" f
	mkdir -p "$t/.specify/tests" "$t/tui"
	git init -q "$t"
	cp "$ROOT/.specify/verify.sh" "$t/.specify/verify.sh"
	cp "$ROOT/.gitleaks.toml" "$t/.gitleaks.toml"
	# The real ignore list keeps the nested gate's secret scan off the .build/ its swift steps write.
	cp "$ROOT/.gitignore" "$t/.gitignore"
	cp "$ROOT/tui/go.mod" "$ROOT/tui/go.sum" "$t/tui/"
	printf 'package main\n\nfunc main() {}\n' >"$t/tui/main.go"
	for s in gitleaks_plant_test gate_wiring_test swift_unit_test; do
		printf '#!/usr/bin/env bash\nexit 0\n' >"$t/.specify/tests/$s.sh"
	done
	# What git would track under macos/, so SwiftPM's .build output never comes along.
	while IFS= read -r -d '' f; do
		mkdir -p "$t/$(dirname "$f")"
		cp "$ROOT/$f" "$t/$f"
	done < <(git -C "$ROOT" ls-files -z --cached --others --exclude-standard macos)
}

run_gate() { # run_gate DIR PATH
	env PATH="$2" bash "$1/.specify/verify.sh" 2>&1 || true
}

# Case 1: on macOS the three swift steps run and pass.
if [ "$(uname -s)" = Darwin ]; then
	T1="$WORK/mac"
	mk_tree "$T1"
	OUT="$(run_gate "$T1" "$GATE_PATH")"
	if ! grep -q '^VERIFY_COVERED:.*swift format(macos),swift build(macos),swift test(macos)' <<<"$OUT"; then
		fail "VERIFY_COVERED does not name swift format, build and test for macos" "$OUT"
	elif grep -q '^FAILED: swift' <<<"$OUT"; then
		fail "a swift step failed" "$OUT"
	elif ! grep -q '^verify: PASS' <<<"$OUT"; then
		fail "the gate did not pass with the macos unit" "$OUT"
	else
		ok "on macOS the gate runs swift format, build and test on macos/ and they pass"
	fi
else
	echo "SKIP: the macOS case needs a Mac; this host is $(uname -s)"
fi

# Case 2: a Linux uname. No swift runs, and the unit is named uncovered.
T2="$WORK/linux"
mk_tree "$T2"
STUBS="$WORK/stubs"
mkdir -p "$STUBS"
printf '#!/bin/sh\necho Linux\n' >"$STUBS/uname"
printf '#!/bin/sh\ntouch "%s/swift-ran"\nexit 1\n' "$WORK" >"$STUBS/swift"
chmod +x "$STUBS/uname" "$STUBS/swift"
OUT="$(run_gate "$T2" "$STUBS:$GATE_PATH")"
if [ -e "$WORK/swift-ran" ]; then
	fail "swift ran although uname answered Linux" "$OUT"
elif grep -qE '^VERIFY_COVERED:.*swift (format|build|test)\(' <<<"$OUT"; then
	fail "VERIFY_COVERED names a swift step under Linux" "$OUT"
elif ! grep -q '^VERIFY_UNCOVERED:.*swift(macos): macOS only' <<<"$OUT"; then
	fail "VERIFY_UNCOVERED does not name the macos unit under Linux" "$OUT"
else
	ok "under a Linux uname no swift runs and VERIFY_UNCOVERED names swift(macos)"
fi

if [ "$FAIL" -ne 0 ]; then
	echo "swift_unit_test: FAIL"
	exit 1
fi
echo "swift_unit_test: PASS"
