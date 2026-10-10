---
type: design
date: 2026-10-10
title: "Design: refetch-hn-discussion"
description: "A Hacker News story stored title-only before hn-discussion shipped can be recovered by `prismis-daemon refetch` exactly as a new story would be on its first fetch: from its article if that now loads, otherwise from its discussion, honestly marked as such."
purpose: "The recorded design of the refetch-hn-discussion work order, read by its plan and its builders."
producer: cli:shape
---

# Design: refetch-hn-discussion

## Purpose

A Hacker News story stored title-only before hn-discussion shipped can be recovered by `prismis-daemon refetch` exactly as a new story would be on its first fetch: from its article if that now loads, otherwise from its discussion, honestly marked as such.

## Form

1. One discussion step, shared. The RssFetcher's _add_hn_discussion method (fetchers/rss.py) becomes public, as add_hn_discussion, with the same signature and behaviour; the live loop calls it as it does now, and refetch calls the same method through orchestrator.rss_fetcher, so the fetcher's [hackernews] max_comments and its Ask/Show self-post handling apply unchanged.

2. The story id from stored content. hackernews.py gains a function that finds the first news.ycombinator.com/item?id=<id> link inside a text, built on the same pattern story_id uses without its anchors, so the two cannot disagree on what an HN item link is.

3. Refetch of an HN row. In the rss branch of the _reextract_item helper (refetch.py), the row's stored content is searched for that id. With no id, the branch runs exactly as today. With an id, a self post (the row's url is the item link itself) keeps its stored content as the starting body instead of extracting an article; any other story re-extracts its article as today; then add_hn_discussion is applied to the result, and a failed discussion read puts comments_outcome into the item's analysis beside fetch_outcome. _refetch_one is unchanged in shape: a readable result is analysed through analyze_and_store_item, which sets content_basis = "discussion" when the readable text is the discussion only, and an unreadable one is rewritten title-only, now keeping comments_outcome when there is one.

## Commitments

- Refetching a stored title-only HN story whose article still fails and whose discussion has comments stores it readable, with content_basis = "discussion" and a summary of the discussion, proven through run_refetch against a real test database with the article and HN API responses recorded.
- A story whose article now loads is recovered from the article plus its discussion, with no content_basis key, the same result a live fetch gives.
- A story with no comments, or whose discussion read fails, stays title-only; the failure is recorded on the item as comments_outcome, and the refetch report counts it as still title-only, not recovered.
- An rss row whose stored content holds no HN item link is refetched exactly as before this change.
- The discussion is fetched and appended by one method that both the live RSS fetch and refetch call; no copy of that logic exists in refetch.py.

## Sacrifices

- The story id is recovered from the stored content; a row whose content no longer holds the item link refetches article-only (none on cerebro on 2026-10-10: all 39 unreadable HN rows hold it).
- Items already readable but judged not substantive (8 HN items on cerebro) are not refetched, as refetch selects only unreadable content.

## Risk

The self-post branch: a story whose url is its own HN page must keep its stored body and take the item's text from the API, not be sent to article extraction against news.ycombinator.com; a test refetches a self post and asserts the item text is the body.

## Licensed by

daemon/src/prismis_daemon/refetch.py:193
