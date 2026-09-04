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
run_step() {
  local desc="$1"; shift
  note_covered "$desc"
  if "$@" >/dev/null 2>&1; then
    return 0
  else
    echo "FAILED: $desc"
    FAIL=1
    return 1
  fi
}

# ---------------------------------------------------------------- Python units
# Every pyproject.toml outside build/venv dirs is a unit.
while IFS= read -r pp; do
  d="$(dirname "$pp")"
  rel="${d#"$ROOT"/}"; [ "$rel" = "$d" ] && rel="."
  UNITS_FOUND=$((UNITS_FOUND + 1))

  if grep -q '\[tool\.ruff' "$pp" 2>/dev/null; then
    run_step "ruff($rel)" env -C "$d" uv run --quiet ruff check . || true
  else
    note_uncovered "ruff($rel): no [tool.ruff] config"
  fi

  if grep -q '\[tool\.pyright\]' "$pp" 2>/dev/null; then
    run_step "pyright($rel)" env -C "$d" uv run --quiet pyright || true
  elif grep -q '\[tool\.mypy\]' "$pp" 2>/dev/null; then
    run_step "mypy($rel)" env -C "$d" uv run --quiet mypy . || true
  else
    note_uncovered "typecheck($rel): no pyright/mypy config — type errors are unchecked here"
  fi

  if [ -d "$d/tests" ]; then
    run_step "pytest($rel)" env -C "$d" uv run --quiet pytest -q || true
  else
    note_uncovered "pytest($rel): no tests/ directory"
  fi
done < <(find "$ROOT" -name pyproject.toml \
           -not -path '*/.venv/*' -not -path '*/node_modules/*' \
           -not -path '*/build/*' -not -path '*/dist/*' | sort)

# -------------------------------------------------------------------- Go units
while IFS= read -r gm; do
  d="$(dirname "$gm")"
  rel="${d#"$ROOT"/}"; [ "$rel" = "$d" ] && rel="."
  UNITS_FOUND=$((UNITS_FOUND + 1))

  run_step "gofmt($rel)" env -C "$d" sh -c '[ -z "$(gofmt -l .)" ]' || true
  run_step "go vet($rel)" env -C "$d" go vet ./... || true
  if command -v staticcheck >/dev/null 2>&1; then
    run_step "staticcheck($rel)" env -C "$d" staticcheck ./... || true
  else
    note_uncovered "staticcheck($rel): binary not installed"
  fi
  run_step "go test($rel)" env -C "$d" go test ./... || true
done < <(find "$ROOT" -name go.mod -not -path '*/vendor/*' | sort)

# ------------------------------------------------------------------ Rust units
while IFS= read -r cg; do
  d="$(dirname "$cg")"
  rel="${d#"$ROOT"/}"; [ "$rel" = "$d" ] && rel="."
  UNITS_FOUND=$((UNITS_FOUND + 1))
  run_step "cargo fmt($rel)" env -C "$d" cargo fmt --check || true
  run_step "clippy($rel)" env -C "$d" cargo clippy -- -D warnings || true
  run_step "cargo test($rel)" env -C "$d" cargo test || true
done < <(find "$ROOT" -name Cargo.toml -not -path '*/target/*' -maxdepth 3 | sort)

# ------------------------------------------------------------------- JS/TS units
while IFS= read -r pj; do
  d="$(dirname "$pj")"
  rel="${d#"$ROOT"/}"; [ "$rel" = "$d" ] && rel="."
  UNITS_FOUND=$((UNITS_FOUND + 1))
  grep -q '"typecheck"' "$pj" && { run_step "typecheck($rel)" env -C "$d" bun run typecheck || true; }
  grep -q '"check"'     "$pj" && { run_step "check($rel)"     env -C "$d" bun run check     || true; }
  if find "$d" -name '*.test.ts' -not -path '*/node_modules/*' | grep -q .; then
    run_step "bun test($rel)" env -C "$d" bun test || true
  fi
done < <(find "$ROOT" -name package.json -not -path '*/node_modules/*' | sort)

# ----------------------------------------------------------------- the verdict
# Two different absences, two different answers. A repo with no units AND no source is
# a project not yet written — the sentinel tells the consuming gate not to send a fixer
# after the absence of code. No units WITH source present is broken discovery.
if [ "$UNITS_FOUND" -eq 0 ]; then
  if [ -z "$(find "$ROOT" \( -name '*.py' -o -name '*.go' -o -name '*.ts' -o -name '*.rs' \) \
              -not -path '*/.venv/*' -not -path '*/node_modules/*' -print -quit)" ]; then
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
