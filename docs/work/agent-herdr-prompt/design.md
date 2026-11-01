---
type: design
date: 2026-11-01
title: "Design: agent-herdr-prompt"
description: "With the herdr target, the article's prompt reaches the agent in the new pane as its first message, whatever lines and characters it holds. A first-run startup question is handed to the operator in that pane with a bounded wait. No bare pane is left behind when a later herdr step fails."
purpose: "The recorded design of the agent-herdr-prompt work order, read by its plan and its builders."
producer: cli:shape
---

# Design: agent-herdr-prompt

## Purpose

With the herdr target, the article's prompt reaches the agent in the new pane as its first message, whatever lines and characters it holds. A first-run startup question is handed to the operator in that pane with a bounded wait. No bare pane is left behind when a later herdr step fails.

## Form

1. herdr's error code (cli/src/cli/agent.py, run_herdr at agent.py:153).
- On a non-zero exit, run_herdr reads herdr's error JSON from stderr, where herdr 0.9.5 writes it with nothing on stdout (herdr-error-stream.txt:5). It raises `HerdrFailed(OpenFailed)`, which carries `code`: `.error.code`, or None when stderr holds no such field. The message is the same as today: `herdr <sub> failed: <herdr's text>`.
- run_herdr gains a keyword `timeout_seconds`, defaulting to HERDR_TIMEOUT_SECONDS (45). A TimeoutExpired keeps today's message.

2. The herdr launch (`_open_in_herdr`, agent.py:196). The list, focus, split and HERDR_ENV steps are unchanged. After the split returns `<pane>`:
- Start: `herdr agent start <name> --kind <kind> --pane <pane>`, with no arguments after the pane.
  - Retried on `agent_pane_busy`, which a freshly split pane answers until its shell reaches its prompt (herdr-prompt-until-working-probe.txt). There are at most START_ATTEMPTS = 20 attempts, 0.5 s apart.
  - This is the one retry, under one-recovery-path: the failed step is retried with its reason a fixed number of times, then stops and reports.
- Startup question: when start fails with `agent_not_ready`:
  - The CLI prints one line to stderr, which is the terminal, since the TUI is suspended: `<name> in pane <pane> is asking a startup question: answer it in that pane (prismis waits up to 120 s)`.
  - It then runs `herdr agent wait <name> --until idle --timeout 120000`, with `timeout_seconds=STARTUP_QUESTION_SECONDS + 15`, where STARTUP_QUESTION_SECONDS = 120.
  - If that wait fails or times out, the outcome is `<name> did not get past its startup question within 120 s; it stays open in pane <pane> without the article prompt`, with exit 1. The pane is not closed.
- Prompt: `herdr agent prompt <name> <prompt> --wait --until working --until blocked --timeout 30000`.
  - The prompt reaches herdr as one argument, and herdr submits it to the agent's input by bracketed paste rather than typing it into a shell. So line count and characters no longer matter.
  - `--wait --until working --until blocked` returns as soon as the agent is seen working on the prompt or stopped at a question, never after its whole turn. On real herdr it returned `working` in 0.8 s (herdr-prompt-until-working-probe.txt). Without `--wait`, the text can still be sitting unsubmitted when the call returns (herdr-start-then-prompt-probe.txt:35).
- Cleanup: every other failure after the split closes the pane. That covers a start failure other than `agent_not_ready`, start still busy after 20 attempts, and a failed prompt submission.
  - The CLI runs `herdr pane close <pane>`, then reports the failed step's outcome line.
  - If the close itself fails, the outcome adds `; closing pane <pane> also failed: <herdr's text>`.
- Success outcome: `opened <name> in pane <pane>`, as today.

3. Test double (cli/tests/unit/test_agent_open_unit.py, the _HERDR_BODY stub at line 67). It answers each call the way herdr 0.9.5 does, and writes every error answer to stderr with exit 1, as herdr does. Today the double prints its split error on stdout; that moves to stderr, and the existing failed-split test's assertion follows it:
- `agent start` with any argument after `--` that contains a newline prints `{"error":{"code":"invalid_agent_argument","message":"agent arguments cannot be encoded safely for the target shell"},"id":"cli:agent:start"}` and exits 1.
- Otherwise `agent start` follows a per-test script: pass; agent_pane_busy N times; agent_not_ready; or another error.
- `agent prompt`, `agent wait` and `pane close` each answer success or a scripted error JSON.
- Every invocation's argv is recorded, as today.

4. Docs. The herdr sentence in docs/configuration.md's agent section (line 171) gains: on the first launch in the agent directory, the agent may ask a startup question (Claude Code's folder trust) in the new pane, and `A` waits up to 120 s for it to be answered there.

Acts on the cause: _open_in_herdr no longer hands the prompt to `agent start`, the call herdr types into a shell. It starts the agent bare and submits the prompt through `agent prompt`, herdr's own channel for agent input. That removes the refused argument. Each failure after the split closes the pane the split made, which removes the left-behind shell.

## Commitments

- Reproducing the defect: the stub rejects a multi-line `agent start` argument exactly as herdr 0.9.5 does, and an item is seeded through live_daemon with `target = "herdr"`, `kind = "pi"` and HERDR_ENV=1.
- At abb0caf, `prismis-cli open <id>` exits 1 with an outcome containing `invalid_agent_argument`, and records no `pane close`, so a bare pane would be left behind.
- After the fix the invocations are exactly, in order: [`agent`,`list`]; [`pane`,`split`,`--current`,`--direction`,`right`,`--cwd`,<agent cwd>,`--no-focus`]; [`agent`,`start`,`prismis-<8>`,`--kind`,`pi`,`--pane`,`w9:p9`]; [`agent`,`prompt`,`prismis-<8>`,<the multi-line prompt>,`--wait`,`--until`,`working`,`--until`,`blocked`,`--timeout`,`30000`].
- The run exits 0 with `opened prismis-<8> in pane w9:p9`.
- Busy pane: when the stub answers `agent start` with agent_pane_busy twice and then succeeds, three start invocations are recorded and the prompt follows. When it answers agent_pane_busy on every attempt, exactly 20 start invocations are recorded, then `pane close w9:p9`, and the outcome names agent start, with exit 1.
- Startup question: when the stub answers `agent start` with agent_not_ready and `agent wait` with success, stderr carries the line naming `prismis-<8>`, pane `w9:p9` and the startup question. The invocations continue [`agent`,`wait`,`prismis-<8>`,`--until`,`idle`,`--timeout`,`120000`], then the prompt invocation, and the run exits 0.
- Unanswered startup question: when the stub answers `agent wait` with exit 1 and `{"error":{"code":"timeout"}}`, the outcome reads `prismis-<8> did not get past its startup question within 120 s; it stays open in pane w9:p9 without the article prompt`. The run exits 1, and neither `agent prompt` nor `pane close` is recorded.
- Cleanup: a `pane close w9:p9` invocation follows the failed call, and the outcome names the failed step, with exit 1, in each of these cases:
- the stub fails `agent start` with another error code;
- it fails `agent prompt` with agent_prompt_stalled.
A failed `pane split` records no `pane close`.
- Unchanged paths: the already-open focus path, the HERDR_ENV-unset path and the timeout test for a hanging `agent list` pass unchanged.
- Docs: configuration.md's agent section says the first launch may ask a startup question in the new pane and that `A` waits up to 120 s for it, and the docs gate check passes.

## Sacrifices

- When the operator does not answer the startup question within 120 s, the agent stays open without the article prompt, and a later `A` on that article only focuses it (the already-open path sends no prompt). (not a case: Only when the operator leaves the startup question unanswered for 120 s, and the question appears once per agent directory, which is one fixed directory by default since abb0caf.)
- While the startup question is pending, the TUI stays suspended for up to 120 s, because `prismis-cli open` runs inside its ExecProcess hand-off. (not a case: Once per agent directory, on the first launch there, and the default agent directory is one fixed path since abb0caf.)
- Closing the pane after a failed prompt submission also ends the agent that had started in it. (not a case: Only when herdr rejects a prompt submission. No submission failed in this session's real-herdr probes, and the orchestrator ruled that no pane is left behind on failure.)

## Risk

The stub mirrors herdr 0.9.5's answers as observed in this session's probes and the orchestrator's: invalid_agent_argument, agent_pane_busy, agent_not_ready, the prompt `--until working` status and the timeout error. A herdr upgrade that renames an error code turns the startup-question or busy branch into an ordinary failure that closes the pane. That shows up as a failed launch with herdr's text, never as an agent left in an unknown state. The real-herdr check is open-agent's C5 walk on the fixed build.

## Licensed by

cli/src/cli/agent.py:196
