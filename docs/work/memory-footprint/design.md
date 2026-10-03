---
type: design
date: 2026-10-03
title: "Design: memory-footprint"
description: "A client listing content costs the daemon memory proportional to the rows it asked for and the fields a list shows, never to the size of the corpus, so the remote TUI, CLI and web UI cannot push cerebro into the OOM killer, and the margin holds as the corpus grows."
purpose: "The recorded design of the memory-footprint work order, read by its plan and its builders."
producer: cli:shape
---

# Design: memory-footprint

## Purpose

A client listing content costs the daemon memory proportional to the rows it asked for and the fields a list shows, never to the size of the corpus, so the remote TUI, CLI and web UI cannot push cerebro into the OOM killer, and the margin holds as the corpus grows.

## Form

One bounded list query, one slim list shape, and detail on demand.

1. `daemon/src/prismis_daemon/storage.py` gains `get_content_list`, built on the shape of `get_content_by_priority`: one SELECT over `content c JOIN sources s` whose WHERE carries every filter `get_content` accepts (since on `datetime(c.fetched_at)`, archived, source substring, `_kind_filter_sql`, priority IN, `c.read = 0` for unread_only, `c.user_feedback = 'up'` for interesting_override, the condition `get_flagged_items` uses), whose ORDER BY is the requested sort in SQL (priority: `CASE c.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 WHEN 'low' THEN 2 ELSE 3 END, c.published_at DESC`; date: `c.published_at DESC`; unread: `c.read ASC, c.published_at DESC`), and which ends in `LIMIT ?`. It takes a `view` argument. For `view="list"` it selects every content column except `content` and `analysis`, plus `json_object(...)` over `json_extract` of the list keys (kind, kind_confidence, title_only, metrics, metadata, matched_interests, preference_influenced) as the item's `analysis`, and `json_extract(c.analysis, '$.deep_extraction') IS NOT NULL` as `has_deep_extraction`, both guarded by `json_valid` the way `get_distinct_kinds` guards its read. For `view="list"` the full `analysis` JSON is never read. For `view="full"` it selects `c.*` as today, still under the same WHERE, ORDER BY and LIMIT.

2. `daemon/src/prismis_daemon/api.py` `get_content` makes one `storage.get_content_list` call in place of its four branches (get_flagged_items, the per-priority unread loops, and both `get_content_since` calls) and drops the Python sort. With `skip_dedup=False` it asks for `max(limit, 200)` rows, dedups the first 200 as today, then slices to `limit`. It gains an additive query parameter `view`, per constitution VIII: `full`, the default, keeps today's response fields; `list` returns the slim shape. `compact` keeps its field set. Every list item goes through `ContentItemModel` (INV-API-TS-4), dumped with `exclude={"content"}` for `view=list`; `ContentItemModel` in api_models.py gains `has_deep_extraction: bool = False`. `get_entry_summary` (GET /api/entries/{id}) is unchanged and stays the only way to get content or the full analysis.

3. `tui/internal/api/client.go` sends `view=list` from `FetchEntries` and `FetchEntriesSince`, and gains `FetchEntry(id)` calling `GET /api/entries/{id}?include=content` and returning one full `ContentItem`; `ContentItem` gains `HasDeepExtraction`. `tui/internal/ui/model.go` marks items merged from a list as not hydrated, and, in remote mode, every action that reads `item.Content` (the reader open in reader.go, copy, and the `FabricMsg` handler in model.go) goes through one model helper that calls `FetchEntry` when the item is not hydrated, replaces that item's `Content` and `Analysis` in `m.items`, and marks it hydrated; a fetch error shows in the status line the way other API errors do and leaves the item unhydrated. Local mode (`tui/internal/db`) is unchanged.

4. `cli/src/cli/extract.py` requests `view=list` (through a `view` argument on `APIClient.get_content`) and keeps candidates whose `has_deep_extraction` is false instead of reading `analysis.deep_extraction`. `cli/src/cli/list.py` and the web UI need no change: the list keys they read are in the slim shape.

5. `docs/architecture/boundaries.md` gains a list-shape contract beside the limit-ceiling one: GET /api/entries reads at most the rows it returns; `view=list` never returns `content` and returns `analysis` cut to the list keys; `view=full` is kept for compatibility; full items come from GET /api/entries/{id}?include=content.

6. Tests: a storage test plants rows whose `content` and `analysis.full_text` are 50 KB each, calls `get_content_list` with `limit=10` and `view="list"`, and asserts ten rows, none carrying content or full_text, ordered per each sort, and flagged rows selected by user_feedback; a request test through the real FastAPI app over a 2,000-row database of such rows measures `tracemalloc` peak for `GET /api/entries?limit=10000&view=list` and for `GET /api/entries?limit=50` and asserts it stays under a bound the old path exceeds (shown red against the old `get_content`); a TUI test drives the reader open, copy and Fabric on an unhydrated item against an `httptest` server and asserts one detail request and the content shown; a CLI test asserts extract skips items with `has_deep_extraction` true.

## Commitments

- GET /api/entries reads at most `limit` rows (200 when dedup is on) in one SQL query that filters, sorts and limits in SQLite; no list path loads the whole corpus.
- `view=list` responses never carry `content`, and carry `analysis` cut to kind, kind_confidence, title_only, metrics, metadata, matched_interests and preference_influenced, plus a top-level `has_deep_extraction`.
- `view` is an additive parameter on GET /api/entries; `view=full` stays the default with today's fields (constitution VIII), and every remote TUI is updated at deploy so none requests a large full list afterwards.
- Full content and full analysis come from GET /api/entries/{id}?include=content, which the remote TUI calls when an item is opened, copied or sent to Fabric, as the web UI already does.
- The server's limit ceiling of 10,000 and INV-API-TS-4's Pydantic routing stay as they are.
- Local-mode TUI, the CLI's local commands, search and reports are unchanged.
- The proof is daemon RSS sampled on cerebro during the remote TUI's startup request and a fetch cycle, plus an in-process tracemalloc bound that the old path fails.

## Sacrifices

- Opening an item in the remote TUI costs one HTTP round trip the first time.
- A remote-TUI item that was never opened has no content cached, so copying it needs the daemon reachable.
- With unread_only or the interesting filter and a date sort, the list now returns the newest unread items across priorities instead of filling high priority first (or taking the newest-fetched flagged items) and sorting that, and the unread sort orders by the `read` column instead of a `read_at` key no row has.
- A new list view that reads another analysis key needs that key added to the projection in storage.py.
- The ~450 MB torch import baseline stays; it is flat in corpus size.
- A client that still asks for `view=full` with a large limit (an un-updated TUI, or a hand-written request) can still spike the daemon by the size of the rows it asked for, though no longer by the whole corpus.

## Risk

If the projection misses a key a list view reads, that view shows empty for remote clients (a missing badge or metric) until the key is added; if the TUI's hydrate step misses a path that reads `item.Content`, that path shows empty content in remote mode rather than failing loudly.

## Licensed by

daemon/src/prismis_daemon/static/index.html:1647
