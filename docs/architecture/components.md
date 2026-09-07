---
type: architecture
subtype: components
project: "prismis"
status: active
created: "2026-04-07"
updated: "2026-09-07"
last_change: "wo-reddit-validation-seam (#59, #63): Source Validator rewritten onto PRAW with nine distinguishable outcomes and an optional Config; API Server gained get_validator and moved validation off the event loop under a 20s budget; auth.py stopped rendering config-load exception text into the response; Fetchers noted as divergent from the validator's PRAW construction (#67, #68)"
tags: [architecture, components]
---

# Components

Registry of all system components. Each entry links to a detail doc when the component has enough substance to warrant one.

## Daemon

**Purpose:** Python daemon that fetches content from multiple sources, summarizes and evaluates it via LLM, stores results in SQLite, and serves a REST API.
**Key files:** `daemon/src/prismis_daemon/` — orchestrator.py (fetch loop + deep-extract gate), summarizer.py, evaluator.py, deep_extractor.py, storage.py, api.py (`/audio` now mounted unconditionally with `StaticFiles(..., check_dir=False)` — the route table no longer depends on the audio dir existing at import time), config.py (incl. `deep_extract_exclude` field), defaults.py (config template defaults, incl. `deep_extract_exclude = ["reddit"]`), __main__.py (`_load_ambient_env()` loads `~/.config/prismis/.env` explicitly from the Typer callback instead of at module import; the background API-server task from `asyncio.create_task()` is now held in `api_task` and awaited — bounded 0.5s — on shutdown, since an unreferenced task is only weakly held by the event loop and can be GC'd mid-flight)
**Connections:** Depends on llm-core (Python) for all LLM calls. Depends on apiconf for API key resolution. TUI and CLI connect via REST API (api.py) and shared SQLite DB.
**Detail:** [components/daemon.md](components/daemon.md)

## Fetchers

**Purpose:** Content source adapters — RSS, Reddit, YouTube, and static-URL change monitoring (source type `"file"` — despite the name, fetches a remote URL and diffs it, not local filesystem ingestion).
**Key files:** `daemon/src/prismis_daemon/fetchers/` — rss.py, reddit.py, youtube.py, file.py (`FileFetcher`: GETs a URL, SHA256-hashes content, compares against the previous entry's stored `analysis.content_hash`; on change, emits a new entry with a unified diff and `external_id = sha256(url + content_hash)[:16]` so each change becomes a new content item, preserving history — see `docs/explorations/EXPLORATION-2025-10-31-url-monitoring-design.md`)
**Connections:** Called by orchestrator.py during fetch cycles. Each returns normalized content items to storage. **INV-FETCH-1 (task 2.9):** all fetcher datetime emissions are tz-aware via `datetime.now(UTC)`; no `datetime.utcnow()` anywhere under `daemon/src/prismis_daemon/fetchers/`. Producer-side complement to the storage ISO-string convention and the API RFC3339 wire contract — closes the wire end-to-end at the producer layer. `yt-dlp` is declared as a hard `daemon/pyproject.toml` runtime dependency (not an optional extra) — `youtube.py` requires the binary on PATH and raises without it.

**`reddit.py` now diverges from the validator's PRAW construction, knowingly.** After #59 the validator builds its client with `check_for_updates=False`, a bounded transport, and an `env:`-placeholder credential guard; `RedditFetcher` does none of these. It therefore reaches `pypi.org` once per process at construction, imposes no timeout of its own, and hands an unexpanded `"env:REDDIT_CLIENT_ID"` straight to PRAW — which constructs successfully (praw raises `MissingRequiredAttributeException` only for `CONFIG_NOT_SET` or `None`) and fails later as a real 401, telling an unconfigured install its credentials are invalid. Tracked as gh #67 and gh #68; deliberately out of scope for #59, which only touched the add-time path.

## Summarizer

**Purpose:** LLM-powered content summarization producing structured summaries with insights, entities, patterns.
**Key files:** `daemon/src/prismis_daemon/summarizer.py`
**Connections:** Receives service_name from __main__.py. Calls llm_core.complete(). Uses circuit_breaker for quota protection. Logs via observability.

## Evaluator

**Purpose:** LLM-powered content prioritization — scores items by relevance to user interests defined in context.md.
**Key files:** `daemon/src/prismis_daemon/evaluator.py`
**Connections:** Same pattern as Summarizer. Receives service_name, calls llm_core.complete(), uses circuit_breaker.

## Deep Extractor

**Purpose:** Second-tier LLM synthesis for high-priority items — produces labeled **Counterintuitive / Buried lede / So what / Pushback** sections plus 0–3 verbatim quotables per article.
**Key files:** `daemon/src/prismis_daemon/deep_extractor.py` (ContentDeepExtractor class, DeepExtraction dataclass, CircuitOpenError typed exception)
**Connections:** Same shape as Summarizer/Evaluator — standalone class instantiated in `__main__.py`, injected into orchestrator and `app.state` for both pipeline auto-extract and on-demand API calls. Uses its own circuit breaker keyed to `llm_deep_service` (separate from `prismis-openai`). Synthesis stored at `analysis.deep_extraction` and combined with `summary` text for embedding regeneration. Skepticism is source-internal only — no external knowledge, no reader-verification asks. **Source-type exclusion (commit 1e39916):** `orchestrator._should_deep_extract(priority, auto_extract, source_type, exclude)` short-circuits to `False` when `source_type in exclude` BEFORE priority gating — low-signal sources skip deep synthesis regardless of priority. Driven by `Config.deep_extract_exclude: list[str]` (loaded from `[llm] deep_extract_exclude`, ships `["reddit"]` by default in `defaults.py`). Call site at orchestrator.py:315-319 passes `source_type` + `self.config.deep_extract_exclude`.

## Context System

**Purpose:** User interest profile (context.md) and LLM-driven auto-update from feedback patterns.
**Key files:** `daemon/src/prismis_daemon/context_analyzer.py`, `context_auto_updater.py`
**Connections:** context_analyzer suggests new topics from flagged items. context_auto_updater runs on daily schedule via APScheduler.

## Circuit Breaker

**Purpose:** Service-keyed quota protection — prevents cascading LLM failures from exhausting API quotas.
**Key files:** `daemon/src/prismis_daemon/circuit_breaker.py`
**Connections:** Used by summarizer, evaluator. Keyed by service_name (e.g., "prismis-openai"). Module-level singleton registry.

## API Server

**Purpose:** FastAPI REST API for content access, search, flagging, context analysis, on-demand deep extraction.
**Key files:** `daemon/src/prismis_daemon/api.py` (`get_validator` dependency provider added alongside `get_storage` and `get_config`, injected into `add_source` and `update_source` — neither constructs a `SourceValidator` inline; both `validate_source` calls run off the event loop behind `asyncio.wait_for(asyncio.to_thread(...))` since the validator is blocking and the handlers are `async def`; `SOURCE_VALIDATION_TIMEOUT = 20.0` bounds them, see boundaries.md for why the number must stay under every client's), `auth.py` (`verify_api_key` no longer renders the config-load exception into the `ServerError` body — it logs the detail and answers with a fixed message; the blanket `str(e)` was leaking the absolute config path and structural detail to a caller holding no valid key), `api_models.py` (ContentItemModel + ContentResponseData + ContentResponse Pydantic models added by task 2.8 with `@field_serializer` decorators on every datetime field delegating to `_rfc3339`; SourceResponse + AudioBriefingResponse migrated from V1-compat `model_config = {"json_encoders": ...}` to V2-native `@field_serializer` in the same task; SourceRequest.validate_url migrated from V1 `@validator` to V2-native `@field_validator(mode='before')` by task 2.12 — `api_models.py` now has zero V1-compat Pydantic mechanisms; task 2.14 added three `@typing.overload` stubs to `_rfc3339` (`datetime → str`, `None → None`, `datetime | None → str | None`) immediately before the implementation — closes the per-callsite `-> str` narrowing pattern from task 2.10 at the encoder definition site so non-Optional serializers narrow automatically without per-callsite annotation), `api_errors.py` (ServiceUnavailableError 503 added for circuit-open and not-configured cases)
**Connections:** Reads/writes SQLite via storage.py. TUI connects as HTTP client. Protected by API key auth. POST `/api/entries/{id}/extract` is idempotent (checks `analysis.deep_extraction` first, returns existing without LLM call) and routes errors by exception type — `CircuitOpenError` → 503, generic `Exception` → 500. **INV-API-TS-4 (task 2.8, audio endpoint closed by task 2.10, detail endpoint closed by task 2.11):** `/api/entries` and `/api/search` route through `ContentResponse(...).model_dump(mode="json")`; `/api/audio/briefings` routes through `AudioBriefingResponse(...).model_dump(mode="json")` as of task 2.10; `/api/entries/{content_id}` routes through `ContentItemModel(...).model_dump(mode="json")` (with `exclude={'content'}` on the lightweight branch) as of task 2.11 — every datetime field on the wire is RFC3339-compliant via `@field_serializer`. No raw-dict response paths remain on datetime-bearing content endpoints. The detail-endpoint gap was caught by test-runner's independent grep audit of all 13 raw-dict return sites in api.py during task 2.8 — neither planner nor builder flagged it; the audit pattern is now codified in quality.md.

## Storage

**Purpose:** SQLite persistence layer — content items, metadata, deduplication, archival, on-demand analysis patching.
**Key files:** `daemon/src/prismis_daemon/storage.py` (added `update_analysis(content_id, analysis)` for on-demand extraction patching; datetime values bound as ISO strings via `datetime.now(timezone.utc).isoformat()` to satisfy Python 3.12 sqlite3 adapter requirements), `database.py`, `schema.sql`
**Connections:** Central data layer. Written by daemon orchestrator, read by API server and TUI. The on-demand extract endpoint updates `analysis` JSON without rewriting other content fields.

## Audio Briefings

**Purpose:** Generate spoken audio briefings from daily high-priority content.
**Key files:** `daemon/src/prismis_daemon/audio.py`, `reports.py`
**Connections:** Uses llm_core.complete() for script generation (no circuit breaker). Uses lspeak for TTS.

## TUI

**Purpose:** Go terminal UI for reading, triaging, and managing content.
**Key files:** `tui/internal/` — ui/ (reader.go renders deep_extraction synthesis + quotables visually distinct from light summary; helpers.go renderSimpleMarkdown handles `## ` and `### ` headers; model.go copy command), api/ (client.go ExtractEntry method), commands/ (registry.go cmdExtract + ExtractMsg), ui/operations/extract.go (async extract command), db/, clipboard/, fabric/, service/
**Connections:** Connects to daemon REST API including POST /extract. The `:extract` TUI command triggers on-demand deep extraction, refreshes the reader on completion. 503 responses parsed for `reason` sub-code → distinct user-facing messages (`not_configured` vs `circuit_open`). **Quote unification (commit 54eadb4):** reader.go renders `Summary → Deep Synthesis → one "Quotes" section` — `appendSynthesisSection` (prose only, no quotables) + `appendQuotesSection(combineQuotes(metadata.Quotes, metadata.DeepExtraction))` which dedups light article quotes with deep quotables into a single block (replaces the former separate "Key Quotes" + "Notable Lines"; `injectQuotesIntoSummary` removed). The copy command (model.go) now emits `reading_summary + deep synthesis prose` (no quotes).
**Detail:** [components/tui.md](components/tui.md)

## Web View

**Purpose:** Single-page web frontend served from daemon — card list with click-to-open article detail panel showing full content + deep extraction.
**Key files:** `daemon/src/prismis_daemon/static/index.html` — inline CSS/JS, no framework, no build step. PrismisWebapp class with `openDetail(id)`, `closeDetail()`, `renderContentItem`. Card click target scoped to `.item-summary` (not the whole `.content-item`) so interactive children — title `<a>` link, vote buttons, mark-read button — live outside the click region by structure.
**Connections:** Fetches from `/api/entries` (list) and `/api/entries/{id}?include=content` (detail) with X-API-Key. INV-D3 (click-delegation) holds by structural scoping, not per-element stopPropagation. **Quote unification (commit 54eadb4):** detail panel renders a single deduped "Quotes" section (`.detail-quotes` CSS class, was `.notable-lines`) merging light `analysis.quotes` with deep `de.quotables` via a `Set`-based dedupe — the former separate "Notable Lines" block is gone, and light quotes (never previously shown on web) now appear. Layout matches the TUI: Summary → Deep Synthesis → Quotes.

## CLI

**Purpose:** Python CLI for installation, admin tasks, and batch operations against the daemon API.
**Key files:** `cli/src/cli/__main__.py` (typer app + command registrations; `_load_ambient_env()` loads `~/.config/prismis/.env` explicitly from the Typer callback instead of at module import — mirrors the daemon's identical fix), `api_client.py` (APIClient with `search()`, `get_content()`, `extract_entry()` etc. — httpx-based, X-API-Key auth, configurable timeout per method), `extract.py` (task 3.1 — `prismis-cli extract --priority {high|medium|low|all} --limit N` batch deep-extraction command; symmetric `< 1` and `> 3333` input-validation guards mirror the server's `le=10000` constraint on `GET /api/entries` accounting for the `limit * 3` overshoot formula), `search.py`, `list.py`, `analyze.py` (`repair` now calls `Config.from_file()` + `ContentSummarizer(config.llm_light_service)` / `ContentEvaluator(config.llm_light_service)` — was calling `Config()`/`ContentSummarizer()`/`ContentEvaluator(config)` with the wrong signatures and crashing on entry), `archive.py`, `export.py`, `prune.py`, `report.py`, `source.py` (URL-type detection and normalization extracted into standalone functions — `detect_and_normalize_source_url`, `resolve_source`, `find_source_by_id`, `format_source_row` — for unit testability; see boundaries.md for a documented divergence between this normalization and the daemon's), `migrate-config` shim
**Connections:** Calls daemon REST API via `APIClient` (httpx + X-API-Key). Remote mode (`is_remote_mode() == True` when `[remote]` block in `~/.config/prismis/config.toml`) routes calls to a deployed daemon (e.g., `https://prismis.mal.casa`); local mode hits `http://127.0.0.1:8989`. `youtube://` URL rewriting happens client-side in `source.py` before API calls. `extract_entry()` uses a local `httpx.Timeout(120.0)` override (LLM-bound; longer than the class-level 30s default). That class-level 30s is load-bearing as of #59: the daemon's own validation budget must stay strictly under it or the daemon's timeout message can never reach an operator — see boundaries.md.

## LLM Validator

**Purpose:** Startup health check for the dual-service configuration — light service fatal on failure, deep service non-fatal (graceful degradation).
**Key files:** `daemon/src/prismis_daemon/llm_validator.py`
**Connections:** `validate_llm_services(light_service, deep_service)` calls `llm_core.health_check(service=)` for light (raises on failure) and optionally for deep (wrapped in try/except, returns `unreachable` on failure). Called by `__main__.py:validate_llm_config()` at startup. The legacy single-service `validate_llm_config(service_name)` remains for callers that only need one service.

## Embeddings

**Purpose:** Local, offline embedding generation for semantic search.
**Key files:** `daemon/src/prismis_daemon/embeddings.py` (`Embedder` class)
**Connections:** Uses the `all-MiniLM-L6-v2` SentenceTransformer model (384 dimensions, lazy-loaded on first use). Imported by api.py, orchestrator.py, and storage.py — embeddings are generated on content ingestion/deep-extraction and stored in the `vec_content` vec0 table; storage.py converts vec0's default L2 distance to cosine via `1 - d²/2` (relies on this model's Normalize layer producing unit-normalized vectors). Underpins the `/api/search` semantic-search path.

## Notifier

**Purpose:** Desktop notifications for newly-fetched HIGH-priority content.
**Key files:** `daemon/src/prismis_daemon/notifier.py` (`Notifier` class)
**Connections:** `notify_new_content(items)` filters for HIGH items and calls `_send_notification`. Instantiated/called by orchestrator.py during fetch cycles; defaults sourced from defaults.py. Config-driven via `__main__.py`.

## Observability

**Purpose:** JSONL event-tracking logger for daemon operations.
**Key files:** `daemon/src/prismis_daemon/observability.py` (`ObservabilityLogger` class + module-level `get_logger()` / `log(event, **metadata)` helpers; uses `fcntl` for append-safe writes)
**Connections:** Cross-cutting — used by `__main__.py`, api.py, orchestrator.py, storage.py, summarizer.py, evaluator.py, deep_extractor.py, circuit_breaker.py, context_analyzer.py, context_auto_updater.py. The "Logs via observability" connections noted on Summarizer/Evaluator/etc. resolve here.

## Source Validator

**Purpose:** Pre-add validation of content sources (RSS, Reddit, YouTube, file) before they enter the database.
**Key files:** `daemon/src/prismis_daemon/validator.py` (`SourceValidator` class; constructor takes an optional `Config` — rss/youtube/file need none, and twelve sites construct it bare)
**Connections:** `validate_source(...)` dispatches to per-type validators (`_validate_rss`, `_validate_reddit`, `_validate_youtube`, `_validate_file`), each returning a `(ok, error, metadata)` tuple. Reached from api.py through the `get_validator` dependency rather than inline construction. Distinct from `llm_validator.py` (which health-checks LLM services at startup).

**Reddit path rebuilt on PRAW (#59, #63).** The unauthenticated `httpx.get` to `reddit.com/r/<name>/about.json` is gone — Reddit 403s every such request, and the old 403 branch reported "is private", so every public subreddit read as private and no Reddit source could be added at all. `_validate_reddit` keeps its name (a test calls it directly, in a file `verify.sh` never executes) and is now a composition over parse → credential-gate → build-client → probe → interpret. Parse and interpret are pure and network-free; only the probe touches the wire.

Nine outcomes are told apart where one message served before (Principle II): success, absent subreddit (`NotFound`/`Redirect`), private-or-quarantined (`Forbidden`), legally unavailable, rate-limited, invalid-or-expired credentials, network failure, and two auth shapes that escape a naive enumeration — `OAuthException`, which subclasses `PrawcoreException` directly rather than `ResponseException` so a status-code branch cannot see it, and a bare `KeyError` from prawcore's authorization mapping, which holds `403` plus two OAuth strings and no `401`.

**Credentials are gated before any network call.** `Config` expands credentials with `os.environ.get(env_var, value)`, so an unset variable leaves the literal truthy string `"env:REDDIT_CLIENT_ID"` — handing that to Reddit earns a 401 and reports credentials *invalid* on a machine that has *none*. Empty and `env:`-prefixed credentials are rejected up front, reusing the `startswith("env:")` shape `Config.validate()` already used.

**The probe is bounded, because PRAW is not.** `prawcore.const.TIMEOUT` is 16s, computed at import and bound as a default argument value in `prawcore/sessions.py`, so setting `PRAWCORE_TIMEOUT` at runtime or patching the constant reaches nothing. The client is built with `check_for_updates=False` (`praw.Reddit.__init__` otherwise reaches `pypi.org` via `update_checker`) and a mounted transport adapter carrying the validator's own deadline. Retry suppression overrides prawcore's sleep and should-retry hooks rather than its retry *count* — lowering the count made prawcore sleep 2–4s before the first request, since it sleeps ahead of every attempt and a lowered counter reads as "retries already spent". What is bounded is each socket operation plus request admission, not total elapsed time; the outer `asyncio.wait_for` at the API layer bounds the request.
