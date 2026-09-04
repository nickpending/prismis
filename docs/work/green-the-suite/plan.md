# Plan — green the suite (`wo-green-the-suite`)

Work order: `/Users/rudy/development/projects/prismis/docs/work/wo-green-the-suite.md`
Goal: `./.specify/verify.sh` exits 0, then CI runs that same script.

**Status: READY.** The one blocking decision is resolved below.

### Resolved 2026-09-03 — daemon pyright baseline

**Decision: fix the 18 daemon pyright errors inside this work order (option A).**

Measured this session (mason had no execution tool): `cd daemon && uv run pyright` → exactly
**18 errors, 0 warnings** — matching `decisions.md:78`'s recorded baseline precisely, so that
number is current rather than stale.

All 18 are annotation-level, none behavior-changing:
`reportArgumentType` ×11, `reportAttributeAccessIssue` ×4, `reportReturnType` ×2,
`reportInvalidTypeForm` ×1.

Six of them ARE the ambient-config defect this work order exists to fix — `config: Config = None`
in `fetchers/{rss,reddit,youtube}.py` where the honest annotation is `Config | None`. They sit in
the same code Step 2 already touches.

`decisions.md:78` says the baseline lives in `quality.md ## Learned Patterns` as a regression
yardstick. **`quality.md` does not exist in this repo** — the yardstick pointed at nothing. Retire
the baseline concept with this fix rather than repointing it.

The gate (`verify.sh:48-49`) runs pyright under `run_step` and fails on non-zero exit, so SC-1 is
unreachable while these exist, and the gate is not weakened to accommodate them.

---

## Settled — do not re-open

These are constraints carried into this plan. The builder reads this plan and not the work order,
so treat every line here as a decision already made with reasons already given.

- `.specify/verify.sh` is never weakened to reach green. Fix the suite, not the gate. This plan
  edits no line of it. (One latent discovery bug in it is recorded under `## Concerns`, to be sent
  upstream — not fixed here.)
- The config isolation must NOT monkeypatch `Config.from_file`. That neuters the INV-003 negative
  test at `daemon/tests/unit/test_dual_service_config_unit.py:198-208`, which requires a real
  outdated config to raise. Isolate the ENVIRONMENT.
- `BLE001` (74 findings) is OUT OF SCOPE. It stays in ruff's `select` and carries a
  `per-file-ignores` entry pointing at https://github.com/nickpending/prismis/issues/58. Do not
  narrow blind excepts in this work order.
- `daemon/scripts/model_playtest.py` is the operator's uncommitted work — do not modify. Add
  `daemon/scripts/` to ruff's `exclude` instead.
- `cli/src/cli/analyze.py` and `.gitignore` are the operator's uncommitted work — leave them.
- Lint select, settled: `E4,E7,E9,F,B,ASYNC,BLE,ERA,RUF006,RUF012,RUF013,RUF100,ANN401,PGH,S110,S112`.
  93 in-scope findings (78 daemon + 15 cli) once BLE001 is ignored.
- `cli` gets pyright, not mypy — matching `daemon/pyproject.toml:45-50`.
- A deleted test is deleted because the behavior it guarded is gone, with that reason stated
  (SC-12). "It fails and I don't know why" is not a rationale.
- Pre-existing `skipif` marks and the documented `xfail` are NOT skips added to reach green and
  must survive: `daemon/tests/unit/test_rfc3339_helper_unit.py:298` (two other files assert it must
  remain — `test_content_response_api_integration.py:10`, `test_content_response_model_unit.py:12`),
  and the `OPENAI_API_KEY` / services.toml `skipif`s at
  `test_content_aware_summarization_integration.py:13,50,169`,
  `test_summarizer_evaluator_integration.py:9,92,164`,
  `test_llm_startup_validation_integration.py:106,108,206,208`.

### Open Gaps (known unknowns — not work items)

- No count in the work order was measured by this plan; the planner had no execution tool. Every
  number below is attributed and must be re-measured before it is trusted.
- The `tui/internal/ui` failures are undiagnosed. `go test ./...` was not run here and no
  compile-level break was found by reading. Step 7 is a measure-then-partition step, not a fix list.
- Live-network dependence of the daemon integration suite is unsized. `test_validator_integration.py`
  hits real Reddit/RSS endpoints and already carries a "not private anymore" skip at line 166;
  `test_date_filtering_integration.py:65` skips on network error. Whether CI can be green
  deterministically against those is unknown until Step 8's fresh-environment run.

---

## Sequencing (each step's measurement is invalid until the previous one lands)

1. **Step 1 — toolchain declared** (SC-7). `uv run ruff` currently fails on a missing binary, so
   `verify.sh:43` fails for a reason unrelated to code, and cli pyright has never executed. No lint
   or type count means anything before this.
2. **Step 2 — environment isolation** (SC-8). The suite's failure count is a property of the
   developer's `$HOME`. Until it is sealed, every bucket count is unreadable.
3. **Step 3 — re-measure.** Produce the real baseline under the sealed environment. It will match
   neither 113 nor 153.
4. **Steps 4-7 — buckets** (SC-2, SC-4, SC-10, SC-12): the named root causes, then the residue.
5. **Step 8 — fresh-environment proof** (SC-9). Local evidence that CI will be green.
6. **Step 9 — CI** (SC-6, SC-11), last, on a green tree.

---

## Files to modify

### Step 1 — declare the toolchain (SC-7)

**`daemon/pyproject.toml`**
- `[dependency-groups] dev` (line 34-39): add `"ruff"`. `verify.sh:43` runs `uv run --quiet ruff
  check .`; `uv run` resolves from the dev group, and ruff is currently absent from both
  pyprojects despite both carrying ruff config (`daemon/pyproject.toml:41`, `cli/pyproject.toml:28`).
- Add `[tool.ruff.lint]` with the settled `select`, and `[tool.ruff] exclude = ["scripts"]`.
- Extend `[tool.ruff.lint.per-file-ignores]` (line 41-43) with a `BLE001` entry naming
  https://github.com/nickpending/prismis/issues/58. The existing `B008` and `S101` ignores become
  live once `B` and `S110/S112` are selected — `S101` is only meaningful if an `S` rule that flags
  asserts is enabled; `S101` is not in the settled select, so state in the diff whether that entry
  stays as documentation or goes. Do not silently leave an inert ignore (SC-5's third clause).

**`cli/pyproject.toml`**
- `[dependency-groups] dev` (line 21-26): add `"ruff"` and `"pyright>=1.1.409"` (pin matching
  `daemon/pyproject.toml:36`).
- Add `[tool.ruff.lint] select = ...` (same list) and the same `BLE001` per-file-ignores entry.
- Add `[tool.pyright]` mirroring `daemon/pyproject.toml:45-50`:
  `include = ["src/cli"]`, `exclude = ["**/__pycache__", "**/.venv", "**/node_modules"]`,
  `pythonVersion = "3.13"`, `typeCheckingMode = "basic"`, `reportMissingImports = "warning"`.
  `verify.sh:48` greps for the literal `[tool.pyright]`, so the section header must be exactly that.

Then measure, in this order, and record the numbers in the work order's `## Outputs`:
```
cd daemon && uv run ruff check . ; uv run pyright
cd cli    && uv run ruff check . ; uv run pyright
```

### Step 2 — seal the test environment (SC-8, SC-2)

**`daemon/tests/conftest.py`** — this is the load-bearing edit.

1. **Delete lines 11-15** (the import-time `load_dotenv` of `$XDG_CONFIG_HOME/prismis/.env`). It
   executes before any fixture can run, so it cannot be isolated, and it is the same ambient leak
   as the config: locally it injects the operator's `OPENAI_API_KEY` and turns the `skipif`-gated
   live-LLM tests ON, while CI runs with them off. Sealing the config and leaving this in place
   leaves the suite still reading `$HOME`.

2. **Add the seal** — one autouse function-scoped fixture. Shape:

   ```python
   TEST_API_KEY = "prismis-test-key"

   @pytest.fixture(autouse=True)
   def isolated_xdg_env(tmp_path_factory, monkeypatch) -> Path:
       """Seal the suite from the developer's XDG dirs.

       Production resolves config/data/state from the environment at call time
       (config.py:182-185, database.py:29,107, locking.py:13), including in the
       subprocess at test_daemon_integration.py:99-105 — which only an env-level
       seal can reach.
       """
   ```
   It must set `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_STATE_HOME`, `XDG_CACHE_HOME` and `HOME`
   to fresh dirs under one `tmp_path_factory.mktemp("xdg")` root, then materialize a valid config:

   ```python
   cfg_dir.joinpath("config.toml").write_text(DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY))
   cfg_dir.joinpath("context.md").write_text(DEFAULT_CONTEXT_MD)
   ```

   `DEFAULT_CONFIG_TOML` is `daemon/src/prismis_daemon/defaults.py:7-69` — the production template,
   already complete against every required field in `Config.from_file` (`config.py:243-283`) and
   already passing `validate()` (`config.py:93-166`): `max_items` 25/50/10/1 in range,
   `fetch_interval` 30, `max_days_lookback` 30, `reddit.max_comments` 5,
   `[archival.windows]` medium/low set with `high_read` omitted → `None`, `[context]` all four keys
   in range. Do not author a second template; a hand-written one drifts from the production one and
   the drift surfaces as a test failure nobody can place.

   Use the template rather than calling `ensure_config()` (`defaults.py:101`) for one reason: that
   function generates a random `api_key` (`defaults.py:127`), and the suite needs a deterministic
   one — see item 4.

   **Ordering, which the builder must not invert:** `test_db` (line 33-56) sets `XDG_DATA_HOME` to
   its own temp dir at line 42. pytest instantiates autouse fixtures before non-autouse ones at the
   same scope, so `test_db`'s value wins for tests that request it. That is correct and must stay.
   Likewise, tests that set their own `XDG_CONFIG_HOME` — `test_dual_service_config_unit.py:225`,
   `test_llm_core_migration_unit.py:252,313` — run after the autouse fixture and override it. The
   INV-003 negative test passes an explicit path (`Config.from_file(config_path)`) and is untouched
   by any of this.

3. **Delete the two `except Exception: pytest.skip(...)` fallbacks** (lines 62-67 and 73-78).
   Read what they were actually hiding before deleting: `load_config()` (line 29-30) returns a
   `Config` **dataclass**, and `llm_config` calls `config.get("llm", {})` at line 64 — `Config`
   (config.py:11-72) has no `.get`. That fixture raises `AttributeError` on **every** machine,
   including one with a perfectly good config. The "17 skipped" are not "config file not found";
   they are this bug wearing a skip's clothes, which is the constitution's Quality Gates violation
   ("a gate that passes having executed nothing").
   Removing the fallbacks **exposes** the rot beneath it. Expect these to surface as failures and
   route them through Step 6:
   - `test_summarizer_evaluator_integration.py:222,231-232` passes the `llm_config` dict to
     `ContentSummarizer(...)`, which takes a service-name string (`__main__.py:67`).
   - `test_summarizer_evaluator_integration.py:251` subscripts a dataclass: `full_config["context"]`.
   - `test_context_api.py:132-135` reads `full_config.llm_provider/.llm_model/.llm_api_key/
     .llm_api_base` — none of which exist on `Config` post-rename (`decisions.md:223`).

4. **Add an `api_key` fixture returning `TEST_API_KEY`** and replace every hardcoded key. **31
   occurrences across 10 daemon test files hardcode the operator's live API key
   `<THE-LIVE-KEY>`** — `test_api_integration.py` (10), `test_api_entries_invariants.py` (8),
   `test_favorites_cascade.py` (3), `test_audio_api_integration.py` (3),
   `test_deep_extractor_api_integration.py` (2), `test_extract_endpoint_race.py:60`,
   `test_context_assistant.py`, `test_favorites_invariants.py`,
   `test_search_min_score_integration.py`, `test_content_response_api_integration.py` (1 each).
   `auth.py:31` compares the request header against the ambient config's key, which is why these
   tests pass on exactly one machine. This is a committed secret — the constitution's Security
   Requirements ("secrets live in env/config, never inline — no exception for test or throwaway
   code") and P8 make its removal non-optional, and SC-8 cannot pass while it stands.
   Follow the pattern already in the newest file: a single module constant
   (`test_extract_endpoint_race.py:60`), sourced from the conftest fixture rather than a literal.
   Verify the sweep is complete by grepping the tree for the live key value read from
   `~/.config/prismis/config.toml` — never by writing the literal into this document or any
   other tracked file.

**`cli/tests/conftest.py`**
- `mock_home_dir` (line 41-57) patches `Path.home()` (line 55) but not `XDG_CONFIG_HOME`.
  `cli/src/cli/api_client.py:44` and `cli/src/cli/remote.py:24` both read `XDG_CONFIG_HOME` first
  and only fall back to `Path.home()`, so the patch misses the primary path. Add the same env seal
  (autouse) here: `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `HOME`.
- Note `mock_home_dir`'s only consumers are the two integration files deleted in Step 6; if nothing
  else uses it after that, delete the fixture with them rather than leaving a dead one.

### Step 3 — re-measure (no file changes)

```
cd daemon && uv run pytest -q --no-header 2>&1 | tail -5
cd cli    && uv run pytest -q --no-header 2>&1 | tail -5
```
Capture the **list** of failing node ids, not just the count (`--tb=no -q | grep FAILED | sort`).
The counts in the work order (113 / 153) both describe an unsealed tree and neither predicts the
sealed one. Diffing the failure *set* against the pre-seal set is the only way to see which tests
the seal fixed and which it newly broke — see the swing risk below.

### Step 4 — the three named genuine defects (SC-4)

- **`daemon/src/prismis_daemon/__main__.py:212`** — `asyncio.create_task(api_server.serve())` holds
  no reference; the loop keeps only a weak one and the API server task can be collected mid-flight.
  Bind it (`api_task = asyncio.create_task(...)`) and keep the reference alive for the process
  lifetime — the shutdown sequence at lines 218-224 is where it belongs.
- **`daemon/tests/integration/test_config_integration.py:132`** — `F821` undefined `load_config`.
- **`daemon/tests/integration/test_config_integration.py:191`** — `F821` undefined `config_path`.
  Both sit inside tests that Step 6 deletes wholesale; deleting the file closes the F821s. Confirm
  with the SC-4 gate rather than assuming.

### Step 5 — `pytest.ini` (SC-10)

**`daemon/pytest.ini`** — three changes:
- `[tool:pytest]` → `[pytest]`. The former is the `setup.cfg` section name; in a `pytest.ini` it is
  inert, so `testpaths`, `python_classes`, `python_functions`, `python_files` and `addopts` have
  never taken effect.
- `python_paths = src` → `pythonpath = src`. `python_paths` is a dead plugin option; `pythonpath`
  is core pytest's.
- `addopts = -v --tb=short` → `addopts = --tb=short`. Once the section is live, `-v` fights the
  `-q` that both `verify.sh:57` and SC-8's verification pass on the command line. The caller owns
  verbosity.

Then settle the SC-10 clause: run `uv run pytest --collect-only -q | tail -1` before and after
removing `conftest.py:17-19`'s manual `sys.path.insert`. If the collected count is identical, the
insert is redundant — delete it and say so in `## Outputs`. If collection breaks, restore it and
state why. Do not leave it ambiguous.

### Step 6 — the failure buckets

Partition every failure into exactly one of: **real regression** (fix the code), **rotted test**
(fix the test, citing the intentional change), **superseded** (delete with an SC-12 line). The
buckets below are diagnosed and grounded; the residue from Step 3 gets the same treatment.

**Bucket A — `too many values to unpack (expected 2)` → rotted tests, fix the tests.**
`SourceValidator.validate_source` returns a 3-tuple (`validator.py:22-24`), as do all four
per-type validators (`validator.py:51,108,196,255`). The 3-tuple is the intended contract:
`docs/architecture/components.md:124` documents it, `api.py:425` and `api.py:523` destructure three,
and `daemon/tests/unit/test_validator_youtube_protocol_unit.py` exists specifically to protect it
(gh #27). The 2-tuple unpacks in `test_validator_integration.py:16,23,35,48,58,68,89,111,132,154`
are the rot. Fix them to 3-tuple. While there: `validator.py:117`'s docstring still says
"Tuple of (is_valid, error_message)" against a 3-tuple signature — correct it.

**Bucket B — `ModuleNotFoundError` → a fourth shape of the already-closed import class.**
The work order calls this "dynamic `importlib` resolution"; that is wrong —
`rg -n importlib daemon/tests` returns nothing. It is in-function flat imports that the three
closing commits missed because they were not at module level:
`test_config_integration.py:18,19,78,79,167` (`import config`, `import defaults`) and
`test_daemon_integration.py:22-24,178-180,285-287` (`from fetchers.rss import RSSFetcher` etc.).
Six test functions, two files. `test_daemon_integration.py`'s get the package prefix
(`from prismis_daemon.fetchers.rss import RSSFetcher`); `test_config_integration.py` is deleted
outright — see Bucket D.

**Bucket C — `FileNotFoundError` → hardcoded absolute paths, CI-fatal.**
- `test_daemon_integration.py:99-105` runs a subprocess with
  `cwd="/Users/rudy/development/projects/prismis/daemon"` and `-m src`. Both are wrong: derive the
  cwd from `Path(__file__)` and invoke `-m prismis_daemon` (the package's entry point, per
  `daemon/pyproject.toml:29`).
- `test_content_response_model_unit.py:207-208` asserts against
  `/Users/rudy/obsidian/projects/prismis/architecture/boundaries.md` — outside the repo. The
  in-repo copy at `docs/architecture/boundaries.md:46` contains all three asserted strings
  (`INV-API-TS-4`, `Pydantic`, `ContentResponse`), verified. Repoint to
  `Path(__file__).parents[3] / "docs" / "architecture" / "boundaries.md"`, matching the
  `Path(__file__).parent.parent.parent / "src" / ...` idiom already used at lines 150 and 255 of
  the same file.
These two are the class "test depends on a path outside the repo". Sweep:
`rg -n '/Users/rudy' --glob '!**/.venv/**'` — the only remaining hits must be under `docs/`.

**Bucket D — superseded daemon tests (SC-12 deletions).**
`daemon/tests/integration/test_config_integration.py` tests a config API that is gone: dict-style
`result["daemon"]["fetch_interval"]` (line 135), `llm_provider`/`llm_model`/`llm_api_key`
(139-141), and a "missing files fall back to defaults" contract that `config.py:188-192` replaced
with a hard `FileNotFoundError`. `daemon/tests/unit/test_config_unit.py:44-47,71-74,110-111`
asserts the same dead fields. The rename is recorded as deliberate at `docs/architecture/
decisions.md:223` ("Clean rename. `Config.llm_service` is absent post-task-1.1"). Delete as
`SUPERSEDED`, one line per removed `def test_`, citing that decision. Do not delete the INV-003
tests in `test_dual_service_config_unit.py` — they are the surviving coverage for the same area.

**Bucket E — superseded cli tests (SC-12 deletions + replacement coverage).**
`cli/tests/integration/test_source_validation.py` and `test_source_commands.py` (the 4 + 12) test
a CLI that validated locally and wrote SQLite directly. That CLI is gone: `cli/src/cli/source.py`
imports only `APIClient` (line 9) and adds through `api_client.add_source(...)` (line 115). The
patch targets in those tests do not exist — `cli.source.SourceValidator` (line 93) and
`cli.source.VALIDATOR_AVAILABLE` (line 144); `rg -n 'VALIDATOR_AVAILABLE|SourceValidator' cli/src`
returns nothing. They also hit the live network
(`https://this-host-does-not-exist-999.com/feed`, line 58), which Principle IV rules out for a
CI-run gate. Delete both files as `SUPERSEDED`.
**Replace the invariant they guarded, do not just drop it.** The stated invariant — "Source type
MUST be correctly identified" (line 89) — is live logic at `cli/src/cli/source.py:74-104`. Extract
that URL→type branch into a module-level pure function returning `(source_type, url)`, a sibling of
`extract_name_from_url` (`source.py:15`) which is already pure, module-level and tested directly
with zero mocks — that is the pattern to copy, in the same file. Mirror the daemon's vocabulary
(`normalize_source_url`, `api.py:227`). Then add `cli/tests/unit/test_source_command_unit.py`
calling that function **directly**, asserting `(source_type, url)` for `reddit://`,
`https://reddit.com/r/x`, `youtube://@h`, `https://youtube.com/@h`, `*.md`, and a plain feed URL.

**CORRECTED 2026-09-04 — do not patch `APIClient`.** An earlier revision of this section said
"Patching `APIClient` at the module boundary is the true external boundary here — it is the HTTP hop
to a separately deployed daemon." That is wrong twice, and it propagated into the test's own
docstring before being caught:

1. `APIClient` is `cli/src/cli/api_client.py:13` — **the CLI's own code**, not a third party.
   `constitution.md:42-43`: "Internal code is never mocked, and no fake stands in for infra you
   could run for real." The daemon is infra you can run.
2. `constitution.md:130-132` names the permitted mock exactly: "**LLM providers are the true
   external boundary.** They are the one collaborator Principle I permits mocking, and the only
   one. The database, the HTTP API, and every internal module are exercised for real."

Separate deployment is not a justification either — `constitution.md:118-122` already states the
three components are separately deployed, eight lines above the rule requiring the HTTP API be
exercised for real. The constitution weighed that fact and ruled anyway, so amending it on that
basis would be editing a governing document to ratify a test written against it (P28).

Extracting the pure function removes the need for any mock: the decidable logic is called directly,
and the `APIClient` call that remains in `add()` is a one-line pass-through recorded as glue.

**Bucket F — `cli/tests/unit/test_url_extraction.py` → rotted test, fix the expectations.**
One function fails: `test_extract_name_from_youtube_urls`. Traced by hand against
`cli/src/cli/source.py:46-56`: `https://youtube.com/@mkbhd` returns `"@mkbhd"`, and
`https://youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA` returns `"UC9-y-6csu5WGm29I7Ji"` (the
truncated id, no prefix). The test expects `"YouTube: @mkbhd"` (line 59) and
`"YouTube Channel: UC9-y-6csu5WGm29I7Ji"` (line 68). The code is right and the test is stale: the
daemon API — the authority, since it names the source on the server side — produces exactly the
code's output at `api.py:294-303`, and `source.py:47` says so ("matching API behavior"). Update
lines 59, 62, 68, 72 and add a docstring line pointing at `api.py:294-303` as the contract.
Do not "fix" the code to match the test; that would fork the CLI's proposed name from the API's.

### Step 7 — tui (SC-11 and the `internal/ui` failures)

Not diagnosable by reading; measure first.
```
cd tui && go test ./... 2>&1 | tail -40
cd tui && staticcheck ./...
```
Partition the `internal/ui` failures with the same three categories. `staticcheck` findings: fix,
or give each remaining one a tracked handle (SC-11).

### Step 8 — fresh-environment proof (SC-9)

Run SC-9's verification verbatim before writing any CI. It is the criterion most likely to fail
first, because `env -i` strips everything the interactive shell supplies — and Principle IV names
that as the thing verification must not depend on. Anything it surfaces is a Step 6 item, not a
CI problem.

### Step 9 — CI (SC-6)

**`.github/workflows/ci.yml`** (new). Model: `/Users/rudy/development/projects/bench/.github/
workflows/ci.yml`, whose install step walks the same self-discovery the gate does (lines 28-35) —
a hand-maintained matrix is the failure this is written to avoid. Structure:

- `on: push: branches: [main]` + `pull_request` (bench lines 3-6).
- One `verify` job on `ubuntu-latest`.
- Install step, self-discovering, mirroring `verify.sh:61-63` and `verify.sh:79`:
  ```bash
  while IFS= read -r pp; do (cd "$(dirname "$pp")" && uv sync --frozen); done \
    < <(find . -name pyproject.toml -not -path '*/.venv/*' -not -path '*/build/*' \
         -not -path '*/dist/*' -not -path '*/node_modules/*' | sort)
  while IFS= read -r gm; do (cd "$(dirname "$gm")" && go mod download); done \
    < <(find . -name go.mod -not -path '*/vendor/*' | sort)
  ```
  Both `daemon/uv.lock` and `cli/uv.lock` exist, so `--frozen` is available.
- Install `staticcheck` explicitly (`go install honnef.co/go/tools/cmd/staticcheck@latest`) —
  `verify.sh:73` branches on `command -v staticcheck` and silently drops the check where it is
  absent, so a default image would gate on strictly less than the local run. SC-11 requires it
  present.
- `- name: Verify` → `run: bash .specify/verify.sh`. The same script. No reimplementation.
- Header comment: why self-discovery (a hand-listed unit set silently omits a unit), and why
  staticcheck is installed explicitly.

**`Makefile:274-281`** — `make test` runs pytest directly in daemon and cli and skips lint,
typecheck and staticcheck entirely, so it will report green on a tree the gate rejects. Point it at
the gate:
```make
test: ## Run the full verification gate (same script CI runs)
	bash .specify/verify.sh
```
One gate, one command, three callers (developer, `make test`, CI).

---

## Alternatives Considered

### A. How the config isolation is done

**A1 — autouse fixture in `daemon/tests/conftest.py` sealing the XDG env vars (CHOSEN).**
- Reaches the subprocess. `test_daemon_integration.py:99-105` spawns a child with
  `env={**os.environ, ...}`; `monkeypatch.setenv` mutates `os.environ`, so the child inherits the
  seal. No in-process patch can do this.
- Reaches every reader without enumerating them. The ambient readers are
  `config.py:183`, `defaults.py:113`, `__main__.py:32,454`, `context_auto_updater.py:48`,
  `database.py:29,107`, `api.py:1353,1558`, `observability.py:22`, `locking.py:13`,
  `audio.py:267` — eleven sites across seven modules, plus two in the cli. Sealing four env vars
  and `HOME` covers all of them and covers ones added later.
- Leaves the INV-003 negative test intact: it passes an explicit path
  (`test_dual_service_config_unit.py:206-208`), which never consults the environment.
- Cost: it is a hidden global. A test that wants the real environment cannot have it, and the
  override mechanism (set your own env var after the fixture runs) is implicit ordering knowledge.
  Three existing tests already rely on exactly that ordering and keep working
  (`test_dual_service_config_unit.py:225`, `test_llm_core_migration_unit.py:252,313`).

**A2 — a pytest plugin (`-p` module or a rootdir `conftest.py`).**
Same mechanism, wider blast radius: one seal for daemon and cli both. Rejected because there is no
rootdir shared by the two units — `verify.sh` runs each unit with `env -C "$d"`, so daemon and cli
each have their own rootdir and a repo-root conftest would not be loaded. It also introduces a new
top-level file whose only job is to be found, which Principle VII (use existing primitives) argues
against when a conftest already exists in both places.

**A3 — per-test fixtures, requested explicitly by the tests that need config.**
Honest about which tests depend on config, and no hidden global. Rejected: the work order measured
~80 failures from this cause, and SC-2 explicitly forbids "80 individual edits". Worse, it is
unsound — the dependency is not a property of the test but of any code path it happens to reach
(`auth.py:31` fires on any authenticated API request), so the list of tests needing the fixture is
not knowable from the tests. A seal that must be remembered is a seal that leaks.

**A4 — monkeypatch `Config.from_file`'s default path.**
Rejected by constraint, and correctly: `test_config_old_service_key_rejected`
(`test_dual_service_config_unit.py:198-208`) requires a real outdated config to raise, and INV-003
is the invariant the work order says must survive.

**Why the production template rather than `ensure_config()`:** `ensure_config()` (`defaults.py:101`)
is the natural reuse and the suite already calls it in five places
(`test_daemon_integration.py:33,122,142,189,296`) — but it generates a random api key
(`defaults.py:127`), and the suite needs a fixed one to replace the 31 hardcoded literals.
Formatting `DEFAULT_CONFIG_TOML` with a known key keeps the template as the single source of the
config's shape while making the key deterministic. `ensure_config()`'s own calls inside tests
continue to work — they now write into the sealed dir and find the file already present.

### B. What `make test` becomes

**B1 — `make test` delegates to `.specify/verify.sh` (CHOSEN).** One definition of "tested".
Costs: a developer wanting one unit's tests loses the shortcut and runs
`cd daemon && uv run pytest` directly; the target gets slower (it now lints and typechecks).

**B2 — keep `make test` as-is and add a separate `make verify`.** Faster inner loop, no behavior
change for anyone's muscle memory. Rejected: two commands that both answer "are the tests
passing?" will disagree, and the work order exists because a green-looking signal hid twelve
unexecuted files for months.

---

## Pick Justification

- **P16 (root cause over symptom)** and **SC-2's own wording** drive A1: the cause is the process
  environment, and the fix is at the environment. Editing 80 tests treats the symptom.
- **B5 (check the claim, not its neighbor)** rules out every in-process variant of A1: the claim is
  "the suite is independent of the ambient config", and the subprocess at
  `test_daemon_integration.py:99-105` makes that claim false for any patch that lives inside the
  pytest process. The check has to be about the same noun as the claim.
- **P15 (existing patterns)** picks the mechanism inside A1: `conftest.py:42` already isolates via
  `monkeypatch.setenv`, `defaults.py:7` already holds the config template, and
  `test_extract_endpoint_race.py:60` already establishes the one-constant-per-module key pattern.
  Nothing new is invented; one existing pattern is moved one level up.
- **P19 (generalize the instance)** sets the scope of Step 2 and Bucket C: the class is "the test
  process reads the developer's `$HOME`", and its members were enumerated by sweep, not memory —
  `XDG_CONFIG_HOME` (config), `XDG_DATA_HOME`, `XDG_STATE_HOME`, the import-time `.env` load, and
  two hardcoded absolute paths. Sealing only the config would leave four live siblings.
- **P8 / Constitution Security Requirements** make the `<THE-LIVE-KEY>` removal non-deferrable —
  "no exception for test or throwaway code". It is also load-bearing for SC-8: a test authenticating
  with the operator's real key cannot produce identical results in any other environment.
- **Constitution Principle IV (Verified Where It Runs)** is what SC-8 and SC-9 encode, and it is why
  the `.env` load goes and why CI installs `staticcheck` explicitly: anything the interactive shell
  supplies is absent in CI and must not be depended on.
- **Constitution Quality Gates** ("a gate that passes having executed nothing fails this
  constitution whatever its exit code") is why the two `pytest.skip` fallbacks are deleted rather
  than repaired in place — they converted an `AttributeError` into a pass.
- **Constitution Principle VII (Simplicity & YAGNI)** rejects A2's new top-level plugin and the
  hand-authored config template: both are parallel mechanisms displacing something that exists.
- **P3 (reversibility)** breaks the remaining tie toward one fixture in one file: it is deletable in
  a single diff if it proves wrong.

---

## Success criteria → plan step

| SC | Satisfied by | Note |
|---|---|---|
| SC-1 | All steps; verified by the gate itself | Blocked on the pyright gap below |
| SC-2 | Step 2 (autouse seal, not 80 edits, not a `from_file` patch) | INV-003 preserved by explicit-path tests |
| SC-3 | Superseded by SC-12 — no separate work | Stated so the builder does not plan to both |
| SC-4 | Step 4 (`__main__.py:212` RUF006; two F821s closed by Bucket D's deletion) | |
| SC-5 | Step 1 (select + per-file-ignores) then Steps 4/6 to zero | `S101`/`B008` ignore disposition stated in the diff |
| SC-6 | Step 9 (`.github/workflows/ci.yml`, self-discovering install, `bash .specify/verify.sh`) | Lands last |
| SC-7 | Step 1 (ruff in both dev groups; `[tool.pyright]` in cli) | |
| SC-8 | Step 2 (env seal + skip-fallback removal + api-key delinting) | |
| SC-9 | Step 8 (fresh `HOME`, `env -i`) | Run before Step 9 |
| SC-10 | Step 5 (`[pytest]`, `pythonpath`, `addopts`) + the stated `sys.path` finding | |
| SC-11 | Step 7 (staticcheck to zero or handles) + Step 9 (CI installs it) | |
| SC-12 | Step 6 Buckets D and E; one `- DELETED <file>::<name> — <CATEGORY>: <detail>` line per removed `def test_`, into the work order's `## Outputs` | Categories: `BEHAVIOR-GONE`, `SUPERSEDED`, `UNRECOVERABLE` |

---

## Risks

**HIGH — the ~40-test swing lands in a third world, not either measured one.**
The work order measured 113 failures with no config and 153 with a hostile or the operator's
config; ~40 tests pass in the first world and fail in the second. The seal creates a **third**
environment: a valid, complete, default config. Neither number predicts it. Tests that currently
pass *because* config loading raises — anything asserting a `FileNotFoundError` or an unconfigured
503 path (`test_deep_extractor_api_integration.py:391` describes exactly such a case) — flip to
failing when the config becomes valid. Others in the 153 flip to passing.
*Mitigation:* Step 3 captures the failing **set**, not the count, and diffs it against the pre-seal
set from both environments. Any test that newly fails is triaged as a rotted expectation about an
unconfigured environment and either re-pointed at an explicitly-empty config dir
(`monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))` with nothing in it — the override path is
already proven by three existing tests) or deleted with an SC-12 line. Do not treat a
newly-failing test as a regression introduced by the seal.

**HIGH — daemon pyright is unmeasured and may be a large hidden bucket.**
`verify.sh:48-49` runs `uv run pyright` under `run_step`, so SC-1 requires it to exit 0.
`docs/architecture/decisions.md:78` records an "18-error daemon-wide baseline … fixing pre-existing
items is welcome but not required". The work order's failure inventory has no pyright bucket at
all, so this is uncosted. The document that supposedly holds the baseline (`quality.md`) does not
exist in this repo — SEARCHED `docs/architecture/*.md`, only `architecture.md`, `boundaries.md`,
`components.md`, `decisions.md`, `manifest.md` — so the number is stale and unverifiable.
*Mitigation:* this is the blocking gap below. Measure `cd daemon && uv run pyright` as the very
first action of Step 1; the count decides the size of the answer.

**HIGH — cli lint/typecheck may land on `cli/src/cli/analyze.py`, which must not be modified.**
`[tool.pyright] include = ["src/cli"]` and `ruff check .` both cover it, and it is modified in the
working tree. If findings land there, the builder can neither fix them nor exclude them without
weakening a gate that was just declared.
*Mitigation:* measure per-file (`uv run ruff check src/cli/analyze.py`,
`uv run pyright src/cli/analyze.py`). If clean, no issue. If findings exist, check whether they are
in the committed content (`git show HEAD:cli/src/cli/analyze.py` vs the working copy) — findings in
committed content are ordinary in-scope work if the operator will commit or stash their changes.
**Escalate to the operator rather than deciding unilaterally**; do not add an exclude for a file the
operator is actively editing, and do not edit their working copy.

**MEDIUM — live-network tests make CI nondeterministic, and SC-8 demands identical counts.**
`test_validator_integration.py` resolves real Reddit/RSS URLs and already carries a
"r/lounge is not private/restricted anymore" skip (line 166);
`test_date_filtering_integration.py:65` skips on network error;
`test_host_binding_integration.py:213` skips when port 8989 is in use — which is *true on the
operator's machine when the daemon is running*. Any of these makes two runs differ.
*Mitigation:* run SC-8's verification twice in a row locally; a diff between two identical
invocations localizes the nondeterminism. Port 8989: run the SC-8 and SC-9 checks with the local
daemon stopped, and record that as a precondition. Network-dependent tests that cannot be made
deterministic get a tracked handle (P9), not a silent skip.

**MEDIUM — `verify.sh`'s JS/TS discovery does not exclude `.venv`.**
`verify.sh:101` excludes only `node_modules`, while the Python loop excludes `.venv`
(`verify.sh:62`), Go excludes `vendor` (line 79) and Rust excludes `target` (line 89). Today
`daemon/.venv/lib/python3.13/site-packages/pyright/dist/package.json` is discovered as a "unit";
it contains neither `"typecheck"` nor `"check"` and ships no `*.test.ts` (both verified), so no
step fires. A different pyright version — or any future venv package whose `package.json` contains
`"check"` — would make the gate try `bun run check` inside a vendored directory and fail
unfixably.
*Mitigation:* out of scope here (the gate is not edited in this work order). Recorded in
`## Concerns` for upstream.

**MEDIUM — CI install cost.** `daemon/pyproject.toml:21` depends on `sentence-transformers`, which
pulls torch; `llm-core` is a git source pinned at `rev=2eb4429` (line 61) and needs network at
resolve time. Expect a multi-minute install and a large image footprint.
*Mitigation:* `uv sync --frozen` against the committed lockfiles, plus `actions/cache` on
`~/.cache/uv` keyed by the lockfiles. If the job still exceeds a tolerable runtime, that is a
follow-up handle, not a reason to trim what the gate runs.

**LOW — removing the import-time `.env` load changes what runs locally.**
The `OPENAI_API_KEY`-gated tests (`test_summarizer_evaluator_integration.py:9,92,164` and
siblings) currently execute on the operator's machine because `conftest.py:11-15` injects the key,
and they make real paid LLM calls. After the change they skip unless the operator exports the key
in their shell. That is the intended direction — local converges on CI — but it means the local
run stops exercising those paths by default.
*Mitigation:* state it in `## Outputs`. It is a deliberate sacrifice, not a coverage loss to fix.

**LOW — a stale comment after the skip-fallback removal.**
`test_rfc3339_helper_unit.py:375` justifies its own `pytest.skip` as "P15: follow the conftest.py
pattern of pytest.skip for unavailable deps". Once conftest's fallbacks are gone that citation
points at nothing. Update the comment; the skip itself is legitimate and stays.

---

## Demo command

```bash
cd /Users/rudy/development/projects/prismis && ./.specify/verify.sh; echo "rc=$?"
```

Expected:
```
VERIFY_COVERED: ruff(cli),pyright(cli),pytest(cli),ruff(daemon),pyright(daemon),pytest(daemon),gofmt(tui),go vet(tui),staticcheck(tui),go test(tui)
verify: PASS
rc=0
```
No `FAILED:` lines, and no `VERIFY_UNCOVERED:` line — SC-7 requires the typecheck-missing notice to
be gone, and `staticcheck` must be on PATH or its own uncovered notice appears (`verify.sh:76`).
Unit order follows `find | sort`, so `cli` precedes `daemon`.

---

## Quality Gates

One runnable command per criterion. All paths relative to
`/Users/rudy/development/projects/prismis`.

| SC | Command | Pass condition |
|---|---|---|
| SC-1 | `./.specify/verify.sh >/tmp/v.out 2>&1; rc=$?; grep -q '^verify: PASS' /tmp/v.out && [ $rc -eq 0 ]` | exit 0 |
| SC-2 | `(cd daemon && uv run pytest -q --no-header 2>&1) \| grep -c 'Config \[llm\] section outdated'` | `0` |
| SC-4 | `(cd daemon && uv run ruff check --select F821,RUF006 .)` | `All checks passed!`, exit 0 |
| SC-5 | `(cd daemon && uv run ruff check .) && (cd cli && uv run ruff check .)` | both exit 0 |
| SC-6a | `grep -q 'bash .specify/verify.sh' .github/workflows/ci.yml && grep -q 'find . -name pyproject.toml' .github/workflows/ci.yml` | exit 0 |
| SC-6b | `gh run list --workflow=ci.yml --branch main --limit 1 --json conclusion -q '.[0].conclusion'` | `success` |
| SC-7 | `grep -q '"ruff"' daemon/pyproject.toml && grep -q '"ruff"' cli/pyproject.toml && grep -q '^\[tool.pyright\]' cli/pyproject.toml && ! (./.specify/verify.sh 2>&1 \| grep -q 'VERIFY_UNCOVERED')` | exit 0 |
| SC-8 | the work order's SC-8 script, verbatim (`wo-green-the-suite.md:264-276`) | `SC-8: PASS` |
| SC-9 | the work order's SC-9 script, verbatim (`wo-green-the-suite.md:285-293`) | `SC-9: PASS` |
| SC-10 | `python3 -c "import configparser,sys; c=configparser.ConfigParser(); c.read('daemon/pytest.ini'); s=c['pytest']; sys.exit(0 if 'pythonpath' in s and 'python_paths' not in s else 1)"` | exit 0 |
| SC-11 | `(cd tui && staticcheck ./...)` | no output, exit 0 |
| SC-12 | `git diff -U0 main -- '*test*.py' \| grep '^-def test_' \| sed 's/^-def \([A-Za-z0-9_]*\).*/\1/' \| while read -r t; do grep -q "DELETED .*::$t " docs/work/wo-green-the-suite.md \|\| echo "MISSING: $t"; done` | no output |
| type check | `(cd daemon && uv run pyright) && (cd cli && uv run pyright)` | `0 errors` both |
| lint | `(cd daemon && uv run ruff check .) && (cd cli && uv run ruff check .)` | exit 0 both |
| tests | `(cd daemon && uv run pytest -q) && (cd cli && uv run pytest -q) && (cd tui && go test ./...)` | exit 0 all three |
| go | `(cd tui && gofmt -l . \| tee /dev/stderr \| wc -l \| grep -qx ' *0') && (cd tui && go vet ./...)` | exit 0 |

SC-3 has no gate: it is superseded by SC-12 (`wo-green-the-suite.md:324-325`).

---

## Constitution Check — Principle I

| Module (path) | logic \| glue | Test that proves it, or why glue |
|---|---|---|
| `daemon/tests/conftest.py` | glue | Test fixtures. Holds env wiring and the production config template rendered with a fixed key; nothing decidable independent of what the tests it feeds assert. Proved by the SC-8 gate: identical pass/fail/skip counts under two hostile `$XDG_CONFIG_HOME` values. |
| `cli/tests/conftest.py` | glue | Same; proved by `cd cli && uv run pytest -q` passing under SC-9's `env -i` run. |
| `daemon/src/prismis_daemon/__main__.py` (RUF006 fix at line 212) | logic | The dropped-task-reference defect is statically decidable and enforced on every run by the SC-4 gate `uv run ruff check --select RUF006 .` → zero findings. A runtime GC test would be nondeterministic and would prove less. |
| `cli/src/cli/source.py::detect_and_normalize_source_url` (extracted) | **logic** | The URL→type/normalization branch, pulled out of `add()` so it is callable without any collaborator. Proved directly by `test_source_command_unit.py` — 9 cases, zero mocks. Must agree with the daemon's `normalize_source_url` (`api.py:227`) or a source is fetched by the wrong fetcher. |
| `cli/src/cli/source.py::find_source_by_id` (extracted) | **logic** | `remove` is destructive and cascades to all content from the source; this is what decides whether it deletes at all. Proved by `test_source_list_remove_unit.py` — match, absent, empty, duplicate-id, and the substring case that would otherwise delete the wrong source. |
| `cli/src/cli/source.py::format_source_row` (extracted) | **logic** | Name truncation at 25→22+`…`, the zero-errors `—` rendering, and timestamp truncation are all decidable. Proved by `test_source_list_remove_unit.py`, including the exact-25 boundary. |
| `cli/tests/unit/test_source_command_unit.py` (new) | logic (it is the test) | Proves the URL → `(source_type, url)` mapping for every branch; replaces the invariant deleted with `test_source_validation.py`. Calls the function directly — **no `APIClient` patch**, see the corrected Bucket E above. |
| `cli/tests/unit/test_source_list_remove_unit.py` (new) | logic (it is the test) | Proves `find_source_by_id` and `format_source_row`; restores coverage for `remove` and `list`, which the Bucket E deletions left at zero. |
| `cli/src/cli/source.py::add` — the `APIClient` call | **glue** | After the extraction, `add()` is `source_type, url = detect_and_normalize_source_url(url)`, an optional name derivation, then a one-line `api_client.add_source(...)` pass-through. Nothing decidable independent of its types. Exercising it for real needs a running daemon — gh #63 (the `SourceValidator` seam) blocks that, since `api.py:425-428` builds the validator inline with no `Depends()` and `validator.py:64,147` reach the network. |
| `cli/src/cli/source.py::pause` / `resume` / `edit` | **glue** | Each is one `APIClient` call and a `console.print`. No branch, no derivation, no state — nothing decidable independent of the API client's own contract. Same real-daemon blocker as `add`. |
| `cli/src/cli/source.py::remove` / `list` — the remaining command bodies | **glue** | Their decidable content is extracted above and tested directly. What is left is fetching from `APIClient`, printing, and `typer.confirm` for the removal gate — the confirmation is a stdin read from typer, not our logic, and driving it end-to-end needs the real daemon (gh #63). |
| `daemon/tests/integration/test_validator_integration.py` (3-tuple repair) | logic (it is the test) | Proves `validate_source`'s 3-tuple contract (`validator.py:22-24`, `components.md:124`) at the integration boundary; complements the unit-level guard at `test_validator_youtube_protocol_unit.py`. |
| `daemon/tests/integration/test_daemon_integration.py` (path + import repair) | logic (it is the test) | Proves the daemon entry point runs from a repo-relative cwd as `-m prismis_daemon`; the repair is what makes it provable anywhere but one laptop. |
| `daemon/tests/unit/test_content_response_model_unit.py` (path repair) | logic (it is the test) | Proves INV-API-TS-4 is documented, against the in-repo `docs/architecture/boundaries.md:46`. |
| `cli/tests/unit/test_url_extraction.py` (expectation repair) | logic (it is the test) | Proves the CLI's proposed name matches the API's naming at `api.py:294-303`. |
| `daemon/pytest.ini` | glue | Declarative pytest config; proved by the SC-10 gate and by identical collection counts before/after. |
| `daemon/pyproject.toml`, `cli/pyproject.toml` | glue | Dependency and tool declarations; proved by the SC-5 and SC-7 gates executing the declared tools. |
| `.github/workflows/ci.yml` (new) | glue | Invokes `.specify/verify.sh` with no logic of its own beyond the discovery loop, which mirrors `verify.sh:61-63,79`; proved by SC-6b — the job passing on green `main`. |
| `Makefile` (`test` target) | glue | One-line delegation to the gate; proved by SC-1 (same script, same result). |

Deleted files (`test_config_integration.py`, `test_config_unit.py` cases,
`cli/tests/integration/test_source_validation.py`, `cli/tests/integration/test_source_commands.py`)
carry no row — coverage they held is either gone with the behavior or re-homed in the rows above.

---

## Blocking gap — RESOLVED 2026-09-03

**Decision: fix the 18 daemon pyright errors inside this work order (option A).**

Measured this session (mason had no execution tool): `cd daemon && uv run pyright` → exactly
**18 errors, 0 warnings** — matching `decisions.md:78`'s recorded baseline precisely, so that
number is current rather than stale.

All 18 are annotation-level, none behavior-changing:
`reportArgumentType` ×11, `reportAttributeAccessIssue` ×4, `reportReturnType` ×2,
`reportInvalidTypeForm` ×1.

Six of them ARE the ambient-config defect this work order exists to fix — `config: Config = None`
in `fetchers/{rss,reddit,youtube}.py` where the honest annotation is `Config | None`. They sit in
the same code Step 2 already touches.

`decisions.md:78` says the baseline lives in `quality.md ## Learned Patterns` as a regression
yardstick. **`quality.md` does not exist in this repo** — the yardstick pointed at nothing. Retire
the baseline concept with this fix rather than repointing it.

The gate (`verify.sh:48-49`) runs pyright under `run_step` and fails on non-zero exit, so SC-1 is
unreachable while these exist, and the gate is not weakened to accommodate them.

## Original blocking gap (superseded by the above)

**Does this work order fix the pre-existing daemon pyright errors?**

`verify.sh:48-49` runs `uv run pyright` and fails the gate on a non-zero exit, so SC-1 cannot pass
while any daemon pyright error stands. But `docs/architecture/decisions.md:78` records a settled
position: an 18-error daemon-wide baseline as "a regression yardstick: any task increasing the
count introduces a defect; fixing pre-existing items is welcome but not required and should land
via a tracked handle." The work order's failure inventory has no pyright bucket, so this work is
uncosted either way. The two readings give materially different work orders, and the number itself
is stale — the document said to hold it (`quality.md`) is not in this repo.

- **Option A — fix them here.** SC-1 is met as written and the baseline concept retires (a gate
  that must be 0 needs no yardstick). Cost: unknown until measured, and it means editing daemon
  production code across an unknown number of modules inside a work order scoped to the test suite.
- **Option B — split.** This work order lands everything else; the pyright errors get their own
  work order and a tracked handle, and SC-1 stays red until that lands — meaning CI (SC-6) also
  waits, since it must not be red on arrival.

Recommendation: **A**, conditional on the measured count being small. Run
`cd daemon && uv run pyright` first; if it is at or near 18 and the errors are annotation-level, A
is the smaller total change and it removes a permanent asterisk from the gate. If the count is
large or the fixes require behavior changes, B — and then the CI job lands in the follow-up.
