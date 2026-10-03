---
type: manifest
project: prismis
generated: "2026-10-03"
source: /Users/rudy/development/projects/prismis/docs/architecture
reconciled_at: d6b5a2cc7fda091581bfa31a553865ec6bb278b4
---

## Components

- **Daemon** — Python daemon: fetch, LLM summarize/evaluate/deep-extract, SQLite storage, REST API.
- **Fetchers** — Source adapters: RSS, Reddit, YouTube, static-URL change monitoring.
- **Summarizer** — LLM content summarization with structured insights.
- **Evaluator** — LLM content prioritization against user interests.
- **Deep Extractor** — Second-tier LLM synthesis for HIGH items; low-signal sources excluded.
- **Context System** — User interest profile, auto-updated from feedback.
- **LLM Client** — Direct openai-SDK client for every daemon LLM call.
- **Kind Classifier** — Optional ten-kind item tagging via OpenRouter Decisions; fail-open.
- **Circuit Breaker** — Service-keyed quota protection for LLM calls.
- **API Server** — FastAPI REST: content access, search, on-demand extraction.
- **Storage** — SQLite: content, dedup, archival; tz-aware (+00:00) timestamps, versioned migrations.
- **Embeddings** — Local all-MiniLM-L6-v2 (384-dim) for semantic search.
- **Audio Briefings** — Spoken daily briefings via LLM + TTS.
- **Notifier** — Desktop notifications for new HIGH items.
- **Observability** — JSONL event logger, cross-cutting across daemon modules.
- **Source Validator** — Pre-add validation of RSS/Reddit/YouTube/file.
- **TUI** — Go reading/triage UI; `:extract` triggers deep extraction.
- **Web View** — Single-page frontend served from daemon.
- **CLI** — Python admin/batch ops against the daemon API.
- **LLM Validator** — Dual-service startup health check (light fatal, deep non-fatal).
- **Refetch** — `refetch` backfill: re-extract and re-analyse unreadable stored items.
- **Secret Scan** — gitleaks gate (go.mod tool pin) in verify.sh and CI history scan.
- **Verify Chain** — `verify --chain`: real orchestrator run, throwaway DB, per-link report.

## Where to look

- Overview: /Users/rudy/development/projects/prismis/docs/architecture/architecture.md
- Components: /Users/rudy/development/projects/prismis/docs/architecture/components.md
- Decisions: /Users/rudy/development/projects/prismis/docs/architecture/decisions.md
- Contracts: /Users/rudy/development/projects/prismis/docs/architecture/boundaries.md
