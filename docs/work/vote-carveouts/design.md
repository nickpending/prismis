---
type: design
date: 2026-10-15
title: "Design: vote-carveouts"
description: "Turn downvotes into what the user means by them, a precision cut inside a category they asked for, without ever lowering the category, without acting on one vote, and without changing scoring behind the user's back: the daemon proposes a named carve-out when two or more downvotes form a slice, shows what it would hit and what it would cost the category, and writes it into context.md only when the user accepts, keeping a history the user can always read."
purpose: "The recorded design of the vote-carveouts work order, read by its plan and its builders."
producer: cli:shape
---

# Design: vote-carveouts

## Purpose

Turn downvotes into what the user means by them, a precision cut inside a category they asked for, without ever lowering the category, without acting on one vote, and without changing scoring behind the user's back: the daemon proposes a named carve-out when two or more downvotes form a slice, shows what it would hit and what it would cost the category, and writes it into context.md only when the user accepts, keeping a history the user can always read.

## Form

1. Retire per-topic learning. get_learned_preferences (analysis.py) and its three callers, the evaluator's learned_preferences parameter and its LEARNED USER PREFERENCES prompt block, the preference_influenced key in new analyses, for_llm_context in get_feedback_statistics, and the ContextAutoUpdater (its scheduler job and the auto_update_* config fields) are removed. check_context_md, the context section constants and one walk of context.md into its ## sections, ### groups and bullets move out of context_auto_updater.py into a context module; verify, label resolution and the context analyzer all use that walk, and context_analyzer.py's own _parse_context_sections is replaced by it. Old auto_update_* keys in existing config files are ignored on load. [context] learn_from_votes stays, now meaning: the daemon may propose carve-outs.

2. Read context.md every cycle. run_once loads context.md at the start of each cycle instead of using the copy Config read at startup, so an accepted carve-out or a manual :context edit applies from the next cycle without a restart. The API already loads Config per request.

3. A carve-out record. A carveouts table (schema.sql plus a migration in database.py's _MIGRATIONS) holds id, status (proposed, accepted, rejected, removed), name, the exact context.md line, the category it carves from, the evidence item ids, the preview item ids, the measured impact, the radius used, created_at and decided_at. Rows are never deleted, so the table is the history.

4. Finding slices. A new carveouts module's propose_carveouts(storage, config) runs from the scheduler once a day when learn_from_votes is on. It takes downvoted items that have an embedding, resolves each item's matched_interests to its context.md line and ## section (case-insensitive, parentheticals dropped, bullets and ### headings, prefix fallback: 99.3% of <host>'s labels resolve), and links two downvoted items when they share a resolved category and the raw cosine similarity of their stored embeddings is at or above [context] carveout_radius (default 0.50); this is the vec_content distance, never search_content's relevance score, which blends in priority and source authority. Connected groups of two or more are candidates. A candidate is dropped when any upvoted item lies within the radius of a member, or when it overlaps a proposed or accepted carve-out. A candidate overlapping a rejected carve-out is proposed again only when at least two of its downvotes were not in the rejected proposal's evidence, and the new proposal names the earlier rejection and its date.

5. Naming. The light service is given the evidence items' titles and summaries and the category, and returns a short name and one Not Interested line that names the slice inside the category (for example 'AI-agent incident news: agents leaking data or acting rogue, within AI security topics'). The reply is validated: one line, bounded length, no heading, fence or newline.

6. Preview and impact. The preview is the unarchived items from the last 30 days within the radius of any evidence item by raw cosine similarity, found with the vec_content KNN that storage.search_content already runs, queried with each evidence item's stored vector. Impact is measured before anything is shown, in memory only: the preview items and up to 20 other prioritized items in the same category are each scored twice against context.md as it is and twice with the candidate line added under Not Interested. An item counts as nulled by the line only when both runs without it give a priority and both runs with it give null, which keeps the evaluator's run-to-run variance (7 of 20 control items changed level in the 2026-10-15 check) out of the number. The row records the preview hits, the over-reach (control items nulled by the line), the raw counts and the calls spent (about 80 evaluate calls, roughly $0.05). A candidate is shown only when its line nulls at least half of the preview items that scored a priority without it; otherwise the line is named once more with the preview items as further examples, and the candidate is dropped and logged if it still falls short, because a line named from one item nulled 0 of its 11 slice items in that check.

7. Accept, reject, remove. GET /api/carveouts lists every row with its status and dates; POST /api/carveouts/{id}/accept re-reads context.md, backs it up, inserts the line under '## Not Interested' in a '### Carved out from your votes' group (created when absent), validates the file with check_context_md, writes it, marks the row accepted, and re-scores the proposal's preview items against the file just written; /reject marks it rejected; /remove deletes exactly that line from context.md, marks the row removed, and re-scores the unarchived items scored since acceptance that lie within the radius of the carve-out's evidence. The re-scores reuse the evaluation path analyze priority uses, and their count and cost are recorded on the row. The TUI shows a count of pending proposals and a carve-outs view (pending first, then the history) with accept, reject and remove keys; the web view lists the same.

8. docs/FEEDBACK_LOOP_SPEC.md is rewritten to describe this model, and docs/configuration.md describes learn_from_votes and carveout_radius.

## Commitments

- A downvote never lowers, removes or dislikes a context.md category: no vote reaches scoring except through a carve-out line the user accepted.
- A carve-out is proposed only from two or more downvoted items that share a resolved category and lie within carveout_radius of each other, with no upvoted item within the radius of any of them.
- Nothing is written to context.md without the user accepting the specific proposal; rejecting or removing is one action.
- Every carve-out ever proposed stays in the carveouts table with its status and dates, and the TUI and web show that history.
- Each fetch cycle scores against context.md as it is on disk at the start of the cycle.
- With learn_from_votes off, the default, the daemon proposes nothing and votes only accumulate.
- Accepting a carve-out re-scores its preview items and removing one re-scores the items scored since acceptance within its radius, so the stored priorities match context.md as soon as the user decides.
- A rejected slice comes back only on at least two downvotes that were not in the rejected proposal's evidence, and says when it was rejected before.
- Slice membership and the preview use raw embedding similarity, and context.md is parsed by one shared walk.
- Every proposal shows the items it would hit and how many same-category items outside them its line pushes to null, counted only where two runs without the line give a priority and two runs with it give null.
- A proposal whose line nulls fewer than half of its prioritized preview items is never shown.

## Sacrifices

- Upvotes are no longer learned as likes; they bound carve-outs and keep feeding :context suggest.
- The automatic context.md rewrite and the feedback-loop spec's 'no manual context editing' vision are gone.
- A downvoted item with no embedding or whose labels resolve to no context line cannot contribute to a carve-out.
- The 0.50 radius rests on one measured downvote and will be wrong for some slices until more downvotes calibrate it; the preview is what catches it.
- Impact is measured once, at proposal time, on a sample; an accepted line can still behave differently on items scored later, which #97's drift check is the guard for.
- Manual edits to context.md are not part of the carve-out history.
- Each proposal costs about 80 evaluate calls (roughly $0.05 at <host>'s median), plus one or two naming calls, spent by the daemon on its own once learn_from_votes is on.

## Risk

gpt-5.4-nano reads prompt lines literally, so an accepted Not Interested line could null more of its category than the measured sample showed, the same failure shape as #96 through a line the user approved; the impact number shown at acceptance and #97's unprioritized-share check are what would catch it. If the accept write races a manual edit, the re-read-then-insert keeps the user's edit; a write that check_context_md rejects leaves context.md untouched and the row proposed.

## Licensed by

daemon/src/prismis_daemon/evaluator.py:120
