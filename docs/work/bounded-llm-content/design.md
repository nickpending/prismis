---
type: design
date: 2026-10-07
title: "Design: bounded-llm-content"
description: "An article of any size is analyzed and stored instead of being refused by the model and retried on every fetch cycle: the summarizer, evaluator and deep extractor send at most a fixed number of UTF-8 bytes of article text, and an item analyzed on a cut portion says so in its analysis."
purpose: "The recorded design of the bounded-llm-content work order, read by its plan and its builders."
producer: cli:shape
---

# Design: bounded-llm-content

## Purpose

An article of any size is analyzed and stored instead of being refused by the model and retried on every fetch cycle: the summarizer, evaluator and deep extractor send at most a fixed number of UTF-8 bytes of article text, and an item analyzed on a cut portion says so in its analysis.

## Form

One helper in daemon/src/prismis_daemon/llm_call.py, the module the LLM components already import for `call_llm_with_circuit_breaker`, bounds article text for a prompt: given the content, it returns the text to send and, when it cut, the sizes sent and total. The bound is `MAX_CONTENT_BYTES = 350_000` UTF-8 bytes, named for the 400,000-token window of openai/gpt-5.4-nano that both production services run: a byte-level BPE token always covers at least one byte, so 350,000 bytes can never exceed 350,000 tokens, leaving room for the system prompt, the user's context and the reply. The cut lands on a character boundary (encode, slice, decode ignoring a split trailing character), and the sent text ends with one line telling the model it was cut and how much of the article it holds, so the summary does not present a part as the whole.

The three prompt builders that interpolate the article call it in place of the raw content: the summarizer's analysis prompt (summarizer.py:454, replacing the 'Use full content - no truncation' comment at :426), the evaluator's prompt (evaluator.py:153) and the deep extractor's prompt (deep_extractor.py:207). Bounding inside the components covers every caller: the orchestrator's `analyze_and_store_item`, the CLI's `analyze repair` and the API's on-demand extract. The summarizer's word count and mode selection keep reading the full content, so a cut long-form article is still summarized in long-form mode.

The record: when the summarizer cut, its ContentSummary metadata carries `content_bounded` `{"sent_bytes": n, "total_bytes": m}`, and `build_llm_analysis` (analysis.py:78), the one assembler both the daemon pipeline and `analyze repair` use, copies it into the item's analysis JSON; an item that fit carries no such key. The evaluator and deep extractor cut the same content at the same bound with the same helper, so one record describes all three. Stored content stays complete.

## Commitments

- No request from the summarizer, evaluator or deep extractor carries more than MAX_CONTENT_BYTES (350,000) UTF-8 bytes of article text, for any content, including text that tokenizes below one character per token.
- An item whose content exceeds the bound is stored with its full content, a summary, and analysis `content_bounded` {sent_bytes, total_bytes}; an item under the bound carries no `content_bounded` key.
- When the text is cut, the prompt says so, so the model does not summarize a part as the whole.
- One helper decides the bound for all three prompts.
- The defect walk: content shaped like ascii.rest (over a million characters, about half non-ASCII) through `analyze_and_store_item` against the local LLM stub is refused before the change by a stub that enforces the window in bytes, and is stored with a summary and `content_bounded` after it.
- After deploy, cerebro's next cycle that lists an article over the bound stores it with `content_bounded` instead of logging a context-length failure.

## Sacrifices

- The 8 stored items over 350,000 bytes (the largest 790,301 bytes), if ever re-analyzed, are summarized from their first 350,000 bytes, about 60,000 words, with the cut recorded.
- The bound is sized for a 400,000-token window; a model with a smaller window would need MAX_CONTENT_BYTES lowered, and until then its overflow fails at the per-item boundary as it does today.

## Risk

If the bound sat above the window minus the prompt's other parts, an article near the bound plus a long context.md could still overflow; 350,000 bytes leaves 50,000 tokens for the system prompt, context and reply, against a context.md of about 3,000 bytes and replies of a few thousand tokens.

## Licensed by

daemon/src/prismis_daemon/kind_classifier.py:59
