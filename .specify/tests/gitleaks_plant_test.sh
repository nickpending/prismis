#!/usr/bin/env bash
# Proves the gate's gitleaks check (SC-2, gh #70) actually catches a real-looking
# credential and clears once it's gone — by driving the real `.specify/verify.sh`
# secret-scan step itself, never a re-typed copy of its mirror-and-scan logic. If
# that step's invocation ever changes (a new flag, a different config path, a
# different `git ls-files` filter), this test picks up the change automatically
# because it runs the file, not a description of it.
#
# It builds a throwaway, standalone git repo holding only a fresh copy of
# .specify/verify.sh and .gitleaks.toml — no pyproject.toml/go.mod/Cargo.toml/
# package.json anywhere in it, so every other unit loop in verify.sh finds
# nothing and stays instant; only the unconditional secret-scan step does real
# work. It plants a freshly generated, unallowlisted credential in that repo,
# runs the unmodified verify.sh against it, and asserts on the gate's own
# `FAILED: gitleaks(.)` line and the planted filename — exactly what `run_step`
# prints when the gate's real gitleaks invocation fails. Then it removes the
# file and asserts that line is gone. macOS bash 3.2 safe.
#
# The credential is generated at runtime and never echoed: verify.sh's own
# gitleaks invocation already runs with --redact, and only its output is printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "FAIL: gitleaks binary not found on PATH — cannot prove the check without it"
  exit 1
fi

GATE_TREE="$(mktemp -d)"
trap 'rm -rf "$GATE_TREE"' EXIT

git init -q "$GATE_TREE"
mkdir -p "$GATE_TREE/.specify"
cp "$ROOT/.specify/verify.sh" "$GATE_TREE/.specify/verify.sh"
cp "$ROOT/.gitleaks.toml" "$GATE_TREE/.gitleaks.toml"

run_gate() {
  bash "$GATE_TREE/.specify/verify.sh" 2>&1 || true
}

PLANTED_REL="src/planted_credential_test_fixture.py"
PLANTED="$GATE_TREE/$PLANTED_REL"
SECRET="sk-live-$(openssl rand -hex 20)"
mkdir -p "$(dirname "$PLANTED")"
printf 'api_key = "%s"\n' "$SECRET" >"$PLANTED"
unset SECRET

FAIL=0

# Phase 1: the planted, unallowlisted credential must make the gate's own
# gitleaks step fail, and the gate's own failure line must name the file — a
# check that fails without saying where is not one anyone can act on.
OUT="$(run_gate)"
if ! printf '%s\n' "$OUT" | grep -qF 'FAILED: gitleaks(.)'; then
  echo "FAIL: the gate did not report FAILED: gitleaks(.) with the planted credential present"
  printf '%s\n' "$OUT" | sed 's/^/  | /'
  FAIL=1
elif ! printf '%s\n' "$OUT" | grep -q "$PLANTED_REL"; then
  echo "FAIL: the gate failed gitleaks but did not name the planted file ($PLANTED_REL)"
  printf '%s\n' "$OUT" | sed 's/^/  | /'
  FAIL=1
else
  echo "OK: the gate's own gitleaks step failed and named the planted file"
fi

# Phase 2: once the file is gone, the same gate run must no longer report that
# failure — proving the failure above was the planted file, not something else.
rm -f "$PLANTED"
OUT2="$(run_gate)"
if printf '%s\n' "$OUT2" | grep -qF 'FAILED: gitleaks(.)'; then
  echo "FAIL: the gate still reported FAILED: gitleaks(.) after the planted file was removed"
  printf '%s\n' "$OUT2" | sed 's/^/  | /'
  FAIL=1
else
  echo "OK: the gate's own gitleaks step passed once the planted file was removed"
fi

if [ "$FAIL" -ne 0 ]; then
  echo "gitleaks_plant_test: FAIL"
  exit 1
fi

echo "gitleaks_plant_test: PASS"
