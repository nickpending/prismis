---
id: wo-verify-chain
type: feature
project: prismis
status: active
complexity: 7
created: 2026-09-08
updated: 2026-09-08
plan_ref: docs/work/verify-chain/plan.md
---

## What

Extend the existing `prismis-daemon verify` subcommand into a real end-to-end pipeline run that
drives the **production** classes through the **production** orchestrator against one explicitly
named source, and reports what happened at each link — status, duration, and where applicable the
cost and which model actually answered.

Also instrument the two pipeline links that currently leave no record at all.

## Why

The daemon suite proves the *logic* and nothing about the *pipeline*. All 44 daemon skips are the
links that touch reality — fetch, summarize, evaluate, store, embed, notify. Nobody has ever run
prismis end to end through its own production classes and watched what came out.

That gap is what makes deployment unsafe. `make install-daemon` runs
`uv tool install . --python 3.13 --reinstall`, which **replaces the running daemon** on cerebro.
There is currently no way to learn that a build is broken before it becomes the installed one.

The three bugs found during `wo-green-the-suite` — #59, #61, #65 — were all found by *incidental*
contact with reality while fixing something else. Three real bugs surfaced by accident is the
argument for pointing the instrument at the product deliberately.

## Brief

- **Stakes:** A verify chain that reimplements any link is worse than no chain at all — it would
  report green while the product is broken, which is exactly the failure mode the last two work
  orders were spent removing. The operator's words: *"If verify reimplements any link, it tests
  verify. That is how a green smoke check sits over broken code."*

- **Constraints:**
  - **The chain MUST call the production classes the orchestrator calls.** Not equivalents, not
    re-derived logic — `RSSFetcher.fetch_content()`, `ContentSummarizer`, `ContentEvaluator`,
    `Storage`, `Embedder`, driven through `DaemonOrchestrator`.
  - **The live database is never opened, in either direction.** Operator: *"I dont want to fuck
    with the databse at all."*
  - **No mocks, and deliberately not even the one mock the constitution permits.** Principle I
    allows mocking LLM providers; this chain must not, because exercising them for real is the
    entire point.
  - **Credentials never enter the repo and never enter CI.**
  - **No new dependencies.**
  - `./.specify/verify.sh` must exit 0 and CI must stay green.

- **Decisions taken before planning (do not re-open):**
  - **D-ORCH.** The orchestrator *is* the chain. `DaemonOrchestrator.__init__` already takes every
    collaborator injected — storage, four fetchers, summarizer, evaluator, notifier, config,
    console, embedder, deep_extractor — and `fetch_source_content()` runs the whole per-source
    pipeline. The chain constructs the real orchestrator with real collaborators and calls it with
    a synthetic source dict built from `--source <url>`. It reimplements nothing, and it never
    queries the sources table, so the operator's real source list is untouched.
  - **D-ISOLATE.** Isolation is two knobs — `database.py` resolves the DB from `XDG_DATA_HOME`,
    `config.py` resolves credentials from `XDG_CONFIG_HOME`. Run with `XDG_DATA_HOME` on a temp
    dir and the **real** config — real keys, real LLM, real network, throwaway database.

    **Corrected: there is a third path, and it must be fixed for this to be true.**
    `observability.py`'s `ObservabilityLogger.__init__` resolves its base dir as
    `Path.home() / ".local" / "share"` and **never reads `XDG_DATA_HOME`**. So the JSONL this
    chain exists to mine would land in the operator's real observability tree, not the temp one —
    breaking both this decision and D-REPORT. Every other module in the daemon reads the env var
    (`database.py` twice, `api.py` twice, and the `XDG_CONFIG_HOME` sites in `config.py`,
    `defaults.py`, `context_auto_updater.py` and `__main__.py`); observability is the only one
    that does not. **Fix it to match that established pattern.** There is no clean alternative:
    `obs_log` is a module-level singleton the fetchers, summarizer, evaluator and storage all call
    directly, so the chain cannot inject a base dir into their calls, and monkeypatching is
    forbidden by Principle I. (`audio.py`'s `Path.home() / "Downloads"` is a deliberate
    user-facing location and is NOT part of this.)
  - **D-REPORT.** Per-link reporting is mined from the observability JSONL **the run itself
    writes**, not from instrumentation invented for the test. `observability.log()` already emits
    from all four fetchers, storage (dedup and store), the summarizer and evaluator (both emit
    `llm.call` carrying `action`, `model`, `tokens`, `cost_usd`, `duration_ms`, `status`) and the
    deep extractor. Preferring the artifact the system authoritatively writes over a side-effect
    transcript is P29.

    **Attribution is solved at the observability layer, not per call site.** Events carry only
    `{ts, event, **metadata}` — no run id, no pid — and the file rotates daily, so on cerebro the
    production daemon writes into the same file on its own schedule (`fetch_interval = 90`,
    daemon confirmed running). Filtering by a time window alone is not enough: `fetcher.*` and the
    storage events carry `source_id` and can be attributed, but **`llm.call` events carry no
    source or run identifier at all**, so a chain run overlapping a daemon cycle would count the
    daemon's summarize and evaluate calls as its own and report inflated cost and item counts.

    `observability.py` gains a process-scoped current-run id that `log()` stamps onto every event
    when set. One file — already being modified for the XDG fix — additive to the event schema,
    and it covers every event including `llm.call`. Threading an argument through the summarizer
    and evaluator instead would touch more modules and still miss any call site added later.

    **Operator decision.** A report that silently attributes another process's spend to this run
    is precisely the "looks right and isn't" failure this work order exists to prevent.

    **One state has no event at all.** A circuit-open skip raises at `summarizer.py:104` (and the
    matching site in `evaluator.py`) BEFORE the try block that emits `obs_log`, so a
    quota-exhausted link leaves no trace in the JSONL. It is a fourth state, and it currently
    looks identical to "never ran".
  - **D-DARK.** Three links are dark, not two. `embeddings.py` and `notifier.py` contain **zero**
    `obs_log` calls, and so does `Storage.create_or_update_content` — the method
    `fetch_source_content` actually calls to store an item (`orchestrator.py:216` and `:342`).
    `Storage.add_content` in the same file IS instrumented, which is what made the work order's
    original "storage YES" claim look true; nothing in the pipeline calls it. All three get
    instrumented.

    This is a real constitution Principle III gap — the pipeline runs unattended on a schedule and
    those links leave no record — not scaffolding for a test. Instrumenting the store link also
    keeps the report coherent: without it, one of the six default links would be reported by
    inference from returned stats while every other link is reported from an event.

    **Operator expanded this scope explicitly** after the planner correctly declined to expand it
    unilaterally.
  - **D-EXTEND.** Extend the existing `verify` subcommand rather than forking a parallel command.
    Its current cheap checks stay as the default; the chain is additive behind a flag.
  - **D-INIT.** `Storage.__init__` is not lazy despite its comment: it calls
    `get_db_connection(self.db_path)` eagerly to test the connection. Against a fresh temp
    `XDG_DATA_HOME` with no database file, constructing `Storage` therefore fails immediately.
    The chain must initialize the schema against the temp dir before constructing any
    collaborator.
  - **D-DEFAULT.** Default runs links 1–4 (fetch, dedup, summarize, evaluate), 6 (store) and
    7 (embed). Deep extraction (link 5, the expensive deep service) and notify (link 9) go behind
    `--full`.

- **Key Questions for the planner:**
  1. What flag shape does the chain take, and how does it coexist with the existing `verify`
     behaviour without changing that behaviour for an existing caller?
  2. `fetch_source_content` prints progress to a `Console` and returns aggregate stats. How does
     the chain get *per-link* status out of it without modifying the orchestrator's contract?
  3. How is a link that never ran distinguished from a link that ran and produced nothing? Under
     Principle II those are different answers, and a chain that renders both as a blank cell has
     reintroduced the defect this project keeps fixing.

## Success Criteria

### SC-1: The chain drives the production orchestrator, not a copy
- **Given**: the chain implementation
- **When**: it executes a run
- **Then**: it constructs `DaemonOrchestrator` with the real `Storage`, the real fetchers, the real
  `ContentSummarizer`, `ContentEvaluator` and `Embedder`, and drives the pipeline through
  `fetch_source_content()`
- **And**: it contains no re-derived fetch, summarize, evaluate, store or embed logic of its own —
  a reviewer must be able to point at the orchestrator call and see the whole pipeline behind it
- **And**: backed by a mechanical check, not reviewer judgment alone — an AST walk asserts the
  chain module's absolute imports are a **subset of an allowlist**, so anything not explicitly
  permitted fails the gate regardless of its name. The constitution gates completion on executed
  verification, and this work order's central stake is exactly this criterion, so it cannot rest
  on a judgment call.

  **Why an allowlist and not a denylist.** The first version enumerated the libraries a link would
  need — `feedparser`, `praw`, `yt_dlp`, `httpx`, `sentence_transformers`, `llm_core` — and a
  reimplementation written with `urllib`, `requests`, `socket` or hand-rolled logic walked straight
  past it. A denylist can only exclude the names on it, so calling it "not a judgment call"
  overstated what it proved; the judgment had simply moved into the list's completeness (P16).

  The inversion is cheap because the AST walk already skips relative imports (`node.level == 0`),
  so every real collaborator — the orchestrator, storage, the fetchers, summarizer, evaluator,
  embeddings, notifier — is outside the check by construction and needs no enumeration. The chain
  module's absolute imports are exactly `dataclasses`, `datetime`, `json`, `pathlib`, `rich`,
  `typing` and `uuid`, verified by parsing it, so the allowlist is small, stdlib-shaped and stable.
  It also mechanically enforces this work order's "No new dependencies" constraint as a side
  effect.

### SC-1b: The schema exists before any collaborator is constructed
- **Given**: a fresh temp `XDG_DATA_HOME` with no database file
- **When**: the chain starts
- **Then**: it initializes the schema before constructing `Storage`, because `Storage.__init__`
  eagerly opens a test connection despite its "lazy" comment and fails otherwise
- **And**: a test covers this against a genuinely empty temp dir, so the ordering is proven rather
  than discovered from a stack trace

### SC-2: The live database is never opened
- **Given**: a chain run
- **When**: any `Storage`, `Database` or observability write happens
- **Then**: every path resolves under the temp `XDG_DATA_HOME` the run established, never under
  the operator's real data dir — including the observability JSONL, once `observability.py` reads
  the env var
- **And**: the chain never calls `get_active_sources()` or otherwise reads the sources table; the
  source under test comes from `--source` alone
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  # Scoped to the chain's own module — the first version of this grepped __main__.py for a line
  # mentioning get_active_sources near the words "chain" or "verify", which printed PASS on a
  # tree where none of the work had been done. Name the file the chain lives in.
  CHAIN=daemon/src/prismis_daemon/verify_chain.py   # planner may rename; update here if so
  if [ ! -f "$CHAIN" ]; then echo "SC-2: FAIL (chain module $CHAIN not found)"
  elif grep -q "get_active_sources" "$CHAIN"; then echo "SC-2: FAIL (chain reads the sources table)"
  elif ! grep -q "XDG_DATA_HOME" daemon/src/prismis_daemon/observability.py; then
    echo "SC-2: FAIL (observability still ignores XDG_DATA_HOME)"
  else echo "SC-2: PASS"; fi
  ```

### SC-3: Every link reports, and a skipped link is distinguishable from an empty one
- **Given**: a completed chain run
- **When**: the report renders
- **Then**: each link in scope shows a status, a duration, and — for the LLM links — the cost and
  the model that actually answered
- **And**: **ran-and-produced-nothing**, **skipped-by-flag**, **never-reached-because-an-earlier-link-failed**,
  and **skipped-because-the-circuit-was-open** are four visibly different states, not one blank
  cell (Principle II)
- **And**: the circuit-open case is the hard one. `summarizer.py` and `evaluator.py` raise before
  their `obs_log` try block, so **that raise** emits nothing — but the breaker itself logs
  `circuit_breaker.state` with `state="open"` at `circuit_breaker.py:117-119` as it crosses the
  threshold, through `obs_log`, so it carries the run id like any other event. A record does
  exist; it just is not an `llm.call`.

  Classification reads `fetch_source_content`'s returned stats for the refusal entries — a
  deliberate exception to D-REPORT, named here rather than slipped in. The `circuit_breaker.state`
  event decides only whether the report says "opened during this run" or "was already open when
  the run started"; it must NOT gate the classification itself. A run that begins with the circuit
  already open refuses every item and logs no transition, so gating on the event would report that
  run as `error` — the same collapse this criterion exists to prevent, arriving from the other
  side.

  *(Corrected after the build. The original text asserted the case "emits nothing", which is true
  of the raise and false of the breaker.)*

### SC-4: A link-1 failure stops the run before money is spent
- **Given**: a `--source` URL that cannot be fetched
- **When**: the chain runs
- **Then**: it reports the fetch failure and stops, having made **no** LLM call
- **And**: this is proven by a test that observes the absence of any `llm.call` event, not by
  reading the code
- **And**: absence alone is not sufficient evidence and the test must not rest on it — a
  circuit-open skip also emits no `llm.call`, so the test must additionally show the run reports
  the fetch failure as the reason it stopped. An assertion that cannot distinguish the state under
  test from a different state proves neither (CLAUDE.md)

### SC-5: The two dark links emit records
- **Given**: `daemon/src/prismis_daemon/embeddings.py` and `daemon/src/prismis_daemon/notifier.py`
- **When**: an embedding is generated or a notification is attempted
- **Then**: each emits an observability event in the shape the summarizer and evaluator already
  use, carrying at minimum an outcome status and a duration
- **And**: a failure emits a distinguishable event rather than silence
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  e=$(grep -c "obs_log(" daemon/src/prismis_daemon/embeddings.py || true)
  n=$(grep -c "obs_log(" daemon/src/prismis_daemon/notifier.py || true)
  if [ "$e" -gt 0 ] && [ "$n" -gt 0 ]; then echo "SC-5: PASS"; else echo "SC-5: FAIL (e=$e n=$n)"; fi
  ```

### SC-6: Defaults spend the light service only
- **Given**: a chain run with no `--full`
- **When**: it completes
- **Then**: links 1–4, 6 and 7 ran; deep extraction and notify did **not**, and are reported as
  skipped-by-flag rather than as absent
- **And**: `--full` includes both

### SC-7: The existing verify behaviour is unchanged for existing callers
- **Given**: `prismis-daemon verify` invoked exactly as before, with no new flag
- **When**: it runs
- **Then**: it performs the same config / light service / deep service / active-sources checks and
  exits with the same codes it did before this work
- **And**: the chain is additive — its documented promise of being "safe to run against a
  production daemon" still holds for the default invocation

### SC-8: What can be proven here is proven here, by tests the gate runs
- **Given**: this Mac has no daemon config — `~/.config/prismis/config.toml` is a `[remote]`-only
  CLI stub and `Config.from_file()` raises "Config [llm] section outdated"
- **When**: the gate runs
- **Then**: real tests cover the argument surface, the synthetic-source construction, the JSONL
  reading, the per-link renderer including the three-state distinction from SC-3, the temp-XDG
  isolation, and the new observability emissions from SC-5
- **And**: none of them is gated behind `PRISMIS_LIVE_NETWORK_TESTS` or credentials
- **And**: each is mutation-verified — disable the behaviour, confirm red, restore

### SC-8b: The report describes THIS run and no other
- **Given**: an observability file that already contains events from an earlier cycle, and a
  daemon that may write to the same daily file concurrently
- **When**: the chain renders its report
- **Then**: it reports only events its own run produced, and a test proves this by seeding the
  file with foreign events that must not appear in the output
- **And**: the selection rule is stated in the plan, not left implicit in the reader's code

### SC-9: The gate and CI stay green
- **Given**: the completed change
- **When**: `./.specify/verify.sh` runs
- **Then**: it exits 0 and prints no `VERIFY_UNCOVERED` line
- **And**: CI is green on `main` after the push

### SC-10: The chain has been run for real, on cerebro
- **Given**: the built chain, checked out on cerebro where the real config and keys live
- **When**: run against a real source with a temp `XDG_DATA_HOME`
- **Then**: the per-link report is captured verbatim into the work order's Outputs
- **And**: this is recorded as a **manual, unguarded run** — not as a standing test — because
  nothing in the tree asserts it

## Accepted risk

The circuit breaker's `_breakers` registry is a plain module-level dict, so it resets on every
fresh interpreter. A chain run is a separate process and cannot see a circuit the long-running
daemon already opened, or vice versa. No persisted circuit state exists anywhere in the tree to
read instead, so this is accepted rather than solved — recorded here so a future reader does not
mistake the chain's quota view for the daemon's.

## Out of Scope

- **Link 8 (serve).** The HTTP API has its own coverage; the chain does not exercise it here.
- The OpenAI SDK migration (`docs/work/wo-openai-sdk-migration.md`).
- gh #67, #68, #69, #70 — filed handles from `wo-reddit-validation-seam`.
- gh #61, #65, #58, #64.
- Restoring the other live-network integration tests (#60).

## Verification

```bash
cd /Users/rudy/development/projects/prismis

# the gate — >2 min, raise the Bash timeout
./.specify/verify.sh; echo "exit: $?"

# the chain's own tests, none of which need credentials or network
(cd daemon && uv run pytest -q --no-header -k "verify_chain or chain_report or obs")

# the existing verify behaviour is unchanged (SC-7)
(cd daemon && uv run python -m prismis_daemon verify --help)
```

The real run needs cerebro, where the config and keys live:

```bash
ssh cerebro
cd ~/prismis && git fetch && git checkout <sha>
cd daemon && XDG_DATA_HOME=$(mktemp -d) \
  ~/.local/bin/uv run prismis-daemon verify --chain --source <url>
# uv is at ~/.local/bin/uv and is NOT on the non-interactive ssh PATH
```

## Approach

[Empty — filled after planning]

## Outputs

[Empty — filled on completion]
