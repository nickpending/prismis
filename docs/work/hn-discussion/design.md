---
type: design
date: 2026-10-10
title: "Design: hn-discussion"
description: "A Hacker News story is read with its discussion, as a Reddit post already is, and a story whose article is blocked or gone is still summarized and scored from what people said about it, never passed off as the article."
purpose: "The recorded design of the hn-discussion work order, read by its plan and its builders."
producer: cli:shape
---

# Design: hn-discussion

## Purpose

A Hacker News story is read with its discussion, as a Reddit post already is, and a story whose article is blocked or gone is still summarized and scored from what people said about it, never passed off as the article.

## Form

1. Shared discussion block. The discussion header (readability.py:35, today named for Reddit) becomes the one DISCUSSION_HEADER both fetchers write, with Reddit's comment formatting (author line, quoted body) moved beside it as one helper both use.

2. HN comments. A small hackernews module fetches a story's top comments from HN's official Firebase API: item/<id>.json for the story (its `kids` are in HN's ranked order, measured 2026-10-10, and its `text` is an Ask/Show HN body), then each of the first N kids, skipping deleted and dead ones, converting their HTML to plain text, under the shared http deadline the other fetchers use. N is [hackernews] max_comments (default 5, validated like reddit_max_comments at config.py:109). The RSS fetcher (fetchers/rss.py:255) recognizes an HN story by its entry's comments link (news.ycombinator.com/item?id=<id>), whatever HN feed it came from, and after the article step appends the discussion block; an Ask/Show HN self post uses the item's text as its body. A failed comment fetch leaves the item as it would have been and records comments_outcome {outcome: fetch_failed, detail}, as Reddit does.

3. Discussion basis. readability's link-without-article rule (readability.py:96) changes meaning: a link line followed only by a discussion block is readable when the discussion itself has prose, and stays title-only (content:link_without_article) when it has none. title_only_reason (analysis.py:52) stays the one producer; build_llm_analysis (analysis.py:78) records content_basis = "discussion" when the readable text is discussion only (article absent), and nothing when the article is present.

4. Honest summary. For a discussion-basis item the summarizer receives a note that the article could not be fetched and the text is reader discussion; it summarizes the discussion as discussion, and the substantive paragraph (summarizer.py:336) for that case asks whether the discussion says something substantive about the story rather than whether the piece is present, so the model backstop still catches empty or joke-only threads without rejecting every rescue.

5. Where the reader sees it. The API returns content_basis with the item's analysis (additive); the TUI reader (tui/internal/ui/reader.go:82, beside title_only) and the CLI's item view (cli/src/cli/get.py:71) show 'from the discussion — article unavailable'.

## Commitments

- An HN RSS item carries its top max_comments comments, in HN's ranked order, under the shared discussion header, with deleted and dead comments skipped and HTML converted to text.
- An Ask or Show HN self post's body is the item's own text.
- A story whose article fetch failed but whose discussion has prose is stored readable with content_basis = discussion and summarized as discussion, for HN and Reddit; one with no discussion prose stays title-only as content:link_without_article.
- For a discussion-basis item, the model's substantive verdict is about the discussion, so a substantive thread is not marked model:not_substantive and an empty one still is.
- A failed HN comment fetch records comments_outcome and leaves the item as it would otherwise be.
- The TUI reader and the CLI item view show 'from the discussion — article unavailable' for a discussion-basis item.
- HN API behavior is tested from recorded cassettes with no network.

## Sacrifices

- Each HN story costs up to 1 + max_comments extra API calls per fetch cycle.
- Summaries of discussion-basis items describe what commenters said, which can be wrong about the article; the basis marker is the reader's warning.

## Risk

If the content_basis marker failed to reach the reader, a discussion summary would read as an article summary; the TUI and CLI tests assert the marker renders for a discussion-basis item.

## Licensed by

daemon/src/prismis_daemon/fetchers/reddit.py:352
