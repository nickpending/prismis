---
type: manifest
project: prismis
generated: "2026-06-12"
source: /Users/rudy/development/projects/prismis/docs/architecture
reconciled_at: 730e0bd8f4aa65a095e7f8839113c3a87f60cc1a
---

## Components

- **Daemon** — Python daemon: fetch, LLM summarize/evaluate/deep-extract, SQLite storage, REST API.
- **Fetchers** — Source adapters: RSS, Reddit, YouTube, file ingestion.
- **Summarizer** — LLM content summarization with structured insights.
- **Evaluator** — LLM content prioritization against user interests.
- **Deep Extractor** — Second-tier LLM synthesis for HIGH items; `deep_extract_exclude` skips low-signal sources.
- **Context System** — User interest profile, auto-updated from feedback.
- **Circuit Breaker** — Service-keyed quota protection for LLM calls.
- **API Server** — FastAPI REST: content access, search, on-demand extraction.
- **Storage** — SQLite: content, dedup, archival, analysis patching.
- **Embeddings** — Local all-MiniLM-L6-v2 (384-dim) for semantic search.
- **Audio Briefings** — Spoken daily briefings via LLM script + lspeak TTS.
- **Notifier** — Desktop notifications for new HIGH-priority content.
- **Observability** — JSONL event logger, cross-cutting across daemon modules.
- **Source Validator** — Pre-add validation of RSS/Reddit/YouTube/file sources.
- **TUI** — Go reading/triage UI; `:extract` triggers deep extraction.
- **Web View** — Single-page frontend served from daemon.
- **CLI** — Python admin/batch ops against the daemon API.
- **LLM Validator** — Dual-service startup health check (light fatal, deep non-fatal).

## Where to look

- Overview: /Users/rudy/development/projects/prismis/docs/architecture/architecture.md
- Components: /Users/rudy/development/projects/prismis/docs/architecture/components.md
- Decisions: /Users/rudy/development/projects/prismis/docs/architecture/decisions.md
- Contracts: /Users/rudy/development/projects/prismis/docs/architecture/boundaries.md
