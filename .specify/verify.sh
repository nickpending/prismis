#!/usr/bin/env bash
# Project verification gate. Discovers its own units at run time — never a hardcoded list,
# because a repo grows packages and a fixed list ships the new ones unexecuted.
#
# Contract: runs every unit, collects every failure, reports what it covered, and exits
# non-zero if anything failed OR if nothing ran. macOS bash 3.2 safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

FAIL=0
COVERED=""
UNCOVERED=""
UNITS_FOUND=0

note_covered() { COVERED="${COVERED:+$COVERED,}$1"; }
note_uncovered() { UNCOVERED="${UNCOVERED:+$UNCOVERED; }$1"; }

# Coverage records EXECUTION, not outcome — a check that ran and failed is covered.
# An accumulator filled only on success would make an all-failing run look like broken
# discovery, sending a fixer after the wrong problem.
# Output is captured rather than discarded: a gate that names the failing check but not
# the failing assertion cannot be diagnosed anywhere the failure does not also reproduce,
# which is precisely the CI-vs-laptop case the gate exists to catch.
run_step() {
	local desc="$1"
	shift
	local out
	note_covered "$desc"
	if out="$("$@" 2>&1)"; then
		return 0
	else
		echo "FAILED: $desc"
		printf '%s\n' "$out" | sed 's/^/  | /'
		FAIL=1
		return 1
	fi
}

# ---------------------------------------------------------------- Python units
# Every pyproject.toml outside build/venv dirs is a unit.
# --locked: the gate judges the tree as committed and never rewrites it. Plain `uv run` relocked
# cli/uv.lock and blocked the lane merge (2026-10-01); a stale lock now fails that unit instead.
while IFS= read -r pp; do
	d="$(dirname "$pp")"
	rel="${d#"$ROOT"/}"
	[ "$rel" = "$d" ] && rel="."
	UNITS_FOUND=$((UNITS_FOUND + 1))

	if grep -q '\[tool\.ruff' "$pp" 2>/dev/null; then
		run_step "ruff($rel)" env -C "$d" uv run --locked --quiet ruff check . || true
	else
		note_uncovered "ruff($rel): no [tool.ruff] config"
	fi

	if grep -q '\[tool\.mypy\]' "$pp" 2>/dev/null; then
		run_step "mypy($rel)" env -C "$d" uv run --locked --quiet mypy . || true
	else
		note_uncovered "typecheck($rel): no [tool.mypy] config — type errors are unchecked here"
	fi

	if [ -d "$d/tests" ]; then
		run_step "pytest($rel)" env -C "$d" uv run --locked --quiet pytest -q || true
	else
		note_uncovered "pytest($rel): no tests/ directory"
	fi
	# `.claude/worktrees` is where Claude Code materialises an agent worktree —
	# inside the repo, and `find` does not read `.gitignore`. Without this exclusion a
	# live sibling worktree is adopted as units of the main checkout; it has no
	# node_modules, so the gate fails on code that is fine (workshop#40).
done < <(find "$ROOT" -name pyproject.toml \
	-not -path '*/.venv/*' -not -path '*/node_modules/*' \
	-not -path '*/build/*' -not -path '*/dist/*' \
	-not -path "$ROOT/.claude/worktrees/*" | sort)

# -------------------------------------------------------------------- Go units
while IFS= read -r gm; do
	d="$(dirname "$gm")"
	rel="${d#"$ROOT"/}"
	[ "$rel" = "$d" ] && rel="."
	UNITS_FOUND=$((UNITS_FOUND + 1))

	# shellcheck disable=SC2016  # the $(...) is for the sh -c below, expanded there
	run_step "gofmt($rel)" env -C "$d" sh -c '[ -z "$(gofmt -l .)" ]' || true
	run_step "go vet($rel)" env -C "$d" go vet ./... || true
	# `go tool` runs the staticcheck pinned by the unit's own go.mod tool directive, so
	# no PATH binary is involved; a Go unit without the directive fails here loudly.
	run_step "staticcheck($rel)" env -C "$d" go tool staticcheck ./... || true
	run_step "go test($rel)" env -C "$d" go test ./... || true
done < <(find "$ROOT" -name go.mod -not -path '*/vendor/*' -not -path "$ROOT/.claude/worktrees/*" | sort)

# ------------------------------------------------------------------ Rust units
while IFS= read -r cg; do
	d="$(dirname "$cg")"
	rel="${d#"$ROOT"/}"
	[ "$rel" = "$d" ] && rel="."
	UNITS_FOUND=$((UNITS_FOUND + 1))
	run_step "cargo fmt($rel)" env -C "$d" cargo fmt --check || true
	run_step "clippy($rel)" env -C "$d" cargo clippy -- -D warnings || true
	run_step "cargo test($rel)" env -C "$d" cargo test || true
done < <(find "$ROOT" -name Cargo.toml -not -path '*/target/*' -not -path "$ROOT/.claude/worktrees/*" -maxdepth 3 | sort)

# ----------------------------------------------------------------- Swift units
# The `swift test` flags ember's gate passes (its swiftTestArgs), so both gates agree on this Mac. On
# Command Line Tools (a developer dir named CommandLineTools holding Testing.framework) SwiftPM does not
# find Testing itself: the compiler gets its framework dir, the linker its rpaths, and, where those tools
# hold it, the macro plugin dir every @Test needs to expand. Under Xcode, or without xcode-select, nothing
# is added. A change to where Command Line Tools keeps Testing must reach both copies.
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

# Every Package.swift outside SwiftPM's own build output is a unit. Swift with Testing and the Apple SDKs
# is a macOS toolchain here, so elsewhere the unit is reported uncovered, never passed unrun.
HOST_OS="$(uname -s)"
SWIFT_TEST_FLAGS=()
while IFS= read -r sp; do
	d="$(dirname "$sp")"
	rel="${d#"$ROOT"/}"
	[ "$rel" = "$d" ] && rel="."
	UNITS_FOUND=$((UNITS_FOUND + 1))
	if [ "$HOST_OS" != Darwin ]; then
		note_uncovered "swift($rel): macOS only — swift format, swift build and swift test do not run on $HOST_OS"
		continue
	fi
	if [ "${#SWIFT_TEST_FLAGS[@]}" -eq 0 ]; then
		while IFS= read -r flag; do SWIFT_TEST_FLAGS+=("$flag"); done < <(swift_test_flags)
	fi
	run_step "swift format($rel)" env -C "$d" swift format lint --strict --recursive . || true
	run_step "swift build($rel)" env -C "$d" swift build || true
	# ${a[@]+...}: bash 3.2 treats an empty array as unset under `set -u`.
	run_step "swift test($rel)" env -C "$d" swift test ${SWIFT_TEST_FLAGS[@]+"${SWIFT_TEST_FLAGS[@]}"} || true
done < <(find "$ROOT" -name Package.swift -not -path '*/.build/*' \
	-not -path '*/.venv/*' -not -path '*/node_modules/*' \
	-not -path "$ROOT/.claude/worktrees/*" | sort)

# ------------------------------------------------------------------- JS/TS units
while IFS= read -r pj; do
	d="$(dirname "$pj")"
	rel="${d#"$ROOT"/}"
	[ "$rel" = "$d" ] && rel="."
	UNITS_FOUND=$((UNITS_FOUND + 1))
	grep -q '"typecheck"' "$pj" && { run_step "typecheck($rel)" env -C "$d" bun run typecheck || true; }
	grep -q '"check"' "$pj" && { run_step "check($rel)" env -C "$d" bun run check || true; }
	# -print -quit, not `find | grep -q .`: under pipefail grep's early exit can kill find and
	# read a unit with many test files as one with none.
	if [ -n "$(find "$d" -name '*.test.ts' -not -path '*/node_modules/*' -print -quit)" ]; then
		run_step "bun test($rel)" env -C "$d" bun test || true
	fi
done < <(find "$ROOT" -name package.json -not -path '*/node_modules/*' -not -path "$ROOT/.claude/worktrees/*" | sort)

# ------------------------------------------------------------------- Secret scan
# gitleaks scans only what git would track for committed secrets (gh #70), so this
# local check and CI's full-history scan judge the same files. `gitleaks dir` is a
# raw filesystem walk (no git/.gitignore awareness at all) — pointed at $ROOT
# directly it would also walk .venv (tens of thousands of untracked files) and any
# live sibling `.claude/worktrees/*` checkout, reproducing for secrets the exact
# defect `find` had for unit discovery above (workshop#40). `git ls-files` instead
# lists exactly the tracked-or-would-be-tracked paths, and gitleaks is pointed at a
# throwaway mirror built from that list, so a secret in .venv or a sibling worktree
# is never read at all, while one in a real source file still is.
# The scanner is the gitleaks pinned by a tool directive in tui/go.mod, run through
# `go tool`, so no PATH binary is involved and a missing directive fails the check.
# The module is tui/. Only the plant test, whose throwaway repo has no tui/, redirects
# it through VERIFY_TOOL_MODULE_DIR, and that override is honoured solely while
# VERIFY_IN_PLANT_TEST is set — an ambient value alone cannot redirect the scan.
# Known fakes are allowlisted in .gitleaks.toml.
TOOL_MODULE_DIR="$ROOT/tui"
if [ -n "${VERIFY_IN_PLANT_TEST:-}" ]; then
	TOOL_MODULE_DIR="${VERIFY_TOOL_MODULE_DIR:-$ROOT/tui}"
fi
GITLEAKS_TREE="$(mktemp -d)"
trap 'rm -rf "$GITLEAKS_TREE"' EXIT
while IFS= read -r -d '' f; do
	mkdir -p "$GITLEAKS_TREE/$(dirname "$f")"
	cp "$ROOT/$f" "$GITLEAKS_TREE/$f"
done < <(git -C "$ROOT" ls-files -z --cached --others --exclude-standard)
run_step "gitleaks(.)" env -C "$TOOL_MODULE_DIR" go tool gitleaks dir "$GITLEAKS_TREE" --redact --no-banner --verbose --config "$ROOT/.gitleaks.toml" || true
rm -rf "$GITLEAKS_TREE"
trap - EXIT

# ------------------------------------------------------------ Gate self-tests
# Prove the checks above rather than trust them: the plant test shows the scan catches a
# planted credential, the wiring test shows this script's own failure paths, and the swift
# unit test shows the Swift unit runs with the Command Line Tools flags on macOS and is
# reported uncovered elsewhere. Without these runs all three are one-time manual checks.
# All drive this very script in throwaway repos; the plant test sets VERIFY_IN_PLANT_TEST so
# its nested run does not start the self-tests again. That skip is recorded as uncovered,
# never silent, and outside that nested run a missing script is a failure, never a skip.
if [ -n "${VERIFY_IN_PLANT_TEST:-}" ]; then
	note_uncovered "gitleaks-plant(.), gate-wiring(.), swift-unit(.): not run — nested inside the plant test (VERIFY_IN_PLANT_TEST is set)"
else
	run_step "gitleaks-plant(.)" bash "$ROOT/.specify/tests/gitleaks_plant_test.sh" || true
	run_step "gate-wiring(.)" bash "$ROOT/.specify/tests/gate_wiring_test.sh" || true
	run_step "swift-unit(.)" bash "$ROOT/.specify/tests/swift_unit_test.sh" || true
fi

# ----------------------------------------------------------------- the verdict
# Two different absences, two different answers. A repo with no units AND no source is
# a project not yet written — the sentinel tells the consuming gate not to send a fixer
# after the absence of code. No units WITH source present is broken discovery.
if [ "$UNITS_FOUND" -eq 0 ]; then
	if [ -z "$(find "$ROOT" \( -name '*.py' -o -name '*.go' -o -name '*.ts' -o -name '*.rs' -o -name '*.swift' \) \
		-not -path '*/.venv/*' -not -path '*/node_modules/*' \
		-not -path "$ROOT/.claude/worktrees/*" -print -quit)" ]; then
		echo "VERIFY_NOT_YET: no verifiable unit and no source files — nothing has been written yet"
	else
		echo "VERIFY_UNCOVERED: source files exist but no unit was discovered — discovery is broken"
	fi
	echo "verify: FAIL"
	exit 1
fi

echo "VERIFY_COVERED: ${COVERED:-none}"
[ -n "$UNCOVERED" ] && echo "VERIFY_UNCOVERED: $UNCOVERED"

# Units were found but nothing executed: every check routed to a skip path, so the failure
# accumulator was never touched and the verdict would print PASS having proven nothing.
if [ -z "$COVERED" ]; then
	echo "VERIFY_UNCOVERED: $UNITS_FOUND unit(s) discovered but no check executed"
	echo "verify: FAIL"
	exit 1
fi

if [ "$FAIL" -ne 0 ]; then
	echo "verify: FAIL"
	exit 1
fi

echo "verify: PASS"
