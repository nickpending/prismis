---
type: design
date: 2026-10-31
title: "Design: browser-ua"
description: "A source whose site rejects non-browser clients can be marked `naughty`, and from then on every HTTP request prismis itself makes for that source (its feed, its items' articles, its add-time validation) carries a current browser user-agent, so the source's items arrive with their content; the mark is stored with the source, set from the CLI and API, and shown wherever sources are listed."
purpose: "The recorded design of the browser-ua work order, read by its plan and its builders."
producer: cli:shape
---

# Design: browser-ua

## Purpose

A source whose site rejects non-browser clients can be marked `naughty`, and from then on every HTTP request prismis itself makes for that source (its feed, its items' articles, its add-time validation) carries a current browser user-agent, so the source's items arrive with their content; the mark is stored with the source, set from the CLI and API, and shown wherever sources are listed.

## Form

1. Storage. `sources` gains `naughty INTEGER NOT NULL DEFAULT 0` in schema.sql, and `_migrate_3_source_naughty` in database.py's `_MIGRATIONS` adds the column to existing databases (additive, self-contained DDL as `_migrate_2_drift_checks`; `_backup_beside` already backs up first). `add_source` takes `naughty: bool = False` and inserts it; `update_source` accepts `naughty` in `update_data` beside name and url; the two 10-column SELECTs become 11 and `_source_row_to_dict` maps `naughty` to a bool.

2. API. `SourceRequest` (api_models.py) gains `naughty: bool = False`, the update request gains `naughty: bool | None = None`, `SourceResponse` gains `naughty: bool`. The add handler passes it to `storage.add_source` and to validation; the update handler puts a non-None `naughty` into `update_data`. `get_sources` returns it. API.md's Create, List and Update Source show the field.

3. The user-agent. article_extractor.py holds `_BROWSER_USER_AGENT`, one current stable Chrome string beside `_USER_AGENT`, with a comment saying it is bumped by hand when Chrome moves, and one helper `user_agent(browser: bool) -> str` that returns the browser string when `browser` is true and today's identifier otherwise; it is the only place that chooses. `extract_article(url, *, allowed_private_hosts=(), browser_ua: bool = False)` sends `user_agent(browser_ua)`. `fetchers/rss.py` sets the feed GET's User-Agent per request to `user_agent(bool(source.get("naughty")))` and passes the flag to its extract_article call; `fetchers/file.py` does the same for its GET; `fetchers/reddit.py` passes the source's flag to extract_article for a post's linked article; `refetch.py` maps each row's `source_id` to its source's flag (from `get_all_sources`) and passes it. The validator's `_validate_rss` and `_validate_file` take `browser_ua` from the add request and send `user_agent(browser_ua)`, so a UA-sensitive feed can be added at all. PRAW and the YouTube lookup are untouched.

4. CLI. `prismis-cli source add` gains `--naughty`; a new `prismis-cli source update <id> [--name NAME] [--naughty/--no-naughty]` calls the existing PATCH; `source list` shows a seventh column, UA, reading `browser` or `prismis`; `--json` carries `naughty`. api_client.add_source posts the flag and gains update_source(id, name=None, naughty=None).

5. TUI. `Source` and `SourceRequest` in tui/internal/api/client.go gain `Naughty bool` (`json:"naughty"`), and the source modal's list marks a naughty source with `[browser UA]` after its name; the modal's add form does not set it (CLI and API do).

6. Docs. README's Sources section shows `prismis-cli source add --naughty <url>` with one line on when to use it; API.md documents the field; docs/architecture/components.md's fetcher entry names the per-source user-agent.

Acts on the gap: A per-source `naughty` flag stored in the sources row reaches every HTTP client prismis makes for that source through one helper, `user_agent(browser)` in article_extractor: extract_article takes a browser_ua keyword and the RSS, file and validator clients set their User-Agent from the same helper, so the setting the gap says is missing exists and is chosen in one place at every request.

## Commitments

- A source added with `naughty` (CLI flag or API field) is stored with naughty=1, survives a daemon restart, and GET /api/sources, `source list` and the TUI show it; an unmarked source stores naughty=0 and is listed as today.
- `prismis-cli source update <id> --naughty` and `--no-naughty` flip the stored flag through PATCH /api/sources/{id}, and a PATCH without the field leaves it unchanged.
- For a marked RSS source, the feed GET and every extract_article call for its items send `_BROWSER_USER_AGENT`; for an unmarked source they send what they send today; proven against a real local HTTP server that returns 403 to any user-agent containing 'prismis' or 'httpx' and 200 to the browser string, through the real RSSFetcher and the real add-source validation.
- A marked file source's GET sends the browser string; a Reddit post's linked article and a refetched RSS row send it when their source is marked.
- Adding a marked RSS source whose feed 403s the validator's identifier succeeds; adding the same feed unmarked is refused as today.
- An existing database migrates to the new column with its rows intact (user_version advances by one, every source keeps its fields, naughty reads 0), and a fresh database needs no migration.
- A TUI or CLI built before this change keeps working against the new daemon: the new field is optional on requests and additional on responses.
- ember verify passes.

## Sacrifices

- The browser user-agent string is a constant that goes stale as Chrome releases move; it is bumped by hand. (not a case: Chrome's reduced user-agent keeps the string's shape valid across releases, and the 141 'HTTP 403' items on <host> all failed under the prismis identifier, none under a browser string of any age; no observed case keys on the exact Chrome major.)
- The TUI shows the mark but cannot set it; setting is CLI and API only. (0 of 49 the 49 sources on <host>, the only install with history)

## Risk

A site that fingerprints beyond the user-agent header (TLS, cookies, JavaScript challenges) still refuses the daemon; the flag solves exactly the 403-on-identifier case the issue describes, and the test proves that case, not the harder ones. If the flag is read from the source dict in one fetch path and forgotten in another, that path silently keeps the old identifier; the commitments name every path and the test covers the RSS feed, the RSS item and validation.

## Licensed by

docs/constitution.json:1
