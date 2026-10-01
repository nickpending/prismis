#!/usr/bin/env bash
# Proves the gate's own wiring by running the real `.specify/verify.sh` in throwaway
# repos, with a PATH that holds Go and the system tools but no staticcheck and no
# gitleaks binary:
#   1. the plant-test check runs, and a missing plant script FAILS the gate;
#   2. with the plant script present and VERIFY_IN_PLANT_TEST unset, the check runs
#      the script (a stub that leaves a marker) and the gate passes with no scanner
#      binary on PATH, even when an ambient VERIFY_TOOL_MODULE_DIR points nowhere;
#   3. with VERIFY_IN_PLANT_TEST set the stub is NOT run and the skip is reported as
#      uncovered, not silent;
#   4. a Go unit whose go.mod has no tool directive fails staticcheck loudly.
# The throwaway repos carry a stub gate-wiring test so a nested gate does not recurse
# into this script. macOS bash 3.2 safe.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

GOBIN_DIR="$(go env GOROOT)/bin"
GATE_PATH="$GOBIN_DIR:/usr/bin:/bin"
FAIL=0

# The proofs below mean nothing if a scanner binary is reachable.
for b in staticcheck gitleaks; do
  if PATH="$GATE_PATH" command -v "$b" >/dev/null 2>&1; then
    echo "FAIL: $b is on the test PATH ($GATE_PATH); the proof would be void"
    exit 1
  fi
done

fail() { echo "FAIL: $1"; printf '%s\n' "$2" | sed 's/^/  | /'; FAIL=1; }
ok() { echo "OK: $1"; }

# mk_tree DIR — a repo with the real gate, one Go unit at tui/ holding the real tool
# directives, and stub self-tests. Plant stub drops a marker file in DIR.
mk_tree() {
  local t="$1"
  mkdir -p "$t/.specify/tests" "$t/tui"
  git init -q "$t"
  cp "$ROOT/.specify/verify.sh" "$t/.specify/verify.sh"
  cp "$ROOT/.gitleaks.toml" "$t/.gitleaks.toml"
  cp "$ROOT/tui/go.mod" "$ROOT/tui/go.sum" "$t/tui/"
  printf 'package main\n\nfunc main() {}\n' >"$t/tui/main.go"
  printf '#!/usr/bin/env bash\ntouch "%s/plant-ran"\n' "$t" >"$t/.specify/tests/gitleaks_plant_test.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' >"$t/.specify/tests/gate_wiring_test.sh"
}

run_gate() { # run_gate DIR [ENV=VAL...]
  local t="$1"; shift
  env PATH="$GATE_PATH" "$@" bash "$t/.specify/verify.sh" 2>&1 || true
}

# Case 1: plant script missing -> the gate fails and names the check.
T1="$WORK/missing"; mk_tree "$T1"; rm "$T1/.specify/tests/gitleaks_plant_test.sh"
OUT="$(run_gate "$T1")"
if printf '%s\n' "$OUT" | grep -qF 'FAILED: gitleaks-plant(.)' \
   && printf '%s\n' "$OUT" | grep -q '^VERIFY_COVERED:.*gitleaks-plant(\.)' \
   && printf '%s\n' "$OUT" | grep -q '^verify: FAIL'; then
  ok "a missing plant script fails the gate and is listed as covered"
else
  fail "missing plant script did not fail the gate as gitleaks-plant(.)" "$OUT"
fi

# Case 2: guard unset -> the plant script runs; gate passes without scanner binaries,
# and an ambient VERIFY_TOOL_MODULE_DIR cannot redirect the scan.
T2="$WORK/present"; mk_tree "$T2"
OUT="$(run_gate "$T2" VERIFY_TOOL_MODULE_DIR=/nonexistent-module-dir)"
if [ ! -e "$T2/plant-ran" ]; then
  fail "the gate did not run the plant script" "$OUT"
elif ! printf '%s\n' "$OUT" | grep -q '^VERIFY_COVERED:.*staticcheck(tui).*gitleaks(\.).*gitleaks-plant(\.)'; then
  fail "VERIFY_COVERED lacks staticcheck(tui), gitleaks(.) or gitleaks-plant(.)" "$OUT"
elif ! printf '%s\n' "$OUT" | grep -q '^verify: PASS'; then
  fail "the gate did not pass with no scanner binary on PATH" "$OUT"
else
  ok "the gate runs the plant script and passes with no staticcheck/gitleaks on PATH"
fi

# Case 3: guard set -> no recursion, and the skip is reported.
T3="$WORK/guarded"; mk_tree "$T3"
OUT="$(run_gate "$T3" VERIFY_IN_PLANT_TEST=1)"
if [ -e "$T3/plant-ran" ]; then
  fail "the plant script ran although VERIFY_IN_PLANT_TEST was set" "$OUT"
elif ! printf '%s\n' "$OUT" | grep -q '^VERIFY_UNCOVERED:.*gitleaks-plant(\.)'; then
  fail "the guarded skip was not reported as uncovered" "$OUT"
else
  ok "the guard skips the plant script and reports it as uncovered"
fi

# Case 4: a Go unit without the tool directive fails staticcheck.
T4="$WORK/notool"; mk_tree "$T4"
mkdir "$T4/other"
printf 'module example.com/other\n\ngo 1.24\n' >"$T4/other/go.mod"
printf 'package main\n\nfunc main() {}\n' >"$T4/other/main.go"
OUT="$(run_gate "$T4")"
if printf '%s\n' "$OUT" | grep -qF 'FAILED: staticcheck(other)' \
   && printf '%s\n' "$OUT" | grep -q '^verify: FAIL'; then
  ok "a Go unit without the tool directive fails staticcheck loudly"
else
  fail "a Go unit without the tool directive did not fail staticcheck(other)" "$OUT"
fi

# Case 5: a Python unit whose uv.lock is stale fails its checks and the lock file is
# left untouched (the gate runs `uv run --locked`; plain `uv run` would relock it).
T5="$WORK/stale"; mk_tree "$T5"
mkdir "$T5/py"
printf '[project]\nname = "x"\nversion = "0.1"\nrequires-python = ">=3.10"\n\n[tool.ruff]\nline-length = 100\n' >"$T5/py/pyproject.toml"
(cd "$T5/py" && uv lock --quiet)
LOCK_BEFORE="$(cat "$T5/py/uv.lock")"
printf 'dependencies = ["six"]\n' >"$WORK/dep.txt"
sed -i.bak '/^requires-python/r '"$WORK/dep.txt" "$T5/py/pyproject.toml"
rm -f "$T5/py/pyproject.toml.bak"
OUT="$(run_gate "$T5" "PATH=$(dirname "$(command -v uv)"):$GATE_PATH")"
if printf '%s\n' "$OUT" | grep -qF 'FAILED: ruff(py)' \
   && printf '%s\n' "$OUT" | grep -q 'needs to be updated' \
   && [ "$LOCK_BEFORE" = "$(cat "$T5/py/uv.lock")" ]; then
  ok "a stale uv.lock fails the Python unit and is not rewritten"
else
  fail "a stale uv.lock did not fail ruff(py) or was rewritten" "$OUT"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "gate_wiring_test: FAIL"
  exit 1
fi
echo "gate_wiring_test: PASS"
