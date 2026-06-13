---
type: architecture
subtype: boundaries
project: "prismis"
status: active
created: "2026-04-07"
updated: "2026-05-20"
last_change: "task 3.1 — CLI ↔ /api/entries limit-ceiling boundary documented; symmetric client-side input-validation guards (`limit < 1` + `limit > 3333`) mirror the server's Pydantic `le=10000` constraint accounting for CLI's `limit * 3` overshoot formula"
tags: [architecture, boundaries]
---

# Boundaries

Interface contracts between components and external systems.

## Daemon ↔ llm-core

**Between:** Daemon consumers ↔ llm-core Python package
**Contract:** Consumers call `complete(prompt=, system_prompt=, service=, temperature=, json=)` and receive `CompleteResult` with `.text`, `.tokens.input`, `.tokens.output`, `.cost`, `.model`, `.duration_ms`. Health check via `health_check(service=)`. All LLM errors surface as `ProviderError`.
**Constraints:** Single-turn only — no messages array, no chat. Service name must exist in ~/.config/llm-core/services.toml. Cost may be None if pricing.toml missing for model.

## Daemon ↔ apiconf

**Between:** llm-core (via daemon) ↔ apiconf
**Contract:** llm-core calls `load_api_key(key_name)` which reads from ~/.config/apiconf/config.toml [keys.{name}].value.
**Constraints:** No env var override for apiconf config path. Tests must use real config or skip.

## TUI ↔ Daemon API

**Between:** Go TUI ↔ FastAPI REST API
**Contract:** HTTP client at tui/internal/api/client.go connects to daemon API. Auth via X-API-Key header. JSON request/response.
**Constraints:** API key must match daemon config [api].key. API bound to configured host/port.

## CLI ↔ Daemon API (limit-ceiling contract)

**Between:** Python CLI ↔ FastAPI `GET /api/entries` (and any other endpoint with a Pydantic `limit` constraint)
**Contract:** `GET /api/entries` enforces a server-side Pydantic `le=10000` constraint on the `limit` query parameter. CLI batch-iteration commands that fetch candidates via this endpoint with a `limit * N` overshoot formula (where `N > 1`) MUST guard the user-facing `--limit` value against the effective ceiling `10000 / N` BEFORE calling `APIClient.get_content()`. Symmetric input-validation guards live at the function-top of each batch command, mirroring the priority/filter validation pattern: a lower-bound `< 1` guard (short-circuits with "No items need extraction" message, exit 0 — the no-op path; honors plan-documented `--limit 0` behavior since the slice path `[:0]` is unreachable through the server's `ge=1` constraint) and an upper-bound `> ceiling` guard (clear red message naming the ceiling value and explaining the formula, exit 1 — the out-of-range path). The two guards have different exit codes by design (no-op vs out-of-range) and share form (P15).
**Constraints:** Adding a new batch-iteration CLI command that consumes `/api/entries` requires verifying the effective ceiling against the current `limit * N` overshoot factor. Server-side `le=10000` is the authoritative ceiling — do not modify it; only the client-side guard adapts. quality.md `## Learned Patterns` 2026-05-19 "CLI batch-iteration limit floor" entry documents the floor; the 2026-05-20 task 3.1 build phase added the symmetric ceiling guard with `> 3333` for the `limit * 3` overshoot in `cli/src/cli/extract.py`. Future commands with different overshoot factors must compute their own ceiling. Demo-verified: `--limit 3333` passes the guard; `--limit 3334` fires it with exit 1; `--limit 10000` same path.

## API ↔ Consumers (datetime wire format)

**Between:** Daemon API (FastAPI/Pydantic) ↔ all consumers (TUI, CLI, web, external clients)
**Contract:** Every `datetime` field in API JSON responses MUST serialize as RFC3339: `T` separator between date and time, explicit timezone offset (`+00:00` or `Z`). No space-separator, no naive (timezone-less) timestamps on the wire. Enforced via Pydantic `json_encoders` on response models in `api_models.py` (`_rfc3339()` helper appends `Z` to naive datetimes so values from `CURRENT_TIMESTAMP` rows still round-trip as valid RFC3339). Consumers MUST parse using `time.RFC3339` (Go) or `datetime.fromisoformat()` (Python 3.11+) — no fallback layout lists.
**Constraints:** Paired with the storage-layer decision in `decisions.md` "Storage datetime ISO-string convention" — the storage layer binds datetime parameters as ISO strings (`datetime.now(timezone.utc).isoformat()`); the API layer re-serializes to RFC3339 on the wire regardless of stored representation. A future consumer that implements its own fallback layout list is absorbing a symptom; the contract is explicit and enforced at the boundary. As of task 2.11, `/api/entries` and `/api/search` flow through `ContentResponse`, `/api/audio/briefings` flows through `AudioBriefingResponse`, and `/api/entries/{content_id}` flows through `ContentItemModel` (all Pydantic-routed via `.model_dump(mode="json")`) — there are no remaining raw-dict response paths emitting datetime fields.

**INV-API-TS-4 (Pydantic-routed responses for content endpoints):** Every API list/detail endpoint that returns content data MUST flow through a Pydantic response model (`ContentResponse`, `ContentItemModel`, or a named sibling model with the same `@field_serializer` decorators applied). Raw-dict pass-through on response paths that emit datetime fields is prohibited. `/api/entries` and `/api/search` route through `ContentResponse` as of task 2.8; `/api/audio/briefings` routes through `AudioBriefingResponse` as of task 2.10 (`api.py:1337` returns `AudioBriefingResponse(...).model_dump(mode="json")`); `/api/entries/{content_id}` (`get_entry_summary`) routes through `ContentItemModel` as of task 2.11 (`api.py:1022-1028`, both branches — full and lightweight-with-`exclude={"content"}`). Future endpoints adding content responses must extend `ContentResponse` or define an equivalent model — this is the structural gate that makes INV-API-TS-1 a compile-time guarantee rather than a convention. The three datetime-bearing response models in `api_models.py` (`SourceResponse`, `AudioBriefingResponse`, `ContentItemModel`) all use Pydantic V2 `@field_serializer` decorators delegating to `_rfc3339`; the deprecated V1-compat `model_config = {"json_encoders": ...}` mechanism has been fully removed (zero references in `api_models.py`).

**INV-OL-1/OL-2/OL-3 (encoder narrowing contract at definition site):** `_rfc3339` in `api_models.py` carries three `@typing.overload` stubs immediately before the implementation: `(datetime) -> str`, `(None) -> None`, `(datetime | None) -> str | None`, in narrowest-to-widest order. This narrows the encoder return type for callers based on argument type rather than requiring each non-Optional `@field_serializer` to annotate `-> str` at the callsite (task 2.10's symptom-level pattern). Adding new non-Optional datetime serializers requires no per-callsite type discipline — pyright resolves `_rfc3339(datetime)` to `str` via the first overload. Structural tests at `daemon/tests/unit/test_rfc3339_overload_stubs_unit.py` protect the three invariants via AST inspection (overload count, signature ordering, `overload` imported from `typing`). Pyright 1.1.409 in basic mode is the configured verifier (`cd daemon && uv run pyright`); daemon-wide baseline at 18 errors documents the regression yardstick.

## Daemon ↔ SQLite

**Between:** Storage layer ↔ SQLite database file
**Contract:** Single writer (daemon). Multiple readers (API, TUI direct reads). Schema managed by database.py + schema.sql.
**Constraints:** File at ~/.local/share/prismis/prismis.db (XDG_DATA_HOME). No concurrent write support — SQLite handles locking.

## Config Migration Contract

**Between:** migrate-config command ↔ filesystem
**Contract:** Reads ~/.config/prismis/config.toml, detects one of three states — already dual-service (no-op), post-llm-core stale (`service =` → rename to `light_service =`), or pre-llm-core (`provider/model/api_key` → rewrite to `light_service = "{service_name}"`). In every non-no-op branch, idempotently appends `[services.prismis-openai-deep]` to ~/.config/llm-core/services.toml so the final state of a single run is a loadable dual-service config.toml plus a services.toml with both light and deep entries. Atomic temp-file writes for config.toml (`.toml.tmp` → rename). Also creates ~/.config/apiconf/config.toml and ~/.config/llm-core/pricing.toml on first run if missing.
**Constraints:** Idempotent on re-run — existing files and existing section headers are preserved and reported as skipped. Never overwrites user-customized content outside the target `[llm]` section or target service entry. A single invocation from any starting format converges to the final dual-service shape; users never need to run migrate-config twice. Old api_key with "env:" prefix resolved from environment at migration time. migrate-config bypasses startup config validation via the typer callback subcommand guard.

## Daemon ↔ Dual-Service LLM Config

**Between:** Daemon consumers ↔ Config.llm_light_service / Config.llm_deep_service
**Contract:** `Config.llm_light_service: str` is required — it routes all routine LLM calls (summarization, evaluation, audio script, context analysis/auto-update, api context analyzer). `Config.llm_deep_service: str | None = None` is optional and gates high-priority deep extraction; when None, deep extraction is disabled at runtime. `Config.auto_extract: str = "none"` is the threshold (`"none" | "high" | "all"`) controlling when deep extraction runs. Startup validator runs `validate_llm_services(light, deep)` — light failure `sys.exit(1)`, deep failure warning-only with runtime deep extraction disabled.
**Constraints:** No backward-compat for the old `llm_service` field — any reference raises `AttributeError`. Loader rejects configs lacking `light_service` with a directional migrate-config hint. Deep failure must NEVER be fatal (graceful degradation invariant).

## Daemon ↔ Deep Extractor

**Between:** Orchestrator / API server ↔ ContentDeepExtractor instance
**Contract:** `ContentDeepExtractor(service_name: str)` constructor takes the deep service name. `extract(content: str, title: str, url: str) -> dict | None` returns `{synthesis: str, quotables: list[str], model: str, extracted_at: str}` on success, `None` on empty content or malformed JSON. Raises `CircuitOpenError(RuntimeError)` when the deep circuit is open. Raises generic exceptions on LLM/provider failures. Reasoning-class models (gpt-5-mini and o-series) require omitting the `temperature` argument from `complete()` — passed via `llm_core/providers/openai.py:40` guard `if request.temperature is not None`.
**Constraints:** Single instance shared between orchestrator (constructor injection) and API endpoint (`app.state.deep_extractor`). `None` instance means deep extraction disabled — orchestrator skips the gate, API returns 503 `ServiceUnavailableError`. Deep extraction failure must NEVER block item storage in the pipeline path (orchestrator's outer try/except enforces INV-002). Synthesis text combined with light summary for embedding generation when present. Skepticism is source-internal only — no external knowledge, no reader-verification asks.

## API Server — Deep Extraction Endpoint

**Between:** API clients (TUI, CLI, curl) ↔ POST /api/entries/{content_id}/extract
**Contract:** Auth via `X-API-Key` header. Returns `{success: true, message: str, data: {deep_extraction: {synthesis, quotables, model, extracted_at}}}` on 200. Idempotent — `analysis.deep_extraction` is checked BEFORE the extractor-configured check (task 1.3 reorder), so cached returns succeed even when `llm.deep_service` is unconfigured (INV-004 observable in unconfigured environments). Errors routed by exception type with structured `reason` sub-codes on 503: `NotFoundError` → 404 (unknown content_id); `CircuitOpenError` → 503 `ServiceUnavailableError(reason="circuit_open")`; extractor-not-configured (`app.state.deep_extractor is None`) → 503 `ServiceUnavailableError(reason="not_configured")`; generic `Exception` → 500 `ServerError`. The `reason` field rides in the response body for client branching (TUI maps to distinct user messages).
**Constraints:** INV-004 holds for sequential callers and is now observable regardless of deep_service config (cached read does not require the extractor). Concurrent first-callers within the ~17s LLM-call window can both pass the idempotency check before either writes — bounded double-bill (~$0.02–0.05 per occurrence) tracked at gh#19 with three documented fix options. Embedding regeneration on success is best-effort (failure does not fail the request).
