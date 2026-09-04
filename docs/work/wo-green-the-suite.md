---
id: wo-green-the-suite
type: fix
project: prismis
status: awaiting-push
complexity: 7
created: 2026-09-03
updated: 2026-09-03
plan_ref: docs/work/green-the-suite/plan.md
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
rather than silently off. *(As built this is not what happened — the ignore is a `"**/*.py"` glob,
so the class is suppressed everywhere and no ruff run reports it. See `## Outputs → Lint
configuration`.)* Narrowing a blind except changes runtime behavior in a pipeline the
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

Sequenced so each step's measurement is valid: declare the toolchain (nothing can be counted
before ruff and cli pyright exist), seal the test environment (no failure count is readable while
it is a property of `$HOME`), re-measure to get the real baseline, then work the buckets, then
prove the fresh-environment case, then land CI on an already-green tree.

The real sealed baseline was **84 failures** — neither the 113 measured against an empty config nor
the 153 measured against the developer's own. Every fix below was chosen against that number, not
against either pre-seal figure.

## Verification

```bash
cd /Users/rudy/development/projects/prismis && ./.specify/verify.sh
```

That is the whole verification. It is the gate this work exists to turn green.

## Outputs

### Gate

`./.specify/verify.sh` → `verify: PASS`, exit 0, no `VERIFY_UNCOVERED:` line.

```
VERIFY_COVERED: ruff(cli),pyright(cli),pytest(cli),ruff(daemon),pyright(daemon),pytest(daemon),gofmt(tui),go vet(tui),staticcheck(tui),go test(tui)
verify: PASS
```

### Measurements

| | before | after |
|---|---|---|
| daemon pytest | 113 failed / 240 passed (empty config) · 153 failed / 200 passed (developer's config) | **344 passed, 0 failed**, 27 skipped, 1 xfailed |
| daemon ruff | not runnable (`ruff` undeclared); 76 findings once declared | **0** |
| daemon pyright | 18 errors | **0** |
| cli pytest | 17 failed / 19 passed | **27 passed, 0 failed** |
| cli ruff | not runnable; 13 findings once declared | **0** |
| cli pyright | no typechecker configured | **0 errors** |
| tui go test | `internal/ui` FAIL | **all packages ok** |
| tui staticcheck | 18 findings | **0** |

The sealed baseline was 84 failures — matching neither 113 nor 153, as the plan predicted. The
suite's count had been a property of the developer's `$HOME`, not of this repo.

### Known ambient input the seal does NOT cover: the HuggingFace model cache

Recorded 2026-09-04, before the SC-8/SC-9 evidence below, because it qualifies both.

`huggingface_hub` computes `HF_HOME` **at import** from `os.getenv("XDG_CACHE_HOME", ...)` (the
`HF_HOME` assignment in `huggingface_hub/constants.py`). That import happens during pytest
collection — `api.py` does `from .embeddings import Embedder`, and `embeddings.py` does
`from sentence_transformers import SentenceTransformer` — so it runs before the autouse seal
exists. Measured: after importing the module, setting `XDG_CACHE_HOME` leaves
`huggingface_hub.constants.HF_HOME` pointing at `/Users/rudy/.cache/huggingface` (16G on this
machine). `Embedder()` is then constructed per request inside `api.py`'s search and
similar-content handlers, so `/api/search` tests resolve `all-MiniLM-L6-v2` from that real cache.

**Neither SC-8 nor SC-9 can see it.** SC-8 varies only `XDG_CONFIG_HOME`. SC-9's `env -i` run
strips `HOME`, which *would* redirect the cache — but a pass there is equally consistent with a
silent ~90MB download, and the criterion cannot tell those apart.

Not sealed to a per-run temp dir: that forces a fresh download on every local run. Instead the
model is made an **explicitly provisioned input**, the same move `ci.yml` already makes for
staticcheck — `.github/workflows/ci.yml` caches `~/.cache/huggingface` keyed on the model
identity, so a cache miss is the only path that reaches the network and an upstream outage cannot
turn the gate red for reasons unrelated to the code.

**So the claim below is scoped, not absolute:** the suite is independent of the ambient *config*
and data directories. The embedding model is a separate ambient input, covered by the CI cache
rather than by the seal, and on a developer machine it still comes from the real
`~/.cache/huggingface`.

### SC-8 — the suite is independent of the ambient config

Measured, same tree, two hostile environments:

| `$XDG_CONFIG_HOME` | rc | counts |
|---|---|---|
| `[remote]`-only config, no `[llm]` | 0 | 344 passed, 27 skipped |
| empty dir, no prismis config at all | 0 | 344 passed, 27 skipped |

Identical, and zero occurrences of "Config file not found". Before this work the same two
environments produced 153 and 113 failures respectively — a 40-test swing on home-directory
contents alone.

### SC-9 — green locally implies green in CI

Caught a real CI blocker that the local gate could not see. Under `env -i` with a fresh `HOME`,
`go test(tui)` failed:

```
--- FAIL: TestConnectionPool
    connection_test.go:11: failed to set WAL mode: unable to open database file: no such file or directory
--- FAIL: TestPoolSingletonStress
    pool_stress_test.go:72: Had 50 errors during stress test
```

Both call `db.GetDB()` without overriding the `dbPathFunc` seam, so they open whatever database
sits at the developer's real `XDG_DATA_HOME`. That file exists on exactly one machine. **CI has no
prismis database, so both would have failed on the workflow's first run** — the outcome SC-6 defers
CI specifically to avoid.

This is the same class the Python seal closed — "the test process reads the developer's `$HOME`" —
which I had swept in `daemon/tests` and `cli/tests` but not in `tui/`. Sweeping it properly found
four members:

| site | disposition |
|---|---|
| `internal/db/connection_test.go::TestConnectionPool` | **fixed** — overrides `dbPathFunc` to a temp DB |
| `internal/db/pool_stress_test.go::TestPoolSingletonStress` | **fixed** — same |
| `internal/db/queries_integration_test.go` | already guarded: skips when the real DB is absent |
| `internal/api/client_test.go:272` | safe: builds a path string, never touches the filesystem |

The fix reuses the package's own existing pattern rather than inventing one — `resetDBForTest`,
`createTestDB` and the `dbPathFunc` override are already used 41 times in `queries_test.go`.

**SC-9's recipe as written is confounded on a mise-managed machine, and the confound is not in the
repo.** `env -i ... HOME=$fresh` strips mise's tool config (`$HOME/.config/mise/config.toml`), so
every mise shim on PATH breaks:

```
mise ERROR staticcheck is not a valid shim. This likely means you uninstalled a tool
and the shim does not point to anything.
```

`staticcheck` then exits 1 having analyzed nothing, and the gate reports `FAILED: staticcheck(tui)`
for a reason that has no analogue in CI — CI has no mise, and gets a real binary from
`go install`. Running the check faithfully means provisioning staticcheck the way the workflow
does (`GOBIN=$tmp go install honnef.co/go/tools/cmd/staticcheck@v0.8.1`) and putting it on PATH
ahead of the shim. That build reports `staticcheck 2026.2.1 (0.8.1)` — identical to the local
version — which is the evidence the CI pin is the right one.

Anyone re-running SC-9 on this machine must do the same, or they will chase a phantom.

Run faithfully, it passes:

```
fresh-env rc=0
VERIFY_COVERED: ruff(cli),pyright(cli),pytest(cli),ruff(daemon),pyright(daemon),pytest(daemon),gofmt(tui),go vet(tui),staticcheck(tui),go test(tui)
verify: PASS
COVERED lines identical
SC-9: PASS
```

A stripped environment with a fresh `HOME` produces the same verdict and the same covered-check
set as a normal local run.

### Root causes fixed (not per-test patches)

- **Environment seal** (`daemon/tests/conftest.py`) — one autouse fixture setting `HOME` and all
  four XDG vars to fresh temp dirs and materializing a valid config from the production template
  (`defaults.DEFAULT_CONFIG_TOML`). Reaches the subprocess at `test_daemon_integration.py`, which no
  in-process patch can. `Config.from_file` is NOT patched, so INV-003's negative test still raises
  on a genuinely outdated config. Same seal added to `cli/tests/conftest.py`.
- **Committed secret removed** — 31 occurrences of the operator's live API key across 10 daemon test
  files now resolve from one `TEST_API_KEY` constant in the sealed conftest. Zero occurrences remain
  in the tree. **Removing it from the tree does not remove it from history**: `git log -S` finds it
  in 9 commits on `origin/main`, earliest `e2ffa94` (2025-09-09). Rotation is the operator's call.
- **`yt-dlp` was undeclared** — `fetchers/youtube.py:39-42` hard-requires the binary and raises
  without it, but no pyproject declared it. A fresh install had a YouTube fetcher that could not
  run. Declaring it fixed 19 tests and the packaging gap.
- **`pytest.ini` was inert** — the header was `[tool:pytest]`, the `setup.cfg` section name, so
  `testpaths`, `python_classes`, `addopts` and the rest had never taken effect. Corrected to
  `[pytest]`, `python_paths` (a dead plugin option) → core pytest's `pythonpath`, and `-v` dropped
  from `addopts` so the caller owns verbosity.
- **`sys.path` hacks retired** — with `pythonpath` declared, the manual `sys.path.insert` in
  `daemon/tests/conftest.py` is redundant: collection is **372 tests both with and without it**, so
  it was deleted. Same for five `cli/tests` modules, which also closed their E402s at the cause.
- **Two skip fallbacks deleted** — `conftest.py`'s `except Exception: pytest.skip("Config file not
  found...")` pair was hiding an `AttributeError` that fired on *every* machine (`llm_config` called
  `.get` on a `Config` dataclass). A gate that passes having executed nothing.

### Genuine defects fixed

- `__main__.py:212` **RUF006** — `asyncio.create_task(api_server.serve())` held no reference. The
  event loop keeps only a weak one, so **the API server task could be garbage-collected mid-flight**.
  Bound as `api_task` and awaited under a bounded timeout during shutdown, which also replaces a
  blind `sleep(0.5)` with waiting on the actual task.
- `test_config_integration.py:132,191` **F821** — undefined `load_config` / `config_path`; closed
  with the file's rewrite.
- `database.py:130` — the inner sqlite-vec fallback handler reported the *outer* exception and
  discarded its own. Now chained.
- `embeddings.py:64` — declared `-> int` while returning `int | None`. Now raises rather than
  silently returning `None` as a dimension.
- `rss.py:97-98` — feed `title`/`link` passed to `ContentItem` unchecked; coerced at the boundary.

### Deleted tests (SC-12)

Categories: `SUPERSEDED` (behavior replaced by a deliberate change), `BEHAVIOR-GONE` (the guarded
behavior no longer exists), `UNRECOVERABLE` (intent not recoverable).

`cli/tests/integration/test_source_commands.py` and `test_source_validation.py` deleted wholesale:
they patch `cli.source.SourceValidator` and `cli.source.VALIDATOR_AVAILABLE` — `rg 'VALIDATOR_AVAILABLE|SourceValidator' cli/src` returns nothing. The CLI no longer validates locally or writes
SQLite; it adds through `APIClient.add_source` (`source.py:115`) and the daemon owns validation.
They also hit the live network, which Principle IV rules out for a CI-run gate. **Coverage re-homed**
in `cli/tests/unit/test_source_command_unit.py` (7 cases over the URL → `source_type` mapping).

- DELETED cli/tests/integration/test_source_commands.py::test_add_rss_source — SUPERSEDED: local-validation CLI replaced by APIClient.add_source; type detection re-homed in test_source_command_unit.py
- DELETED cli/tests/integration/test_source_commands.py::test_add_reddit_source — SUPERSEDED: same; reddit:// mapping re-homed in test_source_command_unit.py
- DELETED cli/tests/integration/test_source_commands.py::test_add_youtube_source — SUPERSEDED: same; youtube:// mapping re-homed in test_source_command_unit.py
- DELETED cli/tests/integration/test_source_commands.py::test_add_source_with_custom_name — SUPERSEDED: same; name pass-through is now an APIClient argument
- DELETED cli/tests/integration/test_source_commands.py::test_list_sources_empty — SUPERSEDED: read SQLite directly; listing is now a daemon API call
- DELETED cli/tests/integration/test_source_commands.py::test_list_sources_with_data — SUPERSEDED: same
- DELETED cli/tests/integration/test_source_commands.py::test_remove_source — SUPERSEDED: same
- DELETED cli/tests/integration/test_source_commands.py::test_remove_nonexistent_source — SUPERSEDED: same
- DELETED cli/tests/integration/test_source_commands.py::test_pause_source — SUPERSEDED: same
- DELETED cli/tests/integration/test_source_commands.py::test_resume_source — SUPERSEDED: same
- DELETED cli/tests/integration/test_source_commands.py::test_source_type_detection — SUPERSEDED: re-homed verbatim as test_source_command_unit.py::test_source_type_detected_from_url
- DELETED cli/tests/integration/test_source_commands.py::test_duplicate_source_handling — SUPERSEDED: duplicate rejection moved server-side to the daemon API
- DELETED cli/tests/integration/test_source_validation.py::test_invalid_source_not_added_when_validator_rejects — BEHAVIOR-GONE: the CLI does not validate; cli.source.SourceValidator does not exist
- DELETED cli/tests/integration/test_source_validation.py::test_validation_error_message_shown_to_user — BEHAVIOR-GONE: same
- DELETED cli/tests/integration/test_source_validation.py::test_source_type_detection_for_validation — SUPERSEDED: re-homed in test_source_command_unit.py
- DELETED cli/tests/integration/test_source_validation.py::test_validator_unavailable_warning — BEHAVIOR-GONE: patches cli.source.VALIDATOR_AVAILABLE, which does not exist
- DELETED daemon/tests/integration/test_config_integration.py::test_complete_config_workflow_with_real_files — SUPERSEDED: asserts max_items/llm_provider/llm_model/llm_api_key, absent post-rename (decisions.md:223); the ensure_config → from_file workflow is re-homed as test_ensure_config_produces_a_loadable_config
- DELETED daemon/tests/integration/test_config_integration.py::test_config_loading_with_custom_user_modifications — SUPERSEDED: subscripts load_config() as a dict; Config is a dataclass
- DELETED daemon/tests/integration/test_config_integration.py::test_config_integration_with_partially_missing_files — BEHAVIOR-GONE: partial-config fallback replaced by a hard raise (config.py:188-198)
- DELETED daemon/tests/integration/test_config_integration.py::test_config_from_file_with_max_items — SUPERSEDED: max_items / max_items_per_feed split into four per-type fields; bound coverage re-homed in test_config_unit.py::test_config_max_items_validation
- DELETED daemon/tests/integration/test_config_integration.py::test_config_from_file_uses_defaults_when_max_items_missing — BEHAVIOR-GONE: defaults-on-missing-key removed; required fields now raise
- DELETED daemon/tests/unit/test_config_unit.py::test_config_loading_with_missing_files_uses_defaults — BEHAVIOR-GONE: renamed to test_config_loading_with_missing_file_raises; fallback replaced by FileNotFoundError (config.py:188-192)
- DELETED daemon/tests/unit/test_config_unit.py::test_config_loading_with_malformed_toml_uses_defaults — BEHAVIOR-GONE: renamed to test_config_loading_with_malformed_toml_raises; parse errors now raise (config.py:196-198)
- DELETED daemon/tests/unit/test_summarizer_unit.py::test_summarizer_initialization_with_config — SUPERSEDED: ContentSummarizer takes an llm-core service name (summarizer.py:39-46), not a config dict; replaced by test_summarizer_initialization_with_service_name
- DELETED daemon/tests/unit/test_summarizer_unit.py::test_summarizer_initialization_with_defaults — SUPERSEDED: same; model selection is llm-core's, not prismis's
- DELETED daemon/tests/unit/test_summarizer_unit.py::test_summarizer_handles_env_api_key — BEHAVIOR-GONE: `env:` API-key expansion moved into llm-core's services.toml; the summarizer never sees a key
- DELETED daemon/tests/unit/test_evaluator_unit.py::test_evaluator_initialization_with_config — SUPERSEDED: ContentEvaluator takes a service name (evaluator.py:40-47); replaced by test_evaluator_initialization_with_service_name
- DELETED daemon/tests/unit/test_evaluator_unit.py::test_evaluator_initialization_with_defaults — SUPERSEDED: same

Ten further `-def test_` lines in the diff are signature changes, not deletions — the nine
`test_file_fetcher_unit.py` cases gained the `test_db` fixture (they had been silently using the
developer's real database) and `test_config_loading_with_all_files_present` kept its name.

### Skips — every one names a precondition and a handle

No test is skipped to dodge a failure. **28 skips**, all resource preconditions. Every row below
was re-derived from a single run's `-rs` output on 2026-09-04, and the rows sum to the skip count
pytest reports:

| count | gate | why |
|---|---|---|
| 8 | `OPENAI_API_KEY` | 6 pre-existing `skipif` marks plus 2 inline `pytest.skip`s in `test_llm_startup_validation_integration.py` |
| 7 | `PRISMIS_LIVE_NETWORK_TESTS` | blocked on **gh #59**, not on credentials |
| 7 | `PRISMIS_LIVE_LLM_TESTS` | needs a live llm-core service (gh #60) |
| 5 | `REDDIT_CLIENT_ID` | PRAW returns 401 without OAuth credentials (gh #60) |
| 1 | `shutil.which("terminal-notifier")` | macOS-only binary, absent on the CI runner **and on the operator's machine** (gh #60) |
| **28** | | **= pytest's reported skip count** |

Separately, pytest reports **1 xfailed** — `test_rfc3339_helper_unit.py`, the documented xfail two
other files assert must remain. An earlier revision listed it as a fifth row of this table, which
was wrong twice: an xfail is not a skip and pytest counts them separately, and that row sat about
forty lines below the other four with intervening prose, so the table did not read as a table at
all. It is out of the skip count now.

An earlier revision's rows summed to 26 against a stated 27. The cause was re-gating
`test_complete_analysis_pipeline` out of the `OPENAI_API_KEY` row without adding it to the
`PRISMIS_LIVE_LLM_TESTS` row — patching the row known to be wrong instead of re-deriving all of
them. Hence the whole-table re-derivation above.

**Correction (2026-09-04): the ninth was mine, not pre-existing.** `plan.md:58-61` names eight
pre-existing marks; this table reported nine and labelled them all pre-existing. The extra was a
`skipif` I added to
`test_summarizer_evaluator_integration.py::test_complete_analysis_pipeline` — the very test
`plan.md:184-185` predicted would surface as a *failure* once the conftest fallbacks were removed,
and routed to Step 6 to be fixed. `git show c11e34f~1` on that file has **3** `skipif`s; it now
has 4.

Worse, the mark named the wrong resource. It gated on `OPENAI_API_KEY`, but exporting that does
not let the test run — it makes it fail:

```
$ OPENAI_API_KEY=probe uv run pytest …::test_complete_analysis_pipeline
llm_core.exceptions.ConfigError: Unknown service: "prismis-openai". Available: [anthropic, openai, ollama]
```

The real precondition is a live llm-core `services.toml` defining `prismis-openai`, which the seal
removes along with `HOME`. Re-gated on `PRISMIS_LIVE_LLM_TESTS`, matching the other live-service
tests, with the reason string stating outright that the mark was added here.

**The repair itself is sound and is now proven** — separately from the LLM call, which cannot run
anywhere: `llm_config` resolves to a non-empty service-name string (it used to call `.get` on a
dataclass), `full_config.context` resolves to a non-empty string (it used to subscript one), and
both `ContentSummarizer` and `ContentEvaluator` accept the service name. What stays unexecuted on
every machine is only the live model call.

**Class finding, not fixed here.** All three *pre-existing* marks in that file have the same
wrong-gate defect — `test_summarizer_with_real_llm_extracts_all_fields` also fails rather than runs
with `OPENAI_API_KEY` set, for the same `Unknown service` reason. They are left alone because
`plan.md` settles that those marks must survive, and re-gating them is re-opening a settled
decision rather than a defect this work order introduced. Recorded so it is not lost.

`test_rfc3339_helper_unit.py::test_boundaries_md_documents_rfc3339_contract` no longer skips: it
read `~/obsidian/projects/prismis/architecture/boundaries.md`, outside the clone, so it skipped on
every machine but one — including CI, where the invariant it names was therefore unguarded.
Repointed at the in-repo `docs/architecture/boundaries.md`, which satisfies all three of its
assertions. Sweep: `rg '/Users/rudy|obsidian|Path.home\(\)' daemon/tests cli/tests` now returns only
a legitimate XDG fallback — the "test reads a path outside the repo" class is closed.
| 1 | documented `xfail` | `test_rfc3339_helper_unit.py`, required by two other files |

Previously these passed only because an import-time `load_dotenv` of
`$XDG_CONFIG_HOME/prismis/.env` injected the operator's keys, so local runs silently exercised paths
CI never could.

**Correction (2026-09-04).** An earlier revision of this section claimed that load "is deleted".
That was wrong, and the claim was checked against the wrong noun: only the copy in
`daemon/tests/conftest.py` was removed. A second copy lived at **module scope** in
`daemon/src/prismis_daemon/__main__.py:32-36`, and four test modules import that module at top
level — `test_verify_subcommand_unit.py:21`, `test_llm_startup_validation_unit.py:13`,
`test_dual_service_config_unit.py:23`, `test_llm_startup_validation_integration.py:14` — so it ran
at pytest **collection**, before the autouse seal exists. Neither SC-8 nor SC-9 could observe it:
both set or strip `XDG_CONFIG_HOME`/`HOME` at process launch, so the `.env` path resolves into a
temp directory under either, and the leak stays invisible. It was inert on this machine only
because no `.env` happens to be present — the exact machine-dependence this work order exists to
remove, still live on the largest surface in the repo.

Fixed by moving the load into `_load_ambient_env()`, called from the Typer callback
(`__main__.py:375`) which runs for every real invocation including subcommands. Proven directly
rather than argued: with a `.env` planted on the XDG path, importing `prismis_daemon.__main__`
leaves the variable unset, and calling the entry-point helper sets it.

**Class swept, not just the instance.** The class is "module-scope side effect in
`prismis_daemon` that reads the environment", reachable at collection by any test that imports the
module. Swept by walking every module's AST and inspecting statements at module scope only
(skipping imports, defs and classes) for `getenv`/`environ`/`load_dotenv`/`expanduser`/`home`.
Two members across the whole package:

| site | disposition |
|---|---|
| `prismis_daemon.__main__` — the `.env` load | **fixed** — moved into `_load_ambient_env()`, called from the Typer callback |
| `prismis_daemon.api` — `/audio` mount gated on `XDG_DATA_HOME` existing at import | **fixed** — mounted unconditionally with `check_dir=False` |
| `cli.__main__` — a byte-identical `.env` load | **fixed 2026-09-04** — see below |

**The first sweep was scoped to the wrong thing.** It walked `daemon/src/prismis_daemon` only,
but the defect shape is "an entry-point module loads `$XDG_CONFIG_HOME/prismis/.env` at import",
which is not package-specific. `cli/src/cli/__main__.py` carried the same code verbatim, inert only
because its one importer (`test_extract_command_unit.py`) imports it inside a test body, after the
seal — a module-scope import would have restored the leak. Fixed by mirroring the daemon's
`_load_ambient_env()` rather than inventing a second pattern, and proved the same way: with a
`.env` planted on the XDG path, importing `cli.__main__` leaves the variable unset and calling the
helper sets it.

Re-swept across **both** `daemon/src/prismis_daemon` and `cli/src/cli`: one module-scope
environment read remains, the `/audio` directory above, which no longer branches the route table.
`api.py`'s `static_dir` was examined and is **not** a member — it is `Path(__file__)`-relative and
`static/index.html` is tracked, so the branch is true in every clone including CI.

The second made the app's *route table* a property of the developer's disk: `/audio` was a live
route on a machine with the directory and an unknown path elsewhere, and 13 test modules import
`api` at collection. `StaticFiles` resolves files per request, so mounting unconditionally keeps
the route deterministic while what it serves still follows the live filesystem.
`api.py:226`'s `static_dir` was examined and is **not** a member — it is `Path(__file__)`-relative,
not environment-derived.

### Skips — the Go half of the audit (added 2026-09-04)

The table above was built from pytest output alone and its claim "no test is skipped to dodge a
failure" was scoped to Python. `go test ./...` runs under the same gate and reached **twelve**
`t.Skip` sites that were never classified. `verify.sh:26` sends step output to `/dev/null`, so none
of it was visible in a gate run. Two were converting a failure into a skip:

| site | was | disposition |
|---|---|---|
| `internal/db/queries_test.go:627` | `t.Skip("Connection pool contaminated…")` when `GetDB()` errored | **failure-dodge, closed.** The test points `dbPathFunc` at a temp database 12 lines above (`:615-618`), so a `GetDB()` error there is a real failure. Now `t.Fatalf`. |
| `internal/api/client_test.go:375` | `t.Skip("Expected timeout but got success…")` when the timeout did not occur | **failure-dodge, closed.** It also hit `localhost:8989`, so with no daemon it got connection-refused rather than a timeout and "passed" without exercising one. Rewritten against an `httptest` server that holds the request past the deadline — the timeout is now real, and the recovery invariant is actually asserted. |

The other ten:

| site | classification |
|---|---|
| `client_test.go:19` — `PRISMIS_TEST_API_KEY` unset | resource precondition (needs a live daemon on :8989 plus a key). **CI never sets it, so every test in this file skips there** — `ok internal/api` is a pass having executed almost nothing. Recorded, not changed: making these run needs a daemon fixture, which is its own work. |
| `client_test.go:90,107,121` — `Daemon not running` | resource precondition. Unreachable in practice, since `:19` skips first. |
| `client_test.go:78,104,124,223,344` (5 sites) — `if client == nil { t.Skip(…) }` | **dead code, removed.** `t.Skip` unwinds via `runtime.Goexit` and never returns, so a caller never observes a nil client. Proven, not assumed: running those four tests shows only `client_test.go:19` firing. |
| `client_test.go:133` — `TestIntegrationWithDaemon` | **unconditional skip — executes nothing under any invocation.** Left in place and flagged rather than deleted: removing a pre-existing test needs an SC-12 rationale, and "end-to-end client against a live daemon" is not behaviour that is gone. It is a worked example wearing a test's clothes. Operator's call. |
| `queries_integration_test.go:15,20` | **removed with the file.** Dead three ways: `//go:build integration` means no gate compiles it (`go test -run … ` reports `no tests to run`); its guard stats `~/.config/prismis/prismis.db` while `getDefaultDBPath` resolves `XDG_DATA_HOME` → `~/.local/share/prismis/prismis.db` (`queries.go:443-452`), so it skips on a correct install; and `GetContentByPriority` reads that XDG path regardless, so the guard checked a file the test never opened. Coverage is re-homed — `queries_test.go:118::TestGetContentByPriority` exercises the same function against a temp DB with the real schema, under the gate. |

- DELETED tui/internal/db/queries_integration_test.go::TestGetContentByPriorityRealDB — SUPERSEDED: compiled by no gate, guard checked a path the code does not use; same function covered by queries_test.go::TestGetContentByPriority under the gate

### Second adversarial round — 2026-09-04

**`add`'s name derivation was uncovered, and two tests pinned inputs production never passes.**
`add` rebinds `url` from `detect_and_normalize_source_url` before calling `extract_name_from_url`,
so the name always comes from the NORMALIZED url. Measured: `source add reddit://rust` names the
source **`r/rust`**, not `rust` — yet `test_url_extraction.py` pinned
`extract_name_from_url("reddit://rust") == "rust"`, a raw form no user path produces, in a file
whose docstring presented itself as the CLI/daemon naming contract. Now covers the composition
`add` actually performs; the raw-form assertions are marked as the standalone contract they are.
The plan's Constitution Check row that called all of `add`'s remainder glue is corrected — the name
derivation is logic.

**A docstring I wrote asserted an invariant nothing checks.** It claimed the CLI and daemon "must
agree" on normalization. Measured directly against both functions, they do not:

| input | CLI | daemon | agree |
|---|---|---|---|
| `youtube://PLabc123` | `/channel/PLabc123` | `/@PLabc123` | no |
| `reddit://rust/` | `.../r/rust/` | `.../r/rust` | no |
| ` reddit://rust` | not detected as reddit at all | `.../r/rust` | no |
| `youtube://UCabc` | `/channel/UCabc` | `/channel/UCabc` | yes |

The daemon strips whitespace and trailing slashes and matches only a `UC` prefix; the CLI strips
neither and also matches `PL`. Docstring restated to describe what is true, and all three
divergences pinned by test so either side moving becomes a failure rather than a silent fork.
Which side is right is a product decision — **gh #65** — so neither normalization was changed.

**A test in the 344 passed having executed nothing.**
`test_notifier_integration.py::test_notifier_calls_terminal_notifier_subprocess` caught every
exception, assigned a bool in both branches, then asserted `isinstance(success, bool)` — true
either way. `terminal-notifier` is absent on the operator's Mac as well as the CI runner, so it had
never verified a notification fires, anywhere. Now gated on `shutil.which("terminal-notifier")`
with a stated reason and a handle, and the body asserts for real when the binary is present.
Converting a tautological pass into an honest skip is not the SC-12 prohibition — SC-12 forbids
skipping a test that *fails*, and this one passed.

**Three citations were wrong on arrival.** Two in `test_source_list_remove_unit.py` pointed at
pre-extraction line numbers; one named `api.py:227` for a function defined at 229. All three now
name the **symbol** rather than a line range — `find_source_by_id` moved from 108 to 117 during
this same round, which is exactly how a line number goes stale. Swept every new comment and
docstring in the round rather than fixing only the three: the remaining citations
(`constitution.md:42-43` and `:130-132`, `api_client.py:44`, `remote.py:24`, and four `verify.sh`
references in `ci.yml`) were each re-opened and land on their quoted text. Now a standing rule in
`CLAUDE.md`.

**CI actions were floating.** `ci.yml` argued that a floating version "makes the gate's result
depend on when it ran rather than on the code" and then used mutable `@v6`/`@v5` tags for
`setup-uv` and `setup-go` — a write surface into CI on a public repo, and a `setup-uv` bump can
change resolution under `uv sync --frozen`. All four actions are SHA-pinned with a trailing version
comment, each SHA resolved from the GitHub API at edit time rather than written from memory. This
lands before the first push, so the run that closes SC-6b judges the final file.

**A dead fixture.** `cli/tests/conftest.py`'s `cli_runner` had no consumer after the Bucket E
deletions; the plan directed such fixtures be deleted rather than left dead. Removed with its
now-unused `CliRunner` import.

### RESOLVED — the "intermittent" was a live third-party dependency (gh #64)

Identified and fixed 2026-09-04. It was never random.

`test_date_filtering_integration.py::test_network_timeout_graceful` fetched
`https://httpstat.us/200?sleep=5000` with a one-second fetcher timeout and asserted the request
would raise. That made the outcome a property of a third party's current behaviour: when
httpstat.us is slow the assertion holds, and when it answers promptly the test fails with

```
Failed: DID NOT RAISE <class 'Exception'>
```

Which is what it now does — httpstat.us returns an immediate HTML page, so the fetcher parses it
(`mismatched tag` from feedparser) and never times out. Reproduced 3/3, not intermittently.

**Two earlier sightings were the same test, and I mis-measured twice.** Both runs used `-rs`, which
prints only the skip summary, so the failing node id never reached the output and I recorded an
"unexplained intermittent" instead of a name. A run hunting a failure must use `-ra` or
`--tb=line`; `-rs` cannot report one. Recorded in the round notes above.

**Fixed at the cause, not gated.** A `slow_feed_url` fixture serves from `127.0.0.1` on an
ephemeral port and sleeps past the deadline, so the timeout is produced by the test rather than
observed from the network. This is the same repair already made to the Go suite's
`TestNetworkTimeoutRecovery`, which had the identical shape — a timeout assertion pointed at a live
host. The invariant is unchanged and now actually exercised: daemon **343 passed, 0 failed**, with
no network dependency in the path.
### CI

**SC-6 is NOT fully met, and was previously recorded as though it were.** Its first two clauses
are satisfied by reading the file: the job runs `bash .specify/verify.sh` (the same script, not a
reimplementation) and its install step walks the same self-discovery. The third clause — "the job
passes on green `main`" — is **unverified**, because nothing has been pushed. Local `main` is
`c11e34f`; `origin/main` is `e5b1149`. The plan's own gate for it (`plan.md:607`) returns:

```
$ gh run list --workflow=ci.yml --branch main --limit 1 --json conclusion
HTTP 404: workflow ci.yml not found on the default branch
```

The workflow has never executed. The first push closes this criterion, and the push is the
operator's call — this work order's status stays `awaiting-push` until a run on `main` reports
`success`.

`.github/workflows/ci.yml` (new). One `verify` job on `ubuntu-latest` running
`bash .specify/verify.sh` — the same script, no reimplementation. Its install step walks the same
`find` discovery the gate does (same exclusions), so a unit added later is gated with no list to
maintain. `staticcheck` is installed explicitly because `verify.sh:73` branches on
`command -v staticcheck` and would otherwise gate on strictly less than a local run while still
reporting PASS.

**Deviation from the plan:** it is pinned to `@v0.8.1`, not `@latest` as the plan specified. A
floating version makes the gate's verdict depend on *when* it ran rather than on the code — the
same ambient dependence this whole work order exists to remove, and it would falsify SC-9's claim
that green locally implies green in CI (local pins 0.8.1 via mise). Bumping it becomes a
deliberate, reviewable change. One job rather than one per language: `verify.sh` runs every unit in a single
invocation, so uv, Python and Go must all be present. Both lockfiles were regenerated for the new
dependencies and `uv sync --frozen` verified against them.

`Makefile:275` — `make test` now delegates to the gate. It previously ran pytest in daemon and cli
and skipped lint, typecheck and staticcheck entirely, so it reported green on a tree the gate
rejects.

### Lint configuration

Select, both units: `E4,E7,E9,F,B,ASYNC,BLE,ERA,RUF006,RUF012,RUF013,RUF100,ANN401,PGH,S110,S112`.

- `BLE001` is selected but **suppressed across every Python file** in both units by a
  `"**/*.py"` glob in `per-file-ignores`, pointing at **gh #58**. An earlier revision of this
  section described that as keeping the class "visible and scheduled, not silently off". That was
  false: a glob over every file means no ruff run reports the class, and a green `ruff check .`
  says nothing about it. Measured behind the ignore — **62 in daemon, 4 in cli, 66 total**, not the
  74 the plan projected (the gap is blind excepts removed incidentally by the B904 chaining and the
  dead-code deletions). Recoverable only by deleting the entry:
  `ruff check . --select BLE001 --config 'lint.per-file-ignores = {}'`. The deferral stands; the
  description of it was wrong and both pyproject comments now say so.
- The pre-existing `S101` ignores (daemon and cli) were **deleted**: `S101` is not in the select, so
  they referenced a disabled rule and were inert. `B008` stays and is now live under `B`.
- `cli/src/cli/report.py` carries a `B008` entry: `typer.Option()` in argument defaults is a
  required idiom, the same case as daemon's existing FastAPI `Depends()` entry.
- Five `ERA001` findings are prose comments the heuristic misreads as code; each carries an inline
  `# noqa: ERA001` naming why, rather than disabling the rule.
- `daemon/scripts/` is excluded from ruff — `model_playtest.py` is the operator's uncommitted work.

### `cli source` command coverage — resolved 2026-09-04

Deleting `cli/tests/integration/` left five of the six commands in `cli/src/cli/source.py` with no
coverage. The replacement test I first wrote patched `cli.source.APIClient` and asserted through
`CliRunner`, with a docstring claiming "APIClient is the true external boundary here".

**That claim was false, and the constitution had already ruled on it.** `APIClient` is
`cli/src/cli/api_client.py:13` — the CLI's own code. `constitution.md:42-43`: "Internal code is
never mocked, and no fake stands in for infra you could run for real." `constitution.md:130-132`
names the only permitted mock: "LLM providers are the true external boundary … the one collaborator
Principle I permits mocking, and the only one." Separate deployment is not a defence either —
`constitution.md:118-122` states the three components are separately deployed *eight lines above*
the rule requiring the HTTP API be exercised for real, so the constitution weighed that fact and
ruled anyway. Amending it on that basis would be editing a governing document to ratify a test
written against it (P28).

The claim originated in `plan.md` Bucket E, not in the test — and `plan.md:35` says the builder
reads the plan, not the work order, so leaving it there regenerates this exact test next time. Both
are now corrected, the plan at the origin.

**Fix: extract the decidable logic, call it directly, mock nothing.** This copies the pattern
already in the same file — `extract_name_from_url` (`source.py:15`) is pure, module-level, and
tested with zero mocks.

| command | disposition |
|---|---|
| `add` | `detect_and_normalize_source_url` extracted (`source.py:62`); 9 direct cases. The remaining `APIClient` call is **glue** — a one-line pass-through. |
| `remove` | `find_source_by_id` extracted (`source.py:108`); 5 direct cases including the substring case that would otherwise delete the wrong source. Body is **glue**. |
| `list` | `format_source_row` extracted (`source.py:121`); 10 direct cases including the exact-25 truncation boundary. Body is **glue**. |
| `pause` / `resume` / `edit` | **glue** — one `APIClient` call and a print each; no branch, no derivation, no state. |
| `extract_name_from_url` | already covered directly by `test_url_extraction.py`. |

All recorded with reasons in the plan's Constitution Check table. cli tests: **27 → 44**, still zero
mocks in the new files. `add()` behavior is unchanged — the extraction is a pure move.

Running these commands against a real daemon instead of leaving the bodies as glue is blocked on
**gh #63**: `api.py:425-428` constructs `SourceValidator()` inline with no `Depends()` seam, and
`validator.py:64,147` make live `httpx.get` calls, so `POST /api/sources` reaches the network on
every run — and gh #59 means `reddit://` cases fail today regardless.

### Not done / operator's call

- **The exposed API key is still in git history.** 9 commits on `origin/main`, earliest 2025-09-09.
  Rotation is a decision against the operator's own threat model, not mine to make.
- **Six untracked local files still contain the key on disk.** Sweeping the whole working tree
  (not just the test files the plan enumerated) found the key in `.workflow/archives/iteration-8,9/`
  and `.sable/archive/iteration-11,12/` logs, plus two `daemon/.ruff_cache/` entries. All
  **untracked** — `git ls-files` confirms **0 tracked files** contain it — so nothing new is
  committed, but they are on the operator's disk.
- **gh #59's validator fix** — a runtime behavior change, deliberately not made here.
- **Live-LLM paths no longer run locally by default.** They previously ran because the deleted
  `.env` load injected the operator's key, making real paid API calls on every `make test`. Export
  `OPENAI_API_KEY` / `PRISMIS_LIVE_LLM_TESTS=1` to run them. This is local converging on CI, which is
  the intended direction.
