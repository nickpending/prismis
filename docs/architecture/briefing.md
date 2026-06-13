Prismis is a personal content intelligence system that fetches, summarizes, and prioritizes articles using LLMs, surfacing them via REST API, TUI, and audio briefings. Key components include the Daemon (fetch/process/store), TUI (read/triage), CLI (admin), llm-core (LLM abstraction), and apiconf (API key management). Data is stored in a SQLite database, with configuration managed via XDG standards.

Key data flow: Fetchers retrieve content → Summarizer & Evaluator process via LLM → Storage persists → API serves to TUI/CLI. LLM calls are single-turn completions via llm-core, routed by service name (e.g., "prismis-openai") from services.toml. The Daemon runs continuously, driven by APScheduler.

**Contracts:**
- **Daemon ↔ llm-core:** `complete(prompt=, system_prompt=, service=, temperature=, json=)` with service name in services.toml.
- **Daemon ↔ apiconf:** llm-core calls `load_api_key(key_name)` from ~/.config/apiconf/config.toml.
- **TUI/CLI ↔ Daemon API:** HTTP client with X-API-Key header matching daemon config [api].key.
- **API ↔ Consumers (datetime):** Every datetime field in API JSON responses MUST serialize as RFC3339 (T separator, explicit timezone offset).
- **Daemon ↔ SQLite:** Single writer (daemon), multiple readers. File at ~/.local/share/prismis/prismis.db.
- **CLI ↔ Daemon API (limit-ceiling):** `GET /api/entries` enforces `le=10000` on `limit`. CLI batch commands with `limit * N` overshoot MUST guard `--limit` against the effective ceiling `10000 / N`.
- **Daemon ↔ Dual-Service LLM Config:** `Config.llm_light_service` is required; `Config.llm_deep_service` is optional.

**Gotchas:**
- All fetcher datetime emissions are tz-aware.
- API list/detail endpoints that return content data MUST flow through a Pydantic response model (`ContentResponse`, `ContentItemModel`, etc.) with `@field_serializer` decorators.
- `_rfc3339` in `api_models.py` carries `@typing.overload` stubs for encoder narrowing.
- Missing `rev=` in `daemon/pyproject.toml` `[tool.uv.sources]` causes resolver drift.
- SQL parameterization in storage.py uses heredoc + `query += " AND ... ?"` accretion.
- Web card click delegation uses structural scoping on `.item-summary`, not per-element stopPropagation.
- The extract endpoint's cached-return runs before the extractor-configured check.
- Deep extraction failure must NEVER block item storage in the pipeline path.
- `Config.llm_light_service` is required; failure is fatal. `Config.llm_deep_service` is optional; failure is warning-only.
- Reasoning-class models (gpt-5-mini and o-series) require omitting the `temperature` argument from `complete()`.
- The migrate-config command is idempotent and converges to the dual-service shape.
