---
type: design
date: 2026-10-07
title: "Design: title-only-misfires"
description: "Short genuine posts stay readable while junk stays title-only: what the code can see structurally (a Reddit link post without its article, a markdown link standing alone) is decided by code, and the model judges only whether the piece the title refers to is present."
purpose: "The recorded design of the title-only-misfires work order, read by its plan and its builders."
producer: cli:shape
---

# Design: title-only-misfires

## Purpose

Short genuine posts stay readable while junk stays title-only: what the code can see structurally (a Reddit link post without its article, a markdown link standing alone) is decided by code, and the model judges only whether the piece the title refers to is present.

## Form

Move the structural cases into the code check and narrow the model's question.

1. `daemon/src/prismis_daemon/summarizer.py`: the SUBSTANTIVE paragraph of the standard prompt (line 332) becomes the measured wording: '"substantive" says whether the text contains the piece itself (the article, post, announcement or release note the title refers to) rather than only material around it. Set it to true when the piece's own content is present, however short: a two-sentence release note or a one-paragraph announcement is substantive, and navigation around it does not change that. Set it to false when the piece itself is missing: only a link (with or without reader comments), a notice (JavaScript, cookies, bot check, login, error, paywall), navigation, interface labels or a site tagline, or a citation or listing without the work's content.' The diff prompt (line 399) is unchanged.

2. `daemon/src/prismis_daemon/readability.py`: a constant `REDDIT_DISCUSSION_HEADER = "## Discussion"` beside `REDDIT_LINK_PREFIX`; `readability_failure` (line 120) returns `link_without_article` when the content starts with the link line and the text between that line and the discussion header (or the end) is empty; `_word_count` (line 94) removes markdown links `[text](target)` whole, as it removes bare URLs, before counting.

3. `daemon/src/prismis_daemon/fetchers/reddit.py`: `_to_content_item` builds its discussion separator from `REDDIT_DISCUSSION_HEADER` (line 475); `_is_reddit_domain` (line 31) also returns true for a url with no scheme and no host.

4. Tests: the summarizer test asserts the prompt carries the new paragraph; readability tests: link line + discussion only is `link_without_article`, link line + self-text + discussion and link line + article + discussion stay readable, a self-post of one markdown link is `no_prose`, prose containing a markdown link stays readable, the existing readable fixtures stay readable; a Reddit fetcher test: a link post with url '/r/x/comments/1/y/' makes no extraction request and records no fetch outcome. Each shown red with its change removed.

5. At deploy, the three misfired items (the Nobel announcement, llm-mistral 0.16, datasette-atom 0.11a0) are re-analysed once on cerebro through `analyze_and_store_item`, 3 light calls, and their stored reason checked.

## Commitments

- The model is asked whether the piece the title refers to is present, not whether the text is short.
- A Reddit link post with no article and no self-text is title-only by code (`content:link_without_article`), whatever its comments.
- A markdown link counts as a link, not prose, in the has-prose rule.
- The Reddit discussion header is defined once in readability.py.
- Relative Reddit urls are reddit-internal and never fetched.
- Measured on 108 labeled cerebro items: the new wording called 48 of 48 real items substantive; the new code rules catch 5 of the 6 code-passing junk items the new wording misses.

## Sacrifices

- An X page showing only metadata, a map's interface labels and a paywalled listing (3 items in the labeled set) stay readable, as they were before the model verdict existed.
- A Reddit link post whose article was not obtained is title-only even when its comment thread is long; its summary is still written from the comments.
- Items stored before this change keep their label until re-analysed or refetched, except the three misfires re-analysed at deploy.

## Risk

If the narrower wording lets more junk through than the sample showed, it appears as junk summarized as content, the state before the model verdict existed; if the link-without-article shape changes in the Reddit fetcher without the shared constant, the rule stops matching, which the readability tests catch.

## Licensed by

daemon/src/prismis_daemon/readability.py:120
