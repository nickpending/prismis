---
type: design
date: 2026-10-29
title: "Design: retire-speckit"
description: "Spec-kit leaves prismis: ember verify, run by the pre-push hook, is the only gate, and everything that still ran, named or deferred to .specify/verify.sh (make test, the release script, CI, CLAUDE.md, README, the architecture docs) says what it actually does instead, with the constitution's four prismis-specific constraints carried into docs/constitution.json where ember reads a project's articles."
purpose: "The recorded design of the retire-speckit work order, read by its plan and its builders."
producer: cli:shape
---

# Design: retire-speckit

## Purpose

Spec-kit leaves prismis: ember verify, run by the pre-push hook, is the only gate, and everything that still ran, named or deferred to .specify/verify.sh (make test, the release script, CI, CLAUDE.md, README, the architecture docs) says what it actually does instead, with the constitution's four prismis-specific constraints carried into docs/constitution.json where ember reads a project's articles.

## Form

1. Delete .specify/ whole (verify.sh, tests/, workflows/, templates/, scripts/, integrations/, memory/, init-options.json, integration.json, .gitignore) and the four Spec Kit lines of .gitignore (lines 223-226). .gitleaks.toml stays; its header comment (line 1) says betterleaks reads it from ember's gate and CI's history scan does too.

2. docs/constitution.json, to ember's shared/constitution/schema.json: four articles, each the rule text of one Project Constraint of .specify/memory/constitution.md (lines 112-132), ids local-first-zero-ops, three-components-one-contract, pipeline-runs-unattended and llm-providers-only-external-boundary. Nothing from Core Principles, Security Requirements, Quality Gates or Governance is copied: those are the bench house-default that ember's universal constitution (harness/constitution.json) carries.

3. Makefile `test` (line 313) stops delegating and runs every unit's own checks itself, as each unit declares them: in daemon, cli and packaging/homebrew `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked mypy .` and `uv run --locked pytest -q`; in tui `test -z "$(gofmt -l .)"`, `go vet ./...`, `go tool staticcheck ./...` and `go test ./...`; and `macos/scripts/test.sh` on Darwin (on any other OS it prints that macos is skipped and still exits 0 when the rest pass). Every check runs and every failure is reported before the target exits non-zero, so one run names everything wrong. Its help line reads "Run every unit's checks (what CI runs as proof; the push gate is ember's pre-push hook)". macos/scripts/test.sh is new: it runs `swift format lint --strict --recursive .`, `swift build` then `swift test` in macos/, with the Command Line Tools flags verify.sh derived in swift_test_flags (lines 110-120: -F and -rpath for Library/Developer/Frameworks and usr/lib, the plugin path when usr/lib/swift/host/plugins/testing exists) when xcode-select names a CommandLineTools directory, and plain `swift test` under Xcode. This is the one copy of that derivation in the repo.

4. .github/workflows/ci.yml: the Verify step (lines 134-163) becomes `make test`, proof for outside eyes (pull requests) and for Linux and macOS; the VERIFY_UNCOVERED post-check goes; the header comment (lines 8-21) says CI is proof on Linux and macOS of the tests, never a gate, since the gate is ember's pre-push hook on the developer's machine and ember is private. Checkout, uv, Go, the embedding-model cache, Playwright Chromium, the dependency install loops and the full-history secret scan on Linux (lines 165-184) stay as they are.

5. scripts/release.sh: the gate step (lines 16-18, 66-69) and the RELEASE_TEST/RELEASE_VERIFY_CMD override go; after the notes are written it runs one `git push origin main "$TAG"` with no --no-verify, so the pre-push hook runs ember verify once over the release commit; a refused push dies with "the push was refused, nothing reached origin; undo with: git tag -d $TAG && git reset --hard HEAD~1". The gh release create step and its retry line stay. The header comment names the hook as the gate.

6. scripts/tests/test_make_release.sh: the stub .specify/verify.sh (lines 57, 61-62) goes; mk_clone installs a pre-push hook in each clone that touches gate-ran (gitignored, replacing real-gate-ran and the two stub markers) and exits 1 when GATE_FAIL is set. The cases become: a valid release pushes main and the tag in one push after the hook ran; with GATE_FAIL set the push is refused, the remote holds neither the commit nor the tag, the clone holds both, and the output names the undo line; the RELEASE_VERIFY_CMD case goes. packaging/homebrew/tests/test_make_release.py keeps running it.

7. CLAUDE.md: line 5 points at docs/constitution.json as the project's own articles beside ember's universal ones; lines 7-10 say ember verify, run by the pre-push hook, is the only gate, and CI runs make test on Linux and macOS as proof, never a gate. README.md Development (lines 174-181): make test runs every unit's tests, the same command CI runs; needs Go, uv and Python, plus Xcode or the Command Line Tools for the menu-bar app's tests on a Mac. docs/architecture/components.md (line 157), boundaries.md (lines 16-19) and manifest.md describe make test and CI in place of verify.sh; docs/architecture/decisions.md, a dated log, keeps every existing entry as written and gains one dated entry for this change, so its history still names .specify on purpose.

## Commitments

- No tracked file outside docs/work/ and the dated decisions log names .specify after the change: `git ls-files | grep -v '^docs/work/' | grep -v '^docs/architecture/decisions.md$' | xargs grep -l '\.specify'` prints nothing; docs/architecture/decisions.md keeps its dated entries as written and gains one dated entry recording this change.
- make test runs, in daemon, cli and packaging/homebrew, ruff check, ruff format --check, mypy and pytest; in tui gofmt, go vet, staticcheck and go test; and on Darwin swift format lint, swift build and swift test in macos; it runs every check before exiting, names each failure, and exits non-zero when any fails; on Linux it reports macos skipped and passes on the rest.
- macos/scripts/test.sh runs swift format lint, swift build and the Swift tests on this Mac's Command Line Tools with every @Test expanded and run, derives exactly the framework, rpath and plugin-path flags for a developer directory named CommandLineTools and no flags under Xcode (a standing test asserts both from its --print-test-flags output), and runs plain swift test under Xcode.
- CI's test step is make test on ubuntu-latest and macos-latest, the history secret scan stays on Linux, and CI is green on both runners at the merge commit.
- scripts/release.sh has no --no-verify and no verify step; it pushes main and the tag in one push; a refused push leaves origin untouched and prints the undo line; scripts/tests/test_make_release.sh proves both with a pre-push hook in the clone, and fails when release.sh pushes with --no-verify.
- docs/constitution.json holds exactly the four Project Constraints as articles and ember loads it without refusal (`ember verify --plan` and `ember shape step` on this repo succeed).
- CLAUDE.md names docs/constitution.json and ember verify as the only gate; README's Development says what make test runs and needs.
- ember verify passes on the tree.

## Sacrifices

- verify.sh's three self-tests (gitleaks plant, gate wiring, swift unit) go with no replacement in this repo: they proved verify.sh, which no longer exists, and ember's gate carries its own proofs in ember's repo. (not a case: Each self-test drives .specify/verify.sh in a throwaway repo (verify.sh:202-204); with the script deleted there is nothing left for them to prove.)
- make test and CI list the units by hand; a new unit reaches CI only when someone adds it there, though ember's gate discovers and runs it regardless. (5 of 5 unit manifests added to prismis since its first commit on 2025-08-12)
- CI does not run ember's pinned-tool checks (ty, betterleaks, zizmor, actionlint, osv-scanner, opengrep, bandit, swiftlint, govulncheck, shellcheck, shfmt, duplication, docs, redact): those are the gate's, run before every push from a maintainer's machine, and a pull request shows only the unit-declared checks until then. (10 of 16 every pull request ever opened on nickpending/prismis)

## Risk

The release test's hook stub proves the push goes through a pre-push hook, not that ember verify is what the hook runs; that is ember init's job, and doctor reports a push that went out without a passing run. A contributor on a Mac with only the Command Line Tools depends on test.sh's flag derivation; if Apple moves Testing again, swift test fails loudly there rather than passing unrun. A flagless swift test on the Command Line Tools passes or fails by what was built earlier on the machine (measured 2026-10-29: fresh @Suite content failed, rebuilt content passed), so no standing test asserts that a flagless run fails; the flag derivation is what is guarded.

## Licensed by

~/.claude/skills/ember-project/SKILL.md:21
