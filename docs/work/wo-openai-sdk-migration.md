---
id: wo-openai-sdk-migration
type: feature
project: prismis
status: active
complexity: 5
created: 2026-09-03
updated: 2026-09-03
plan_ref: null
---

## What

Replace `llm-core` in the prismis daemon with the **official `openai` Python SDK**, called
directly against the endpoints prismis already uses.

Build one small module owning four things — a `complete()` wrapping the SDK, a services.toml
reader, an apiconf key fetch, and a JSON extractor — then repoint every call site and remove
the `llm-core` dependency.

`llm-core` is NOT deleted from the machine. Eight other projects still depend on it
(`arsenl`, `reverie`, `corpus`, `lore`, `sable/hooks`, four `llmcli-tools` packages). This
work order removes it from **prismis only**.

`~/.config/llm-core/services.toml` STAYS — same path, same format, still shared with those
eight apps. prismis stops importing a library to read it and reads it directly.

### Why the openai SDK and not the alternatives

All six prismis services speak **one protocol**. Verified 2026-09-03:

```
prismis-openai        adapter=openai  gpt-4.1-mini                  api.openai.com/v1
prismis-openai-deep   adapter=openai  gpt-5-mini                    api.openai.com/v1
prismis-pt-qwen       adapter=openai  qwen/qwen3.7-flash            openrouter.ai/api/v1
prismis-pt-deepseek   adapter=openai  deepseek/deepseek-v4-flash    openrouter.ai/api/v1
prismis-pt-luna       adapter=openai  openai/gpt-5.6-luna           openrouter.ai/api/v1
prismis-pt-ling       adapter=openai  inclusionai/ling-3.0-flash    openrouter.ai/api/v1
```

Two hosts, one wire format. OpenRouter is a `base_url`, nothing more.

`openai` SDK v3.8.0 — 31,546 stars, pushed 2026-09-04, maintained by OpenAI. Sync and
non-streaming, which is the shape prismis actually needs.

**Rejected, with the reason recorded so this isn't reopened cold:**
- **tau / pi** — an agent harness. Hardcodes `"stream": True` in both payload builders, is
  async-only, reports failure as an event rather than an exception, and its multi-provider
  adapters go unused because prismis speaks one protocol. Every integration problem it
  created was that shape mismatch. bench standardized on pi because bench runs agent loops;
  prismis does not.
- **litellm** — operator decision, not on technical grounds.
- **keeping llm-core** — operator decision.

## Why

Cost tracking is silently broken today, and that is the concrete trigger.

`~/.config/llm-core/pricing.toml` was last written **8 April 2026**. Four of the six models
prismis uses are absent from it, so `estimate_cost()` returns `None` and the daemon logs
`cost_usd: null` for every OpenRouter service.

**Refreshing it would not fix this.** llm-core pulls from litellm's
`model_prices_and_context_window.json` (3,561 models), which keys entries provider-prefixed:
it carries `azure/gpt-5.6-luna`, `novita/inclusionai/ling-3.0-flash` and
`dashscope/deepseek-v4-flash-0731`, and nothing at all for `qwen/qwen3.7-flash`. None match
the OpenRouter ids prismis uses. A static table cannot track this.

The provider already knows the answer. Verified live through the SDK against OpenRouter:

```
text        : ok          model: openai/gpt-5.6-luna     finish: stop
tokens      : 15 / 5
COST (real) : 9e-06
cost_details: {'upstream_inference_cost': 9e-06, 'upstream_inference_prompt_cost': 3e-06, ...}
health      : 427 models listed; target present = True
```

`extra_body={"usage": {"include": True}}` returns **actual billed dollars**. No table, never
stale.

## Brief

- **Stakes:** every LLM path in the daemon routes through this — summarization, evaluation,
  deep extraction, context analysis, audio. The failure mode is not a crash. Bad output still
  looks like output, and three separate mechanisms can degrade silently: an error that returns
  empty text rather than raising, a circuit breaker that never counts a failure it wasn't
  handed, and a JSON parse that returns `None` into a stored summary.

- **Constraints:**
  - The public result shape stays llm-core's: `text`, `model`, `provider`, `tokens.input`,
    `tokens.output`, `finish_reason`, `duration_ms`, `cost`. Call sites read these paths today;
    renaming any is breaking.
  - `~/.config/llm-core/services.toml` keeps its current path and format. It is shared with
    eight other apps — do not move it, do not change its schema.
  - Config keys `llm.light_service` / `llm.deep_service` keep working unchanged.
  - prismis's circuit breaker (`get_circuit_breaker`, `circuit_breaker.py`) stays. It currently
    decides quota errors by substring-matching `str(error)` (`"429"`, `"rate limit"`, `"quota"`)
    at `circuit_breaker.py:56-70`. The SDK's typed exceptions are strictly better input for it.
  - No new LLM abstraction layer. One SDK, called directly.

- **Settled — do not re-open:**
  - **D-TEMP** — temperature is dropped, not ported. See below.
  - The openai SDK is the chosen layer; tau, litellm and llm-core are all rejected above.

### D-TEMP (decided 2026-09-03) — temperature is dropped entirely

Controlled probe against OpenRouter, 6 samples per cell, prompt with wide sampling freedom
("Name one color"):

| model | temp=0.0 | temp=2.0 | distinct |
|---|---|---|---|
| `openai/gpt-4.1-mini` (older, non-reasoning) | Blue ×6 | Ashgray, Blue, Red | **3** |
| `openai/gpt-5.6-luna` (reasoning-class) | Blue ×6 | Blue ×6 | **1** |

The control carries the result: the same probe that detects temperature working on
gpt-4.1-mini detects nothing on luna. Separately, `qwen/qwen3.7-flash` returned **HTTP 400 on
every** temp=2.0 call — it rejects the parameter outright.

This repo already recorded the rule on the deep path (`deep_extractor.py:100`: *"reject custom
temperature with ProviderError 400 … Do not reintroduce"*). The five light-path sites still
pass 0.3/0.7 only because they predate that discovery.

**Action:** remove `self.temperature` and every `temperature=` argument from `summarizer.py`,
`evaluator.py`, `context_analyzer.py`, `context_auto_updater.py` and `audio.py`, and carry
`deep_extractor.py:100`'s reasoning into a comment on the new `complete()`.

### What prismis takes ownership of

| was | becomes |
|---|---|
| `llm_core.complete()` | `complete()` wrapping `client.chat.completions.create` |
| `llm_core.estimate_cost()` | `usage.cost` from the response (`extra_body` usage include) |
| `llm_core.resolve_service()` | `tomllib` read of the same services.toml |
| `llm_core.load_api_key()` | direct apiconf call (already a transitive dep → becomes direct) |
| `llm_core.health_check()` | `client.models.list()`, asserting the configured model is present |
| `llm_core.extract_json()` | local helper stripping ```json fences |
| llm-core retry (3 attempts, 1/2/4s) | SDK `max_retries=3` + `timeout=` |

Two things get *better*, not just replaced: cost becomes real rather than estimated, and the
health check verifies the configured model exists rather than only that the endpoint answers.

### Ferret findings, and what the SDK does to them

A ferret pass on the earlier tau-based design returned two HIGH findings that were both
consequences of tau being async and streaming. **The sync SDK dissolves both** — recorded so
nobody reintroduces them:

- **F-ASYNC (dissolved).** `api.py:1311 async def generate_audio_briefing` calls
  `generate_script()` synchronously at 1345, and `api.py:1441 async def analyze_context` calls
  `analyze_flagged_items()` at 1478 — both inside FastAPI's event loop, while the orchestrator
  runs on APScheduler's threadpool. An async client would need a bridge working in both. The
  sync SDK needs none. **Use the sync `OpenAI` client, not `AsyncOpenAI`.**
- **F-RAISE (dissolved).** tau reported provider failure as a terminal event, so the circuit
  breaker's `except` would never fire. The SDK raises typed exceptions. Keep it that way — the
  wrapper must not catch-and-return-empty.

Still live from that pass:
- The four `json=True` sites split on parse failure: two return `None` silently, two raise.
  Malformed output must be logged with the response prefix, not swallowed.
- `test_dual_service_config_unit.py` and `test_llm_core_migration_unit.py` contain tests that
  exist *solely* to assert the setup wizard writes services.toml/pricing.toml. Since
  services.toml stays and pricing.toml is no longer used, those need deliberate disposition —
  not blanket "update" and not blanket "delete". `INV-003` (the `service` → `light_service`
  rename) is orthogonal to llm-core and must survive.

### File set (enumerated 2026-09-03, `grep -rl "llm_core\|llm-core"` across py/toml/md/lock)

**28 files.** An earlier count of 15 in this work order was produced by a narrower grep and
was wrong.

Source (8): `daemon/src/prismis_daemon/` — `__main__.py`, `api.py`, `audio.py`, `config.py`,
`context_analyzer.py`, `context_auto_updater.py`, `deep_extractor.py`, `defaults.py`,
`evaluator.py`, `llm_validator.py`, `summarizer.py`

Tests (8): `tests/unit/` — `test_deep_extractor_unit.py`, `test_dep_pin_unit.py`,
`test_dual_service_config_unit.py`, `test_llm_core_migration_unit.py`,
`test_llm_startup_validation_unit.py`, `test_verify_subcommand_unit.py`;
`tests/integration/` — `test_context_assistant.py`, `test_llm_startup_validation_integration.py`

Manifests (3): `daemon/pyproject.toml`, `daemon/uv.lock`, `cli/uv.lock`

Docs (5): `docs/architecture/{architecture,boundaries,components,decisions}.md`, `README.md`

**Excluded and why:** `daemon/scripts/model_playtest.py` imports `llm_core` but is rudy's
uncommitted in-flight work (`?? daemon/scripts/`). Do not modify it. It will break on the swap
and that is rudy's call to make separately.

### Prerequisite — blocks the build, not this order

`.specify/` does not exist in this repo: no `verify.sh`, no constitution. The build lane
requires that gate and must not route around it. `/bootstrap` step 6 authors one first. The
toolchain is already declared in `daemon/pyproject.toml`: `ruff`, `pyright`, `pytest` +
`pytest-asyncio`, tests under `daemon/tests/{unit,integration}`.

## Success Criteria

### SC-1: Single-turn completion returns the existing result shape
- **Given**: service `prismis-pt-luna` resolved from the existing services.toml
- **When**: `complete(prompt=..., system_prompt=..., service=...)` is called
- **Then**: the result exposes `text`, `model`, `provider`, `tokens.input`, `tokens.output`,
  `finish_reason`, `duration_ms`, `cost` — the same attribute paths the call sites read today
- **And**: no call site's result-handling code changed beyond the import

### SC-2: Temperature is gone, and stays gone
- **Given**: the migrated tree
- **When**: the daemon source is searched for temperature outside comments
- **Then**: nothing matches
- **And**: the new `complete()` carries a comment explaining why
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  hits=$(grep -rnE '^[^#]*\btemperature\b' --include="*.py" daemon/src || true)
  if [ -n "$hits" ]; then echo "$hits"; echo "SC-2: FAIL"; else echo "SC-2: PASS"; fi
  ```
  (Against the tree today this prints nine lines. An earlier form of this check used
  `grep -v "^.*#"`, which hides every line carrying a trailing comment — including
  `audio.py:96`, a call site.)

### SC-3: Cost is real, not estimated, on an OpenRouter service
- **Given**: service `prismis-pt-luna`
- **When**: a completion runs
- **Then**: `result.cost` is a non-null float sourced from the provider's `usage.cost`
- **And**: the same call against `prismis-openai` (api.openai.com, which returns no cost)
  yields either a documented `None` or a locally-computed value — whichever the plan chooses,
  stated explicitly rather than left to chance
- **And**: `obs_log("llm.call", ...)` records that value

### SC-4: A provider error raises, and the circuit breaker counts it
- **Given**: a stubbed 429 response
- **When**: `summarizer.summarize_with_analysis(...)` is called three times
- **Then**: each call raises, and the error is recognized as a quota error by
  `circuit_breaker.is_quota_error`
- **And**: `get_circuit_breaker(service).get_status()["state"] == "open"` after the third

### SC-5: `complete()` works from inside a running event loop
- **Given**: an active asyncio loop, as in `api.py:1311` and `api.py:1441`
- **When**: `complete()` is called synchronously from within a coroutine
- **Then**: it returns or raises a provider error — never
  `RuntimeError: ... cannot be called from a running event loop`
- **And**: the same call from a plain worker thread behaves identically

### SC-6: JSON extraction is tolerant, and its failures are visible
- **Given**: a reply of the form ``Here is the analysis:\n```json\n{...}\n```\n``
- **When**: any of the four former `json=True` sites parses it
- **Then**: all four yield the same dict as a bare-JSON reply
- **And**: given a reply containing no JSON, each site logs the first 200 chars at ERROR
  before returning `None` or raising

### SC-7: Startup validation verifies the model, not just the endpoint
- **Given**: a reachable service whose configured model does not exist
- **When**: the startup validation path runs
- **Then**: it fails, naming both the service and the missing model
- **And**: a merely-unreachable deep service still yields a non-fatal result, preserving
  `test_validate_llm_services_deep_failure_is_non_fatal`

### SC-8: llm-core is gone from prismis and the daemon runs without it
- **Given**: a venv with the `llm-core` package uninstalled
- **When**: `prismis-daemon verify` runs against a configured service
- **Then**: it exits 0
- **And**: `import llm_core` fails, proving the grep below wasn't satisfied by a still-installed
  package
- **Verification**:
  ```bash
  cd /Users/rudy/development/projects/prismis
  if grep -rq "llm_core" --include="*.py" daemon/src cli/src \
     || grep -q "llm-core" daemon/pyproject.toml; then echo "SC-8: FAIL"; else echo "SC-8: PASS"; fi
  ```

### SC-9: services.toml is untouched and still shared
- **Given**: the migrated tree
- **When**: `~/.config/llm-core/services.toml` is compared to its pre-migration content
- **Then**: it is byte-identical — same path, same schema
- **And**: prismis reads it directly, and the other eight apps keep reading it via llm-core

### SC-10: The dependency-pin invariant does not become vacuous
- **Given**: `daemon/pyproject.toml` after the swap
- **When**: `pytest tests/unit/test_dep_pin_unit.py` runs
- **Then**: it passes
- **And**: with llm-core gone from `[tool.uv.sources]`, its assertions on rev `2eb4429` are
  rewritten to state the PyPI decision explicitly rather than silently having nothing to check

### SC-11: Architecture docs describe the layer that exists
- **Given**: the migrated tree
- **When**: `docs/architecture/{architecture,boundaries,components,decisions}.md` and
  `README.md` are searched
- **Then**: no `llm-core` / `llm_core` reference remains, and the daemon's LLM contract names
  the SDK, the result shape, the error contract, and where service config lives

### SC-12: The existing suite passes
- **Given**: the migrated tree
- **When**: the daemon's test suite runs
- **Then**: it passes, with each of the eight llm_core-referencing test files given a
  deliberate disposition (retargeted, or deleted because the behavior it guarded is gone) —
  none skipped

## Approach

[Empty — filled after planning]

## Verification

```bash
cd /Users/rudy/development/projects/prismis/daemon
uv run ruff check .
uv run pyright
uv run pytest
```

Plus a live end-to-end summarization against a real configured service, confirming a stored
summary and a **non-null** `cost_usd` in the observability log — the thing that is broken today.

## Outputs

[Empty — filled on completion]
