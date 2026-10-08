---
type: design
date: 2026-10-08
title: "Design: context-bootstrap"
description: "A new user's first fetch is scored against their own interests: one command gives them a prompt any chatbot can run as an interview, and the chatbot hands back a context.md prismis reads as-is plus the commands to add the feeds they already follow."
purpose: "The recorded design of the context-bootstrap work order, read by its plan and its builders."
producer: cli:shape
---

# Design: context-bootstrap

## Purpose

A new user's first fetch is scored against their own interests: one command gives them a prompt any chatbot can run as an interview, and the chatbot hands back a context.md prismis reads as-is plus the commands to add the feeds they already follow.

## Form

A new CLI module, cli/src/cli/context.py, registered beside the other sub-apps in cli/src/cli/__main__.py:68 as `context`, with one command, `bootstrap`, that writes BOOTSTRAP_PROMPT to stdout and exits 0. It imports nothing from prismis_daemon, so it works on a client-only install, and it makes no network or LLM call.

BOOTSTRAP_PROMPT, a module-level string in the same file, is the one copy of the prompt. It tells the chatbot to interview the user one question at a time, 6 to 10 questions, covering: the topics they want to hear about first, the ones they follow casually, the ones they never want to see, and the blogs, subreddits and YouTube channels they already read. Its output spec asks for exactly this, in one block: a context.md with '## High Priority Topics', '## Medium Priority Topics', '## Low Priority Topics' and '## Not Interested', each a bulleted list of specific topics written the way a headline would name them (not one-word categories); then one `prismis-cli source add <url>` line per feed named, as an RSS/Atom feed URL, reddit://<subreddit> or a youtube.com channel URL, the forms source add accepts (cli/src/cli/source.py:31-33). It ends by telling the user where to save the file, ~/.config/prismis/context.md.

The daemon's required sections move from a local list in _validate_context_md (daemon/src/prismis_daemon/context_auto_updater.py:285) to a module constant, REQUIRED_CONTEXT_SECTIONS, which the validator uses unchanged; a CLI test (in the dev environment, where the daemon package is importable) checks that every required section, and '## Not Interested', appears in BOOTSTRAP_PROMPT's output spec, so the two cannot drift. defaults.py's first-run next steps (line 160) and the README Quick Start replace the hand-written sample step with `prismis-cli context bootstrap`, and the README says the sample context.md the first run writes is a placeholder until the user replaces it.

## Commitments

- `prismis-cli context bootstrap` prints the prompt and exits 0 with no daemon installed, no network and no LLM call.
- The prompt asks 6 to 10 interview questions covering priority topics, casual topics, unwanted topics and sources already read.
- The prompt's output spec names exactly '## High Priority Topics', '## Medium Priority Topics', '## Low Priority Topics' and '## Not Interested', and a test fails if it omits a section the daemon requires.
- The output spec asks for one `prismis-cli source add` line per named feed in a form source add accepts.
- The first-run next steps and the README Quick Start point at `prismis-cli context bootstrap`.

## Sacrifices

- The quality of the generated context.md depends on the chatbot the user pastes into; prismis does not run or check that conversation.

## Risk

A chatbot may wrap the output in prose or code fences; the prompt asks for one plain block and the user saves only that, and a malformed file shows up when the context validator or verify reads it.

## Licensed by

cli/src/cli/__main__.py:68
