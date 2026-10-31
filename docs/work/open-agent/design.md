---
type: design
date: 2026-10-31
title: "Design: open-agent"
description: "From the focused article in the TUI (`A` or `:agent`) or from a shell (`prismis-cli open <id>`), the operator's configured agent starts with one prompt. The prompt names the article (id, title, URL, summary), gives the two prismis-cli commands that fetch the rest of it, and ends with a starter line. All of the work lives once, in `prismis-cli open`: reading the config, building the prompt, the claude and pi exec, and the herdr list/focus/split/start sequence. The TUI's `A` runs that command through the same ExecProcess hand-off `:context edit` uses, and shows its one-line outcome. Claude and pi hold the terminal until they exit. herdr opens the agent in a pane beside the TUI, or focuses that article's agent when it is already live."
purpose: "The recorded design of the open-agent work order, read by its plan and its builders."
producer: cli:shape
---

# Design: open-agent

## Purpose

From the focused article in the TUI (`A` or `:agent`) or from a shell (`prismis-cli open <id>`), the operator's configured agent starts with one prompt. The prompt names the article (id, title, URL, summary), gives the two prismis-cli commands that fetch the rest of it, and ends with a starter line. All of the work lives once, in `prismis-cli open`: reading the config, building the prompt, the claude and pi exec, and the herdr list/focus/split/start sequence. The TUI's `A` runs that command through the same ExecProcess hand-off `:context edit` uses, and shows its one-line outcome. Claude and pi hold the terminal until they exit. herdr opens the agent in a pane beside the TUI, or focuses that article's agent when it is already live.

## Form

1. Config, read only by the CLI (cli/src/cli/agent.py).
- The `[agent]` table has four keys: `target`, `command`, `kind` and `prompt`.
  - `target` is one of `claude`, `pi` or `herdr`.
  - `command` defaults to the target's own name and is split on whitespace, so `ember claude` runs `ember` with `claude` as its first argument.
  - `kind` defaults to `claude` and is read only for herdr.
  - `prompt` is the starter line. Its default is `I just read this. Help me dig into it: what matters, what is questionable, and where to go next.`
- The open-and-parse inside `_load_remote_config` (remote.py:36) becomes one helper, `read_config_toml()` in remote.py.
  - It returns the parsed dict, or `{}` when there is no config.toml.
  - It keeps the existing RuntimeError on a file that cannot be read or parsed.
  - `_load_remote_config` and the new `load_agent_config()` both call it.
- `load_agent_config()` raises RuntimeError when `[agent]` is not a table, and applies the defaults.
- The TUI's Go Config ignores the section: toml.Unmarshal skips keys its struct does not name (config.go:60). The daemon ignores it too: Config.from_file reads only the sections it names (config.py:320). Neither unit changes; one test in each pins that a config with [agent] still loads.

2. Prompt (`build_prompt` in cli/src/cli/agent.py, the only copy).
- Runs of whitespace inside the title and the summary collapse to single spaces.
- When the CLI was given `--remote <url>`, both fetch commands carry it as `prismis-cli --remote <url> get ...`, so the agent reaches the same daemon. Without the flag they read `prismis-cli get ...`, and the agent's prismis-cli resolves the daemon from config.toml as the launching CLI did. remote.py gains `explicit_remote_url()`, which returns the flag's value.
- The text is exactly:

```
I'm reading an article in prismis (item <id>).

Title: <title>
URL: <url>
Summary: <summary, or (none)>

For more, run:
  prismis-cli get <id> --json   # the full analysis: reading summary, quotes, deep synthesis
  prismis-cli get <id> --raw    # the full article text or transcript

<starter line>
```

- The text never begins with `-` or `@`, so pi never reads it as an option or a file (pi-help.txt:4).

3. `prismis-cli open <id> [--status-file PATH]` (cli/src/cli/agent.py, registered in __main__.py beside `get`, at __main__.py:81).
- Every run ends in exactly one outcome line. It is printed (stdout on success, stderr on failure) and, when `--status-file` is given, also written to that file. The option is hidden from `--help`; it is the TUI's channel.
- Order of work:
  - Load [agent] first. With no target, or an unknown one, the outcome is `No agent configured: set [agent] target = "claude", "pi" or "herdr" in config.toml`. For an unknown target the line names the bad value first. Exit 1, nothing started.
  - Fetch the entry with `APIClient().get_entry(id)`, which honours `--remote` and [remote]. An error becomes `Error: <message>`, exit 1.
- claude or pi: `subprocess.run([*command.split(), prompt])` with inherited stdin, stdout and stderr, so the engine owns the terminal. The CLI exits with the engine's code, and the outcome is `<target> session ended`. A missing executable gives `Error: <first word> not found`, exit 1.
- herdr:
  - If `HERDR_ENV` is not `1`: `herdr is not available (HERDR_ENV is not set)`, exit 1.
  - Otherwise each herdr call runs through one helper, `run_herdr(args)`. It calls `subprocess.run(["herdr", *args], capture_output=True, text=True, timeout=HERDR_TIMEOUT_SECONDS)`, with the constant at 45, which covers agent start's own 30 s readiness wait (herdr-agent-start-help.txt:22).
    - A timeout becomes `herdr <sub> did not answer within 45 s`.
    - A non-zero exit becomes `herdr <sub> failed: <herdr's stdout or stderr, stripped>`.
    - JSON without the expected field becomes `herdr <sub> answered without <field>`.
    - Each of these is the outcome, exit 1, and no later call runs.
  - The sequence:
    - `agent list` is read at `.result.agents[].name`. If `prismis-<first 8 chars of the id, lowercased>` is live, the CLI runs `agent focus <name>`, and the outcome is `<name> already open — focused it`.
    - Otherwise `pane split --current --direction right --cwd <cwd> --no-focus` is read at `.result.pane.pane_id`.
    - Then `agent start <name> --kind <kind> --pane <pane id> -- <prompt>`, and the outcome is `opened <name> in pane <pane id>`.

4. TUI (tui/internal/ui/operations/agent.go, beside EditContextFile at context.go:185).
- `OpenInAgent(itemID, remoteURL string) tea.Cmd` resolves `prismis-cli` with exec.LookPath. When it is missing, it returns `AgentDoneMsg{Line: "prismis-cli is not installed; `A` needs it"}` and starts nothing.
- Otherwise it creates a temp status file and builds `exec.Command(path, [--remote remoteURL,] "open", itemID, "--status-file", file)`. `--remote` is present only when the Model is in remote mode.
- It returns `tea.ExecProcess(cmd, cb)`, exactly as context.go:186 does. The callback reads the file's first line, removes the file, and returns `AgentDoneMsg{Line, Err}`.
- ExecProcess runs one child at a time with the TUI suspended, so no second launch can start while one runs, and the TUI keeps no launch state.
- Model (tui/internal/ui/model.go):
  - `case "A":` in the key switch (beside `case "S":` at model.go:828) and `case commands.AgentMsg:` (beside `commands.OpenMsg` at model.go:461) both return `operations.OpenInAgent(m.items[m.cursor].ID, m.remoteURL)` when an item is focused, in the list and reader views alike.
  - `case operations.AgentDoneMsg:` sets the status line to Line. When Line is empty and Err is set, it uses `prismis-cli open failed: <err>`; when both are empty, `agent closed`.
- The registry gains `r.Register("agent", cmdAgent)` beside `open` (registry.go:45). The help modal's ARTICLE COMMANDS section (help_modal.go:169) gains `A / :agent  Open in agent`.

5. Docs. docs/configuration.md gains an `## Opening an article in an agent` section with a commented `[agent]` block, in the form of the [remote] block at configuration.md:133. It covers:
- each key and its default;
- `A`, `:agent` and `prismis-cli open <id>`;
- that `A` needs prismis-cli on PATH;
- that herdr picks its own executable from `kind`.

Acts on the gap: `prismis-cli open <id>` is the one place an article becomes an agent session. It reads [agent] through the CLI's single config.toml read, builds the prompt, and execs claude or pi or drives herdr with every call time-bounded. The TUI's `A` and `:agent` hand the focused item to that command through the existing ExecProcess hand-off and show its outcome. Both shells and the TUI now have the action the gap names.

## Commitments

- TUI hands off to the CLI. The real Model runs in a real tea.Program whose input is an os.Pipe (execprocess-spike.txt), over an item served by the existing detail-server fixture.
- A stub `prismis-cli` first on PATH writes its argv NUL-separated to a record file, writes `opened prismis-abcd1234 in pane w9:p9` to the path after `--status-file`, and exits 0.
- Writing `A` to the pipe runs the stub once with argv exactly [`--remote`, <fixture URL>, `open`, <item id>, `--status-file`, <a path>]. The status line then reads `opened prismis-abcd1234 in pane w9:p9`, and the status file is gone.
- Typing `:agent` and Enter gives the same argv.
- A Model not in remote mode gives [`open`, <item id>, `--status-file`, <a path>].
- Mutation `tui-noexec`: with OpenInAgent returning a no-op Cmd, this test goes red.
- TUI shows the CLI's failure. When the stub writes `No agent configured: set [agent] target = "claude", "pi" or "herdr" in config.toml` to the status file and exits 1, the status line shows exactly that line. Writing `j` afterwards still moves the cursor. With no prismis-cli on PATH, `A` runs nothing and the status line reads ``prismis-cli is not installed; `A` needs it``. Mutation `tui-nostatusfile`: with the callback ignoring the status file, this test goes red.
- CLI, claude. `prismis-cli open <id>` runs as a subprocess against the existing live_daemon fixture (real daemon API, real Storage), with `[agent] target = "claude"`. A stub `claude` first on PATH writes its argv NUL-separated.
- The stub runs once with exactly one argument, equal byte for byte to the hand-written prompt for the seeded item with the default starter line.
- The CLI exits 0, and the status file holds `claude session ended`.
- The seeded title contains `"$(touch pwned)"`: the argument carries it literally, and no `pwned` file exists afterwards.
- CLI, command override and remote. With `target = "pi"`, `command = "fakeember pi"` and `prompt = "Summarise it."`:
- The stub `fakeember` gets argv [`pi`, <prompt ending in `Summarise it.`>].
- It records `PRISMIS_AGENT_PROBE`, set in the CLI's environment, so the engine inherits the environment.
- Run as `prismis-cli --remote <live_daemon url> open <id>`, both fetch lines in the prompt read `prismis-cli --remote <url> get <id> ...`. Without the flag they read `prismis-cli get <id> ...`.
- CLI, herdr new pane. With `target = "herdr"`, `kind = "pi"` and HERDR_ENV=1, a stub `herdr` records every invocation's argv. It answers `agent list` with JSON naming other agents only, `pane split` with `{"result":{"pane":{"pane_id":"w9:p9"}}}`, and `agent start` with exit 0.
- The CLI makes exactly three invocations, in order: [`agent`,`list`]; [`pane`,`split`,`--current`,`--direction`,`right`,`--cwd`,<the CLI's cwd>,`--no-focus`]; [`agent`,`start`,`prismis-<8>`,`--kind`,`pi`,`--pane`,`w9:p9`,`--`,<prompt>].
- It exits 0, and the outcome reads `opened prismis-<8> in pane w9:p9`.
- CLI, herdr already open. When the stub's `agent list` names `prismis-<8>`, the CLI makes exactly [`agent`,`list`] then [`agent`,`focus`,`prismis-<8>`], with no split. It exits 0, and the outcome says the agent was already open.
- CLI, herdr failures. Each case records the invocations and checks the outcome.
- With HERDR_ENV unset: no herdr invocation, the outcome `herdr is not available (HERDR_ENV is not set)`, exit 1.
- When the stub fails `pane split` with exit 1 and an error JSON on stdout: the outcome carries herdr's error text, no `agent start` is recorded, exit 1.
- A herdr call that outlives the timeout: in-process through typer's CliRunner, HERDR_TIMEOUT_SECONDS is set to 1, and a stub `agent list` sleeps 10 s. `open` returns in under 5 s by the test's own clock, with the outcome `herdr agent list did not answer within 1 s`, exit 1, and no split recorded.
- Mutation `cli-notimeout`: with the timeout argument removed from run_herdr, this test goes red.
- CLI, nothing configured. Each of these exits 1 and invokes none of the stub engines or herdr:
- no [agent] section: prints the one line naming `[agent] target`;
- an unknown target `codex`: the line names `codex`;
- an unparseable config.toml: the read error;
- an id the daemon lacks: `Error: ...`.
- Config tolerance and the shared read.
- Daemon: `Config.from_file` over a config.toml with an [agent] table of all four keys returns a Config equal to the one from the same file without the table, and logs no warning.
- TUI: `config.LoadConfig` over the same file returns no error and the same [remote] values.
- CLI: the existing remote-config tests pass through `read_config_toml()`, which is the only `open(` of config.toml in remote.py and agent.py.
- Docs. docs/configuration.md documents [agent] with all four keys and their defaults, and the help modal lists `A / :agent`. The docs gate check passes.

## Sacrifices

- `[agent] command` does not apply to the herdr target: `herdr agent start --kind` names the canonical executable itself, so herdr types `claude` or `pi` into the pane's shell. The operator's `ember claude` wrapper applies there only through their shell's own `claude` function. (not a case: herdr's own CLI has no flag to override the executable a kind runs (herdr-agent-start-help.txt:14); this is a limit of the target, not a usage case.)
- `command` is split on whitespace only: there is no shell quoting, so an executable path containing a space cannot be named. (0 of 4 the engine and wrapper executables on the operator's Mac)
- A herdr launch whose title, URL or summary line exceeds 1024 bytes is refused by herdr's canonical-mode check, and its error is shown. Nothing wraps lines to avoid it. (0 of 239 items on the operator's daemon whose title, URL or summary line exceeds 1024 bytes (max 269, 149, 528))
- An article whose herdr agent was renamed or replaced is not recognised as already open, so `A` opens a second pane for it. (not a case: Occurs only when the operator renames or replaces an agent prismis started; no usage of the feature exists yet to count.)
- A herdr pane whose agent has exited stays open as a shell, and the next `A` on that item splits a new pane instead of reusing it, so panes accumulate until the operator closes them. (not a case: Occurs once per re-open of an item whose agent has exited; no usage of the feature exists yet to count.)
- With the herdr target, the TUI is suspended (its screen gives way to the terminal) for the few seconds the CLI's list, split and start take, because the hand-off runs through ExecProcess like every other launch. (not a case: Every herdr launch takes it; it is the chosen shape of one hand-off path for all targets, not a case that occurs sometimes.)
- A TUI running in local mode reads SQLite directly, but `A` needs the daemon's HTTP API reachable, because prismis-cli fetches the item through it as every prismis-cli command does. (0 of 1 the operator's TUI configs that run in local mode)

## Risk

If the CLI builds the engine's argv through a shell string instead of an argv list, a title or summary containing quotes or `$(...)` would be executed. The claude commitment's byte-for-byte argv check and the planted `$(touch pwned)` catch that. If the TUI starts prismis-cli detached instead of through ExecProcess, claude would fight the TUI for the terminal; the tui-noexec mutation and the status line arriving only through the callback catch that. If herdr changes its JSON shape (`.result.pane.pane_id`, `.result.agents[].name`), the CLI reports the missing field rather than starting an agent in an unknown pane. The stubs follow the shape the herdr skill documents and the live `agent list` showed (herdr-agent-list.json), so a herdr upgrade can break the launch without turning a test red; the operator's post-merge walk is the real-herdr check.

## Licensed by

tui/internal/ui/operations/context.go:186
