---
type: exploration
date: 2026-11-06
title: "Source health: dead feeds, backoff and agent-led repair"
description: "How prismis should notice, surface and handle sources that break, go empty or go dormant, replacing the five-strike deactivation, and how an operator's own agent can diagnose and repair them."
purpose: "The record of how source health was decided, read when its issues are shaped."
producer: skill
---

# Source health: dead feeds, backoff and agent-led repair

## Problem

Some of the operator's sources produce nothing, for different reasons, and prismis neither says so nor handles them well. On 2026-11-01 the operator's daemon host held 49 sources: NCC Group's feed answers 404 and was switched off by prismis; Hnsecurity and SpecterOps answer 200 with a valid feed of zero entries; Nahamsec serves eight entries, all from February 2025 or earlier, so nothing was ever stored; and Sean Heelan, Steipete, Evilsocket and Spaceraccoon have published nothing in three months or more. The rest were healthy.

The only handling is a rule in `Storage.update_source_fetch_status` that sets `active = 0` after five consecutive failed fetches. The question was how prismis should notice each of these, tell the operator, and act, as one approach rather than one rule per case.

## Journey

- **The five-strike rule.** It has been in the code since the first commit (e2ffa94, 2025-09-09), stated in `docs/IDEA.md` as "deactivate after 5 consecutive failures" with no reason given, and is not configurable. It could never fire for its first year, because a failed fetch was recorded as a success (#75, fixed 2026-09-23 in 3610a21); NCC Group is its first real casualty. When it fires it writes the same `active = 0` that the operator's own pause writes, and records nothing about why. At a 120-minute fetch interval, five strikes is ten hours, so one bad day at a site switches it off permanently and silently. rudy asked who decided it; nobody did, so it is re-decided here.
- **One problem, not three.** Broken, empty and dormant were first offered as separate threads (what to do on failure, how to tell the operator, how to classify). rudy: they are all related, as how to be made aware and how to handle. They are taken together.
- **What awareness exists.** Pull only: the TUI's sources screen (`S`) and `prismis-cli source list`. Two TUI renderers disagree (one turns a source orange above three errors, the other red, and only one flags "not fetched in 24 hours"); neither can see an empty or dormant feed, which fetch successfully and show green; and a source prismis switched off shows the same red mark as one the operator paused.
- **Where the signal goes.** rudy wants the main screen to say that something is up, and the sources screen to hold the detail. The drift warning is the precedent: the daemon computes it each cycle and stores it, the API serves it, and the TUI shows one `⚠ Drift: ...` line between the title and the list, in local and remote mode alike. Source health takes the same path, so the daemon classifies each source once and every screen agrees.
- **Handling.** Retrying forever at full rate was rejected for backoff (rudy: "it should backoff right?"). Automatic switch-off was rejected for a warning that stays until the operator acts: fix (change the URL), remove, or keep ("I know, leave it", right for a rare poster). Keep is remembered against the state it answered, so a kept dormant source that later breaks is flagged again. With nothing switched off automatically, the five-strike rule has no job and goes.
- **Telling empty from quiet.** A feed with zero entries and a feed with only old entries both yield zero new items, so the daemon has to record how many entries a feed served on each fetch; it does not today.
- **Agent-led repair.** rudy, thinking out loud: something should notice sources with issues, research what went wrong and recommend a fix (remove, a prismis bug, change something), so prismis heals itself, which changes the approach if the health output is written for a model rather than a person. It does: the record must carry enough evidence to diagnose from (status, entries served, redirect, last new post, error text and its history), not a one-word state. The open-in-agent launcher from #47 already starts an agent with a prompt and `prismis-cli` commands, so an on-demand diagnosis needs little new.
- **Whose agent.** The rub, in rudy's words: everyone has a different approach for how their agent diagnoses. So prismis does not own the diagnosing. It owns the two ends, the evidence it emits and the actions it accepts, and any agent fits in the middle: a herdr pane, a cron job piping into `claude -p`, an ember daemon job. Considered and not chosen: a built-in diagnosing agent (ties prismis to one runtime) and an MCP server (the CLI plus JSON on stdin is more universal). Asked for the most general, open and useful form, the recommendation was a versioned record offered both ways, pull and push, with audited actions; rudy: "I like it".
- **The numbers.** Dormant means no new post in 90 days, which flags five of today's sources (rudy: "sounds right"). Backoff retries a broken source at the normal two-hour cycle, doubles the wait after each further failure, and caps at once a day, so a dead site costs one fetch a day and a recovered one is picked up within a day.

## Decisions

- The five-strike automatic deactivation is removed. Nothing switches a source off or deletes it except the operator or an action the operator's agent takes through the CLI or API.
- A failing source is retried with backoff: the normal fetch interval first, the wait doubled after each further consecutive failure, capped at once a day; a success resets it.
- The daemon classifies every source each cycle as healthy, broken, empty or dormant, and stores the classification with its evidence. Dormant means no new item in 90 days. To tell empty from quiet, the daemon records how many entries each fetch served.
- The classification is the daemon's alone; the TUI, CLI, web view and API show it and never recompute it, so the two disagreeing TUI status rules go.
- While any source needs attention, the TUI's main screen shows one line saying so, the way the drift warning does, and the sources screen shows each flagged source with its state and evidence.
- A warning stays until the operator fixes, removes or keeps the source. Keep is remembered against the state it answered; a later, different state flags the source again.
- The health record is a versioned, documented JSON shape, written so a model can diagnose from it: state, evidence, history, and what has been tried.
- It is offered both ways: pulled through `prismis-cli source health --json` and an API route, and pushed to one configured command, run by the daemon with the changed record on stdin when a source's state changes, time-bounded and logged.
- Repairs are ordinary CLI and API actions (change the URL, remove, keep), each recording who acted and why. prismis ships no diagnosing agent of its own.

## Candidate features

- **A source that breaks, empties or goes quiet is invisible to the operator, and a failing one is silently switched off.** A feed answering 404, a feed with zero entries and a feed whose author stopped posting all show as healthy or as merely paused; nothing on the main screen says anything is wrong; and after five failed fetches prismis deactivates a source permanently with no record of why, indistinguishable from the operator's own pause, so a site that is down for a day is lost until someone notices.
- **The operator's own agent cannot diagnose or repair an unhealthy source.** Nothing exposes a source's health with enough evidence for a model to work out what went wrong, nothing tells an agent when a source turns unhealthy, and the repairs an agent would make are not recorded as who did them and why, so every fix still needs the operator to investigate and act by hand.

## Deferred

- Health signals in the web view and the menu-bar app: the main-screen line is the TUI's first; the record and the API make the others possible later.
- A built-in diagnosing agent or an MCP server: the operator's agent of choice does the diagnosing.
- Refetching or backfilling items a source missed while broken.
- Per-source overrides of the backoff cap or the dormant window.
