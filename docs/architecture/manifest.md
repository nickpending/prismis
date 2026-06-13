---
type: manifest
project: prismis
generated: "2026-06-12"
source: /Users/rudy/development/projects/prismis/docs/architecture
---

## Components

- **Daemon** — Python daemon: fetches content, LLM summarization/evaluation/deep-extraction, SQLite storage, REST API
- **Fetchers** — Source adapters: RSS, Reddit, YouTube, file ingestion
- **Summarizer** — LLM-powered content summarization with structured insights
- **Evaluator** — LLM-powered content prioritization against user interests
- **Deep Extractor** — Second-tier LLM synthesis for HIGH-priority items; source-type exclusion (`deep_extract_exclude`, ships `["reddit"]`) skips low-signal sources regardless of priority
- **Context System** — User interest profile and auto-update from feedback patterns
- **Circuit Breaker** — Service-keyed quota protection for LLM calls
- **API Server** — FastAPI REST API for content access, search, on-demand deep extraction
- **Storage** — SQLite persistence: content, metadata, deduplication, archival, on-demand analysis patching
- **Embeddings** — Local offline embedding generation (all-MiniLM-L6-v2, 384-dim) for semantic search
- **Audio Briefings** — Spoken daily briefings via LLM script generation + lspeak TTS
- **Notifier** — Desktop notifications for newly-fetched HIGH-priority content
- **Observability** — JSONL event-tracking logger; cross-cutting across daemon modules
- **Source Validator** — Pre-add validation of RSS/Reddit/YouTube/file sources before DB insert
- **TUI** — Go terminal UI for reading/triaging; `:extract` triggers deep extraction; renders Summary → Deep Synthesis → one deduped Quotes section
- **Web View** — Single-page web frontend served from daemon; detail panel renders one deduped Quotes section; click target structurally scoped
- **CLI** — Python CLI for installation, admin, and batch ops against the daemon API (extract, list, search, analyze, migrate-config)
- **LLM Validator** — Dual-service startup health check: light fatal, deep non-fatal

## Where to look

- Overview: /Users/rudy/development/projects/prismis/docs/architecture/architecture.md
- Components: /Users/rudy/development/projects/prismis/docs/architecture/components.md
- Decisions: /Users/rudy/development/projects/prismis/docs/architecture/decisions.md
- Contracts: /Users/rudy/development/projects/prismis/docs/architecture/boundaries.md
- Orientation: /Users/rudy/development/projects/prismis/docs/architecture/briefing.md

## Dependencies

**External packages:** llm-core (Python, local dev path), apiconf, APScheduler, FastAPI, typer, rich, httpx, sentence-transformers (all-MiniLM-L6-v2)

**Infrastructure:** SQLite + sqlite-vec (vec_content vec0 table), XDG config dirs, lspeak (TTS)

## Key patterns

- Service-based LLM routing — consumers reference service name, resolved via services.toml at call time
- Single-turn LLM only — all calls are stateless completions, no chat
- Dual-service LLM config — `llm_light_service` (required) for routine calls, `llm_deep_service` (optional) for HIGH-priority extraction; `auto_extract` threshold gates when deep runs; deep failure non-fatal (graceful degradation)
- Deep-extract source exclusion — `_should_deep_extract` short-circuits on `source_type in deep_extract_exclude` before priority gating
- Service-keyed circuit breaker — per-service quota protection in module-level registry
- Typed exception routing in api.py — error class drives status code (NotFoundError → 404, CircuitOpenError → 503, generic → 500)
- API ↔ consumers datetime wire format — RFC3339 on every datetime field via Pydantic `@field_serializer` (`_rfc3339`); content endpoints route through Pydantic response models, no raw-dict pass-through (INV-API-TS-4)
- Cosine similarity from L2 distance — vec_content uses vec0 default L2; storage converts via `1 - d²/2` for unit-normalized all-MiniLM-L6-v2 embeddings
- Unified quotes surface — TUI + web render one deduped Quotes section (light quotes + deep quotables)
- XDG configuration — all config under ~/.config/{prismis,llm-core,apiconf}/
</content>
</invoke>
