---
type: design
date: 2026-10-11
title: "Design: refetch-title-only"
description: "The HN discussion backfill recovers exactly the stories the reader already sees as title-only, without analysing a year of never-shown items into the unread list."
purpose: "The recorded design of the refetch-title-only work order, read by its plan and its builders."
producer: cli:shape
---

# Design: refetch-title-only

## Purpose

The HN discussion backfill recovers exactly the stories the reader already sees as title-only, without analysing a year of never-shown items into the unread list.

## Form

get_unreadable_content (storage.py) gains a title_only_flagged keyword, default False; when True its paging query adds AND json_extract(c.analysis, '$.title_only') = 1, so only flagged rows are paged and the readability filter and limit apply to them as today. run_refetch (refetch.py) takes the same keyword and passes it through, and the refetch command (__main__.py) exposes it as --title-only, so a dry run reports the narrowed count and cost estimate.

## Commitments

- With --title-only, refetch selects only non-archived, unreadable rows of the given type whose analysis records title_only = true, oldest first, up to --limit; rows with title_only false or no title_only key are never selected.
- Without --title-only, the selection is exactly what it is today, rows with no title_only key included.
- refetch --dry-run --title-only reports the narrowed selection's count and cost estimate.
- On <host> after deploy, refetch --type rss --title-only --dry-run selects the 39 flagged HN items, and the real run reports how many were recovered from their discussion.

## Sacrifices

- The 1,304 unflagged HN rows that were never analysed, and the 4 with title_only false, stay as they are.

## Risk

A JSON boolean stored as true must match json_extract(...) = 1; a test with a real database stores a flagged row through the normal analysis path and asserts it is selected.

## Licensed by

daemon/src/prismis_daemon/storage.py:723
