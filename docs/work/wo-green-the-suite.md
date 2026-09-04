---
id: wo-green-the-suite
type: fix
project: prismis
status: active
complexity: 7
created: 2026-09-03
updated: 2026-09-03
plan_ref: null
---

## What

Get `.specify/verify.sh` to exit 0 — a green test suite and a lint configuration that actually
checks the decidable defect classes, across all three units.

Then stand up CI running that same script, so the gate is enforced on every push rather than
only when someone remembers to run it locally.

This runs **before** `wo-openai-sdk-migration`. That order is blocked on this one.

## Why

`.specify/verify.sh` was authored 2026-09-03 (commit `d137628`) and fails on its first run.
Every unit is red, and it long predates any current work.

**The daemon suite had not run at all.** Twelve test files failed at *import* —
`from summarizer import ...` rather than `from prismis_daemon.summarizer import ...`, left over
from a flat→src layout move. pytest aborts collection on an import error, so those twelve took
the whole run down with them and `make test` executed zero daemon tests.

That class is now closed across all three of its shapes (commits `57adcdc`, `f784834`,
`b436d74`): line-anchored imports, indented in-function imports, and `mock.patch` target
strings. Closing it removed all 12 collection errors and made 78 previously-invisible tests
visible.

| | collection errors | passed | failed |
|---|---|---|---|
| as found | **12 (run aborts)** | 174 | 105 |
| import class closed | 0 | 200 | **153** |

The newly-visible failures were always there. Nothing was reporting them.

**Why this blocks the SDK migration:** a migration cannot be verified against a red baseline.
With 153 daemon tests already failing, no one can tell what the migration broke — and the build
lane's fix-loop would spend its rounds on failures it did not cause. The gate exists to prevent
exactly that.

## Brief

- **Stakes:** this is the baseline every future work order is measured against. A suite that is
  red by default trains everyone to ignore it, at which point the gate is decoration. It has
  already happened once here — twelve files sat unexecuted long enough that the code drifted
  out from under them.

- **Constraints:**
  - `.specify/verify.sh` is not weakened to make this pass. Its contract (bench's) requires it
    to fail on any failure; the fix is the suite, never the gate.
  - A test that is deleted must be deleted **because the behavior it guarded is gone**, with
    that reason stated. "It fails and I don't know why" is not a deletion rationale.
  - `INV-003` (the `service` → `light_service` config rename) is real and must survive whatever
    happens to the tests around it.
  - `daemon/scripts/model_playtest.py` is rudy's uncommitted work — do not modify.

- **Key Questions:**
  1. For each failure bucket: real regression, test rotted against an intentional change, or
     dead test to delete? The counts below partition the work but do not answer this.
  2. ~~Does `cli` get pyright or mypy?~~ **Decided: pyright**, matching `daemon` (P15 — use the
     pattern already in the repo rather than introducing a second typechecker).

### Measured failure breakdown (2026-09-03, after the import class was closed)

**daemon — 153 failed, 200 passed, 17 skipped, 1 xfailed**

| count | cause |
|---|---|
| **80** | `config.py:229` — `ValueError: Config [llm] section outdated`. One root cause, 52% of all failures. Test configs reach `Config.from_file` without `light_service` in their `[llm]` block. |
| 6 | `too many values to unpack (expected 2)` — a return-signature change the tests never followed |
| 6 | `ModuleNotFoundError` — dynamic `importlib` resolution, distinct from the import class already fixed |
| ~61 | assorted: `AssertionError` (36 total incl. above), `TypeError` (18), `AttributeError` (4), `FileNotFoundError` (1) |

Error-type totals: 70 `ValueError`, 36 `AssertionError`, 18 `TypeError`, 6 `ModuleNotFoundError`,
4 `AttributeError`, 1 `FileNotFoundError`.

**Start with the 80.** It is one fix at one call site's fixtures, and until it clears, the
remaining buckets cannot be read accurately — an API test failing on config never reaches the
behavior it was written to check.

**cli — 17 failed, 19 passed**

| count | file |
|---|---|
| 12 | `tests/integration/test_source_commands.py` |
| 4 | `tests/integration/test_source_validation.py` |
| 1 | `tests/unit/test_url_extraction.py` |

**tui — builds clean, `go vet` clean, `internal/ui` tests FAIL**

### Property 7 — enable the decidable defect classes

Neither pyproject declares `[tool.ruff.lint] select`, so ruff runs its default `E4,E7,E9,F`.
Both files carry `per-file-ignores` for `B008` and `S101` — rules that are not enabled, making
those ignores inert.

Measured with a class-covering select (`E4,E7,E9,F,B,ASYNC,BLE,ARG,ERA,RUF,ANN401`) before
changing anything: **246 findings in daemon, 47 in cli — 293 total.**

Top of the daemon distribution: `BLE001` blind-except ×73, `ARG002` unused-method-argument ×31,
`RUF010` ×30, `F401` unused-import ×20, `RUF012` ×18, `ARG001` ×18.

**Three are genuine defects, not style — fix these regardless of what happens to the rest:**
- `tests/integration/test_config_integration.py:132` — `F821` undefined name `load_config`
- `tests/integration/test_config_integration.py:191` — `F821` undefined name `config_path`
- `src/prismis_daemon/__main__.py:212` — `RUF006`: `asyncio.create_task(api_server.serve())`
  holds no reference. The event loop keeps only a weak one, so **the API server task can be
  garbage-collected mid-flight.** This is production code.

Enable the rules; do not disable a rule to make its count go away.

### Property 9 — declare the toolchain

- `ruff` is **not declared** as a dependency in either pyproject, despite both having ruff
  config. It currently only runs via `uvx`.
- `cli` has **no typechecker** — `verify.sh` reports this as its one uncovered class today.
- `daemon` has pyright, which is an accepted equivalent to the house-standard mypy.
- `tui` has `staticcheck` available and `gofmt`/`go vet` clean.

## Success Criteria

### SC-1: The gate passes
- **Given**: the repo after this work
- **When**: `./.specify/verify.sh` runs
- **Then**: it prints `verify: PASS` and exits 0
- **And**: its `VERIFY_COVERED:` line names checks for all three units
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  ./.specify/verify.sh >/tmp/v.out 2>&1
  rc=$?
  grep -q "^verify: PASS" /tmp/v.out && [ $rc -eq 0 ] && echo "SC-1: PASS" || echo "SC-1: FAIL"
  ```

### SC-2: The config root cause is fixed at the cause, not per-test
- **Given**: the 80 failures raising `Config [llm] section outdated` at `config.py:229`
- **When**: the fix is applied
- **Then**: zero tests fail with that message
- **And**: the fix is one shared fixture or helper, not 80 individual edits
- **And**: `INV-003` still holds — `light_service` remains the required key, and a genuinely
  outdated config still raises

### SC-3: Every deleted test has a stated reason
- **Given**: the diff for this work
- **When**: reviewed
- **Then**: each deleted test is accompanied by the behavior it guarded and why that behavior
  is gone
- **And**: no test is `skip`-ped or `xfail`-ed to reach green

### SC-4: The three genuine defects are fixed
- **Given**: the migrated tree
- **When**: `uvx ruff check --select F821,RUF006 .` runs in `daemon`
- **Then**: it reports zero findings
- **And**: `__main__.py`'s API server task holds a reference for the process lifetime

### SC-5: Lint classes are enabled and enforced
- **Given**: `daemon/pyproject.toml` and `cli/pyproject.toml`
- **When**: inspected
- **Then**: each declares `[tool.ruff.lint] select` covering weakened types, unhandled
  async/error paths, and dead code
- **And**: `ruff check .` exits 0 in both — or every remaining finding has a tracked handle and
  a `per-file-ignores` entry naming why
- **And**: the pre-existing `B008`/`S101` ignores now reference enabled rules

### SC-6: CI runs the same gate as local
- **Given**: `.github/workflows/ci.yml` (this repo has no CI today)
- **When**: a push to `main` or a pull request runs
- **Then**: the workflow's verify job runs `bash .specify/verify.sh` — **the same script**, not a
  reimplementation of its checks
- **And**: its install step walks the same self-discovery the script does (`find` for
  `pyproject.toml` and `go.mod`), so a unit added later is gated automatically with no list to
  keep in step
- **And**: the job passes on green `main`

  Pattern to match — `bench/.github/workflows/ci.yml`, whose header records why: it used to
  hand-list `[hooks, cli]` in a matrix, so CI checked 2 of 8 packages while the local gate
  checked all 8. A hand-maintained enumeration silently omits a unit. Do not reintroduce one
  here.

  This lands **last**, after the suite is green. CI that goes red on its first run trains
  everyone to ignore it, which is the same failure that let 12 test files sit unexecuted.

### SC-7: The toolchain is declared
- **Given**: both pyprojects
- **When**: inspected
- **Then**: `ruff` is a declared dev dependency in each, and `cli` declares a typechecker
- **And**: `verify.sh` reports no `VERIFY_UNCOVERED:` line for a missing typechecker

## Approach

[Empty — filled after planning]

## Verification

```bash
cd /Users/rudy/development/projects/prismis && ./.specify/verify.sh
```

That is the whole verification. It is the gate this work exists to turn green.

## Outputs

[Empty — filled on completion]
