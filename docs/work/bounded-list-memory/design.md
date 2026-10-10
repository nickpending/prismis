---
type: design
date: 2026-10-10
title: "Design: bounded-list-memory"
description: "No client request can push the daemon toward out-of-memory: a full-content list costs about what a slim one does, so an old TUI anywhere on the network is a slow request, not a crash."
purpose: "The recorded design of the bounded-list-memory work order, read by its plan and its builders."
producer: cli:shape
---

# Design: bounded-list-memory

## Purpose

No client request can push the daemon toward out-of-memory: a full-content list costs about what a slim one does, so an old TUI anywhere on the network is a slow request, not a crash.

## Form

1. Logging without buffering. The api.request middleware (api.py:113) stops reading response bodies: _item_count (api.py:98) and the body += chunk rebuild go. List handlers set request.state.item_count, and the middleware logs that value (absent means no count), so the console line and the obs_log event keep item_count for /api/entries and /api/search.

2. Streamed entries. Storage gains iter_content_list, an iterator over the same SQL get_content_list (storage.py:944) runs, fetching rows in small batches from one cursor; get_content_list becomes list(iter_content_list(...)) so both share one query. The /api/entries handler (api.py:879) validates its parameters exactly as today, then returns a StreamingResponse whose generator writes the envelope's opening, then each item (with kind, title_only, title_only_reason and the list view's cut applied per row by the existing helpers) serialized one at a time with the same JSON encoding the handler's dict return gets today, then the closing fields; the count goes on request.state when the stream ends. The deduplication path (skip_dedup=false) still reads its DEDUP_WINDOW-capped window before streaming. The storage connection stays open during the stream because FastAPI 0.142.3 runs request-scoped yield dependencies' exit after the response is sent (fastapi/routing.py request_response).

3. Proof by measurement. A probe script committed under daemon/scripts runs the full and slim 10,000-item requests through the app against a given database in separate processes and prints peak RSS and body size; the operator-run measurement on a copy of cerebro's database is recorded beside the work order, and a gated test asserts the streamed body equals the dict-built body for both views on a test database.

## Commitments

- A full-content GET /api/entries?limit=10000 on a copy of cerebro's database peaks within 100 MB of the view=list request, down from 2,954 MB.
- The JSON a client receives from /api/entries is unchanged for view=full and view=list: same envelope, same items in the same order, same fields and values, proven by a test that compares against the previous construction.
- The logging middleware reads no response body, and api.request events keep item_count for /api/entries and /api/search.
- Deduplication, filters, sorting, limits and errors behave as before: a validation error still returns the same 4xx envelope, not a broken stream.
- After deploy, cerebro's memory peak stays under 1.5 GB through a full-list request from an old client.

## Sacrifices

- An error after the stream has started (a database failure mid-cursor) can only end the stream early, not change the status code; such a response is truncated JSON the client fails to parse rather than a clean 500.
- Responses are sent chunked without a Content-Length.

## Risk

A per-item field computed differently in the stream than in the dict path would change what clients read; the equality test compares both constructions on the same data for both views.

## Licensed by

daemon/src/prismis_daemon/storage.py:944
