#!/usr/bin/env bash
# Runs the menu-bar app's checks in macos/: `swift format lint --strict --recursive .`,
# `swift build`, then `swift test`. Every check runs; each failure is named, and the script
# exits non-zero when any failed. `make test` runs this on Darwin. macOS only. macOS bash 3.2
# safe.
#
# On the Command Line Tools (xcode-select names a CommandLineTools directory holding
# Testing.framework) SwiftPM does not find Swift Testing by itself: the compiler gets its
# framework directory, the linker its rpaths, and, where those tools hold it, the macro plugin
# directory every @Test needs to expand. Under Xcode, or without xcode-select, swift test runs
# with no flags. These are the flags ember's gate passes to swift test, so the two agree on a
# Command Line Tools Mac; a change to where the Command Line Tools keep Testing must reach both.
#
# `test.sh --print-test-flags` prints the swift test flags this Mac gets, one per line, and runs
# nothing.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

swift_test_flags() {
	local dev frameworks plugins
	dev="$(xcode-select -p 2>/dev/null)" || return 0
	[ "$(basename "$dev")" = CommandLineTools ] || return 0
	frameworks="$dev/Library/Developer/Frameworks"
	[ -d "$frameworks/Testing.framework" ] || return 0
	printf '%s\n' -Xswiftc "-F$frameworks" -Xlinker -rpath -Xlinker "$frameworks" \
		-Xlinker -rpath -Xlinker "$dev/Library/Developer/usr/lib"
	plugins="$dev/usr/lib/swift/host/plugins/testing"
	if [ -d "$plugins" ]; then printf '%s\n' -Xswiftc -plugin-path -Xswiftc "$plugins"; fi
}

if [ "${1:-}" = --print-test-flags ]; then
	swift_test_flags
	exit 0
fi

[ "$(uname -s)" = Darwin ] || {
	echo "macos test: macOS only, this host is $(uname -s)"
	exit 1
}

TEST_FLAGS=()
while IFS= read -r flag; do TEST_FLAGS+=("$flag"); done < <(swift_test_flags)

FAILED=()
check() { # check NAME COMMAND... — runs COMMAND in macos/ and records NAME when it fails
	local name="$1"
	shift
	echo "--- $name"
	(cd "$APP_DIR" && "$@") || FAILED+=("$name")
}

check "swift format lint" swift format lint --strict --recursive .
check "swift build" swift build
# ${a[@]+...}: bash 3.2 treats an empty array as unset under `set -u`.
check "swift test" swift test ${TEST_FLAGS[@]+"${TEST_FLAGS[@]}"}

if [ "${#FAILED[@]}" -gt 0 ]; then
	for name in "${FAILED[@]}"; do echo "macos test: FAILED $name"; done
	exit 1
fi
echo "macos test: PASS"
