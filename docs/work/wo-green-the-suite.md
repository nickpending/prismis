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
| **80** | `config.py:229` — `ValueError: Config [llm] section outdated`. **NOT test-authored config — the ambient one.** See the corrected diagnosis below. |
| 6 | `too many values to unpack (expected 2)` — a return-signature change the tests never followed |
| 6 | `ModuleNotFoundError` — dynamic `importlib` resolution, distinct from the import class already fixed |
| ~61 | assorted: `AssertionError` (36 total incl. above), `TypeError` (18), `AttributeError` (4), `FileNotFoundError` (1) |

Error-type totals: 70 `ValueError`, 36 `AssertionError`, 18 `TypeError`, 6 `ModuleNotFoundError`,
4 `AttributeError`, 1 `FileNotFoundError`.

### CORRECTED DIAGNOSIS (2026-09-03, ferret pass + measurement)

**The failure count is a property of the developer's home directory, not of this repo.**

`daemon/tests/conftest.py:42` monkeypatches `XDG_DATA_HOME` only. Nothing isolates
`XDG_CONFIG_HOME`. Meanwhile production code loads the ambient config with no path:
`auth.py:31`, `api.py:211`, `__main__.py:377,698`, and all four fetchers
(`rss.py:36`, `reddit.py:34`, `youtube.py:36`, `file.py:43`) call `Config.from_file()`.
That resolves `$XDG_CONFIG_HOME/prismis/config.toml` (`config.py:182-185`).

Measured, same tree, three environments:

| `XDG_CONFIG_HOME` | failed | passed |
|---|---|---|
| empty — no prismis config at all | **113** | 240 |
| hostile `[remote]`-only config | 153 | 200 |
| the developer's actual `~/.config` | **153** | 200 |

A 40-test swing on home-directory contents alone. **CI, which has no config, would produce the
113 number** — so a workflow written against the local count disagrees with itself on its first
run.

**This makes SC-2's original framing wrong.** "Zero tests fail with that message" is satisfiable
by changing machines. The real criterion is environment isolation, and it is SC-8 below.

Two further consequences:
- `conftest.py` lines 63-67 and 74-78 swallow config failure with
  `except Exception: pytest.skip("Config file not found...")`. That is the 17 skipped, and it
  violates the constitution's Quality Gates ("a gate that passes having executed nothing").
- The isolation fix must NOT monkeypatch `Config.from_file` — that would neuter the INV-003
  negative tests at `test_dual_service_config_unit.py:200`, which need a real outdated config
  to raise.

**Start with isolation, not with the 80.**

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

**The select is `E4,E7,E9,F,B,ASYNC,BLE,ERA,RUF006,RUF012,RUF013,RUF100,ANN401,PGH,S110,S112`.**

An earlier draft used `...,ARG,ERA,RUF,ANN401` and measured 293. That over-selected for the
three named classes: `ARG002`×31 + `ARG001`×18 are FastAPI/protocol signature conformance, and
`RUF010`×30 is pure style — 79 of 246 daemon findings from rules outside all three classes. It
also under-selected: `S110`/`S112` (try-except-pass / try-except-continue) are the sharpest
error-swallow rules and no `S` was present, and `PGH003` (blanket `# type: ignore`) is the real
weakened-types escape hatch.

Measured with the corrected select:

| | daemon | cli | total |
|---|---|---|---|
| corrected select | 151 | 19 | **170** |
| corrected, minus `BLE001` | 78 | 15 | **93** |

**`BLE001` (74 findings) is OUT OF SCOPE for this work order** — decided 2026-09-03. It stays in
the select but carries a `per-file-ignores` entry pointing at
**https://github.com/nickpending/prismis/issues/58**, so the class is visible and scheduled
rather than silently off. Narrowing a blind except changes runtime behavior in a pipeline the
constitution describes as unattended, and the tests that would catch that regression are exactly
the ones this work order is fixing. Doing it against a green suite is the point.

**So this work order closes 93 findings, not 293.**

`ruff check .` in `daemon` would lint `daemon/scripts/model_playtest.py`, which the Constraints
forbid touching. Add `daemon/scripts/` to ruff's `exclude` rather than editing that file.

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

### Key exposure — verified 2026-09-03, not a hypothesis

`git log -S` against the live key value:

- **9 commits on `origin/main`** — i.e. already public on github.com/nickpending/prismis
- earliest occurrence `e2ffa94`, **2025-09-09** — roughly twelve months
- 31 occurrences currently in the working tree across 10 daemon test files

Step 2 removes it from the tree. **Removing it from the tree does not remove it from history.**
Whether that warrants rotation is the operator's call on their own threat model; the facts above
are recorded so the decision is made against them rather than against an assumption.

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
- **And**: the fix is an autouse fixture in `daemon/tests/conftest.py` isolating
  `XDG_CONFIG_HOME`, not 80 individual edits, and not a monkeypatch of `Config.from_file`
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
- **When**: `uv run ruff check --select F821,RUF006 .` runs in `daemon` — `uv run`, matching
  `verify.sh:43`, not `uvx`; different resolutions give different versions and different findings
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

### SC-8: The suite is independent of the ambient config
- **Given**: a `$XDG_CONFIG_HOME` holding a hostile prismis config (`[remote]` only, no `[llm]`),
  and separately one holding no prismis directory at all
- **When**: the daemon suite runs under each
- **Then**: both exit 0, and both produce **identical** pass/fail/skip counts
- **And**: zero tests skip for the reason "Config file not found" — `conftest.py`'s two
  `except Exception: pytest.skip(...)` fallbacks are gone
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis/daemon
  hostile=$(mktemp -d); mkdir -p "$hostile/prismis"
  printf '[remote]\nurl = "https://example.invalid"\nkey = "x"\n' > "$hostile/prismis/config.toml"
  empty=$(mktemp -d)
  XDG_CONFIG_HOME="$hostile" uv run pytest -q --no-header >/tmp/sc8a.out 2>&1; a=$?
  XDG_CONFIG_HOME="$empty"   uv run pytest -q --no-header >/tmp/sc8b.out 2>&1; b=$?
  ha=$(grep -Eo '[0-9]+ (passed|failed|skipped)' /tmp/sc8a.out | sort | tr '\n' ' ')
  hb=$(grep -Eo '[0-9]+ (passed|failed|skipped)' /tmp/sc8b.out | sort | tr '\n' ' ')
  if [ $a -eq 0 ] && [ $b -eq 0 ] && [ "$ha" = "$hb" ] \
     && ! grep -q "Config file not found" /tmp/sc8a.out /tmp/sc8b.out; then
    echo "SC-8: PASS"; else echo "SC-8: FAIL"; fi
  ```
  Measured today this prints FAIL with 153 vs 113 — the gap this criterion exists to close.

### SC-9: Green locally implies green in CI, proven before ci.yml is written
- **Given**: a shell stripped of the developer environment (fresh `HOME`, no `XDG_CONFIG_HOME`)
- **When**: `./.specify/verify.sh` runs
- **Then**: it prints `verify: PASS`, exits 0, and emits the same `VERIFY_COVERED:` line as a
  normal local run
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  ./.specify/verify.sh >/tmp/sc9-local.out 2>&1 || true
  fresh=$(mktemp -d)
  env -i PATH="$PATH" HOME="$fresh" UV_CACHE_DIR="$HOME/.cache/uv" \
    ./.specify/verify.sh >/tmp/sc9-ci.out 2>&1; rc=$?
  a=$(grep '^VERIFY_COVERED:' /tmp/sc9-local.out); b=$(grep '^VERIFY_COVERED:' /tmp/sc9-ci.out)
  if [ $rc -eq 0 ] && grep -q '^verify: PASS' /tmp/sc9-ci.out && [ "$a" = "$b" ]; then
    echo "SC-9: PASS"; else echo "SC-9: FAIL"; fi
  ```
  SC-6 defers CI to last so it isn't red on arrival. This gets the *evidence* locally instead of
  deferring it, and given `Config.from_file()`'s `$HOME` dependence it is the criterion most
  likely to fail first.

### SC-10: pytest.ini is actually read
- **Given**: `daemon/pytest.ini`, whose section header is `[tool:pytest]` — the `setup.cfg`
  section name, inert in a `pytest.ini`
- **When**: corrected to `[pytest]`
- **Then**: `testpaths` and `python_classes` take effect, and `python_paths` (a dead plugin
  option) is replaced by core pytest's `pythonpath`
- **And**: the suite still collects the same test count, proving `conftest.py:19`'s manual src
  path insertion is now redundant or still needed — stated either way, not left ambiguous

### SC-11: staticcheck is measured
- **Given**: `tui/`
- **When**: `staticcheck ./...` runs
- **Then**: zero findings, or each remaining one carries a tracked handle
- **And**: the CI image installs it, since `verify.sh:73` branches on `command -v staticcheck`
  and would silently skip the check where it is absent

### SC-12: Deletions are auditable, including unrecoverable ones
- **Given**: the diff and this work order's `## Outputs`
- **When**: reviewed
- **Then**: every removed `def test_` has one line reading
  `- DELETED <file>::<name> — <CATEGORY>: <detail>`
- **And**: `<CATEGORY>` is one of `BEHAVIOR-GONE`, `SUPERSEDED`, or `UNRECOVERABLE` (the latter
  naming the searches run — `git log --diff-filter=D -S`, docstring, INV id — that found nothing)
- **And**: no test is `skip`-ped or `xfail`-ed to reach green

  This replaces SC-3, which was unsatisfiable: it forbade skip/xfail *and* required a rationale
  that may not exist, leaving no legal move for a test whose intent is genuinely unrecoverable.

## Approach

[Empty — filled after planning]

## Verification

```bash
cd /Users/rudy/development/projects/prismis && ./.specify/verify.sh
```

That is the whole verification. It is the gate this work exists to turn green.

## Outputs

[Empty — filled on completion]
