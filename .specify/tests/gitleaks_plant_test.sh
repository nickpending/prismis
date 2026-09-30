#!/usr/bin/env bash
# Proves the gate's gitleaks check (SC-2, gh #70) actually catches a real-looking
# credential and clears once it's gone — not by a one-time manual run, but by a
# script that plants a freshly generated, unallowlisted credential into a temporary
# copy of the tracked tree, runs the same gitleaks invocation `.specify/verify.sh`
# runs, and asserts it fails and names the planted file; then removes the file and
# asserts the identical invocation passes again. macOS bash 3.2 safe.
#
# The credential is generated at runtime and never echoed: gitleaks itself is asked
# to --redact, and only its (already redacted) output is ever printed.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if ! command -v gitleaks >/dev/null 2>&1; then
  echo "FAIL: gitleaks binary not found on PATH — cannot prove the check without it"
  exit 1
fi

MIRROR="$(mktemp -d)"
trap 'rm -rf "$MIRROR"' EXIT

# Mirror exactly what the gate mirrors — the tracked-or-would-be-tracked tree built
# from `git ls-files`, never a raw filesystem walk — see .specify/verify.sh.
while IFS= read -r -d '' f; do
  mkdir -p "$MIRROR/$(dirname "$f")"
  cp "$ROOT/$f" "$MIRROR/$f"
done < <(git -C "$ROOT" ls-files -z --cached --others --exclude-standard)

scan() {
  gitleaks dir "$MIRROR" --redact --no-banner --verbose --config "$ROOT/.gitleaks.toml"
}

PLANTED_REL="src/planted_credential_test_fixture.py"
PLANTED="$MIRROR/$PLANTED_REL"
SECRET="sk-live-$(openssl rand -hex 20)"
mkdir -p "$(dirname "$PLANTED")"
printf 'api_key = "%s"\n' "$SECRET" >"$PLANTED"
unset SECRET

FAIL=0

# Phase 1: the planted, unallowlisted credential must fail the check and the
# failure must name the file it lives in — a check that fails without saying
# where is not one anyone can act on.
if OUT="$(scan 2>&1)"; then
  echo "FAIL: gitleaks passed with an unallowlisted planted credential present"
  FAIL=1
elif ! printf '%s\n' "$OUT" | grep -q "$PLANTED_REL"; then
  echo "FAIL: gitleaks failed but did not name the planted file ($PLANTED_REL)"
  printf '%s\n' "$OUT" | sed 's/^/  | /'
  FAIL=1
else
  echo "OK: gitleaks failed and named the planted file"
fi

# Phase 2: once the file is gone, the same invocation must pass again — proving
# the failure above was the planted file and not some pre-existing finding.
rm -f "$PLANTED"
if OUT2="$(scan 2>&1)"; then
  echo "OK: gitleaks passed once the planted file was removed"
else
  echo "FAIL: gitleaks still failed after the planted file was removed"
  printf '%s\n' "$OUT2" | sed 's/^/  | /'
  FAIL=1
fi

if [ "$FAIL" -ne 0 ]; then
  echo "gitleaks_plant_test: FAIL"
  exit 1
fi

echo "gitleaks_plant_test: PASS"
