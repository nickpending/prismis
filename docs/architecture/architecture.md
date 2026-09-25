---
type: architecture
subtype: overview
project: "prismis"
status: active
created: "2026-04-07"
updated: "2026-09-25"
tags: [architecture]
---

# Prismis Architecture

Prismis is a personal content intelligence system that continuously fetches articles from RSS, Reddit, YouTube, and local files, uses LLM-powered summarization and evaluation to prioritize content by user interests, and surfaces results through a REST API, TUI, and audio briefings. Three components — a Python daemon (fetch/process/store), a Go TUI (read/triage), and a Python CLI (admin) — share a SQLite database and XDG-based configuration.

## Principles

- **Single-turn LLM only** — All LLM calls are stateless completions via the daemon's own `llm_client` module, a thin direct wrapper around the openai SDK; no chat sessions or memory.
- **Service-based LLM routing** — Consumers reference a service name (e.g., "prismis-openai"), resolved at call time via services.toml. No hardcoded providers.
- **XDG configuration** — All config under ~/.config/prismis/, ~/.config/llm-core/, ~/.config/apiconf/. No dotfiles in home root.
- **SQLite as single source of truth** — All content state in one database file. No distributed state.
- **Daemon runs continuously** — APScheduler drives fetch cycles; single-shot mode (--once) for testing.

## Components

| Component | Purpose | Detail |
|-----------|---------|--------|
| Daemon | Content fetching, LLM processing, storage, API server | [components/daemon.md](components/daemon.md) |
| TUI | Terminal UI for reading and triaging content | [components/tui.md](components/tui.md) |
| CLI | Admin commands (install, migrate-config) | |
| openai (Python SDK) | Chat-completions client for every LLM call, wrapped by the daemon's own `llm_client.py` (complete(), health_check(), services.toml resolution) | External dep (PyPI); see [boundaries.md](boundaries.md) |
| apiconf | API key management | External dep |

## Key Decisions

See [decisions.md](decisions.md) for the full decision log.

## Boundaries

See [boundaries.md](boundaries.md) for interface contracts.
