"""`prismis-cli open <id>` with the herdr target (open-agent C6, agent-herdr-prompt).

Invariants protected:
  - the CLI lists agents, then focuses the article's live agent or splits a pane at
    [agent] cwd, starts the agent bare and submits the prompt with `agent prompt`;
    it stops at the first herdr call that fails, answers without the expected field, or
    outlives HERDR_TIMEOUT_SECONDS
  - a new pane that is still busy is retried up to 20 starts, a startup question gets
    a 120 s `agent wait`, and any other failure after the split closes that pane, while
    an unanswered startup question leaves the agent open in it
  - outside herdr (HERDR_ENV unset) no herdr call is made

The CLI runs as a real subprocess (in-process through CliRunner where a test shortens a
timing constant: the hanging call and the 20 busy starts) against live_daemon: the
daemon's own app over real Storage. herdr is the external boundary: a stub that answers
as herdr 0.9.5 does and records every invocation through agent_open_support.
"""

import time
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agent_open_support import (
    DEFAULT_STARTER,
    add_agent_section,
    article_prompt,
    default_agent_dir,
    invocations,
    run_open,
    seed,
    stub,
)
from cli.__main__ import app
from cli.remote import set_remote_url
from conftest import LiveDaemon

# herdr's answers, keyed on the first two arguments, as herdr 0.9.5 gives them: success
# JSON on stdout, an error as JSON on stderr with exit 1. AGENTS and each `on_*` step are
# filled per test; a step calls fail(code) to answer with that error, and earlier(sub)
# counts the calls to `sub` made before this one.
_HERDR_BODY = """
def fail(code, message="refused"):
    error = {{"error": {{"code": code, "message": message}}, "id": "cli:" + ":".join(args[:2])}}
    sys.stderr.write(json.dumps(error, separators=(",", ":")) + "\\n")
    sys.exit(1)


def earlier(sub):
    prefix = b"".join(os.fsencode(a) + b"\\0" for a in sub)
    calls = sorted(record.glob("herdr-*.argv"))[:n]
    return sum(p.read_bytes().startswith(prefix) for p in calls)


AGENTS = {agents}
if args[:2] == ["agent", "list"]:
    {on_list}
    print(json.dumps({{"id": "cli:agent:list", "result": {{"agents": AGENTS}}}}))
elif args[:2] == ["pane", "split"]:
    {on_split}
    print('{{"result":{{"pane":{{"pane_id":"w9:p9"}}}}}}')
elif args[:2] == ["agent", "start"]:
    after = args[args.index("--") + 1 :] if "--" in args else []
    if any("\\n" in a for a in after):
        fail(
            "invalid_agent_argument",
            "agent arguments cannot be encoded safely for the target shell",
        )
    {on_start}
    print(json.dumps({{"id": "cli:agent:start", "result": {{"agent": {{"name": args[2]}}}}}}))
elif args[:2] == ["agent", "prompt"]:
    {on_prompt}
    print('{{"id":"cli:agent:prompt","result":{{"status":"working"}}}}')
elif args[:2] == ["agent", "wait"]:
    {on_wait}
    print('{{"id":"cli:agent:wait","result":{{"status":"idle"}}}}')
elif args[:2] == ["pane", "close"]:
    {on_close}
    print('{{"id":"cli:pane:close","result":{{}}}}')
"""

_OTHER_AGENTS = [
    {"agent": "claude", "name": "emberuser", "pane_id": "wB:p4"},
    {"agent": "claude", "pane_id": "wB:p1"},
]


@pytest.fixture(autouse=True)
def _no_remote_flag() -> None:
    set_remote_url(None)


@pytest.fixture
def bin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


@pytest.fixture
def record_dir(tmp_path: Path) -> Path:
    path = tmp_path / "record"
    path.mkdir()
    return path


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _herdr_stub(
    bin_dir: Path,
    record_dir: Path,
    *,
    agents: list[dict[str, str]],
    on_list: str = "pass",
    on_split: str = "pass",
    on_start: str = "pass",
    on_prompt: str = "pass",
    on_wait: str = "pass",
    on_close: str = "pass",
) -> None:
    body = _HERDR_BODY.format(
        agents=repr(agents),
        on_list=on_list,
        on_split=on_split,
        on_start=on_start,
        on_prompt=on_prompt,
        on_wait=on_wait,
        on_close=on_close,
    )
    stub(bin_dir, record_dir, "herdr", body)


def _split_call() -> list[str]:
    """The `pane split` invocation, at the default agent directory."""
    split = ["pane", "split", "--current", "--direction", "right"]
    return [*split, "--cwd", str(default_agent_dir()), "--no-focus"]


def _start_call(name: str) -> list[str]:
    """The bare `agent start` invocation in the split pane."""
    return ["agent", "start", name, "--kind", "pi", "--pane", "w9:p9"]


def _prompt_call(name: str, prompt: str) -> list[str]:
    """The `agent prompt` invocation that submits the prompt and waits for it to land."""
    until = ["--until", "working", "--until", "blocked"]
    return ["agent", "prompt", name, prompt, "--wait", *until, "--timeout", "30000"]


def test_herdr_starts_the_agent_bare_and_submits_the_prompt_through_agent_prompt(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
) -> None:
    """
    INVARIANT: list, split right without focus at the agent directory, start
               prismis-<8> bare in the new pane, then submit the multi-line prompt as
               one `agent prompt` argument that waits until the agent is working or
               blocked
    BREAKS: herdr refuses the multi-line prompt as an `agent start` argument
            (invalid_agent_argument), so no agent starts and a bare shell pane is left
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(bin_dir, record_dir, agents=_OTHER_AGENTS)
    status_file = tmp_path / "status.txt"
    name = f"prismis-{item_id[:8]}"
    prompt = article_prompt(item_id, "Rates and taxes", "Short.", DEFAULT_STARTER)
    assert "\n" in prompt

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert invocations(record_dir, "herdr") == [
        ["agent", "list"],
        _split_call(),
        _start_call(name),
        _prompt_call(name, prompt),
    ], result.stderr
    assert result.returncode == 0, result.stderr
    assert status_file.read_text() == f"opened {name} in pane w9:p9\n"


def test_herdr_retries_start_while_the_new_pane_is_busy(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
) -> None:
    """
    INVARIANT: agent_pane_busy from `agent start` is retried, and the prompt follows
               the start that succeeds
    BREAKS: a freshly split pane, whose shell has not reached its prompt yet, ends the
            run at the first start
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=_OTHER_AGENTS,
        on_start='if earlier(["agent", "start"]) < 2: fail("agent_pane_busy")',
    )
    name = f"prismis-{item_id[:8]}"
    prompt = article_prompt(item_id, "Rates and taxes", "Short.", DEFAULT_STARTER)

    result = run_open(
        ["open", item_id], bin_dir=bin_dir, cwd=work_dir, env={"HERDR_ENV": "1"}
    )

    assert invocations(record_dir, "herdr")[2:] == [
        _start_call(name),
        _start_call(name),
        _start_call(name),
        _prompt_call(name, prompt),
    ], result.stderr
    assert result.returncode == 0, result.stderr


def test_herdr_closes_the_pane_still_busy_after_twenty_starts(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    INVARIANT: `agent start` is tried exactly 20 times while the pane stays busy, then
               the split pane is closed and the outcome names agent start
    BREAKS: the retry never ends, or gives up leaving a bare shell pane behind

    In-process, with the pause between attempts set to zero so the 20 attempts do not
    spend 9.5 s of sleep.
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir, record_dir, agents=_OTHER_AGENTS, on_start='fail("agent_pane_busy")'
    )
    monkeypatch.setattr("cli.agent.START_RETRY_SECONDS", 0)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HERDR_ENV", "1")
    name = f"prismis-{item_id[:8]}"

    result = CliRunner().invoke(app, ["open", item_id])

    assert invocations(record_dir, "herdr")[2:] == [
        *[_start_call(name)] * 20,
        ["pane", "close", "w9:p9"],
    ], result.output
    assert result.exit_code == 1, result.output
    assert result.stderr.startswith("herdr agent start failed: "), result.stderr
    assert "agent_pane_busy" in result.stderr


def test_herdr_waits_for_the_startup_question_to_be_answered_in_the_pane(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
) -> None:
    """
    INVARIANT: agent_not_ready from `agent start` tells the operator on stderr to answer
               the startup question in the new pane, waits up to 120 s for the agent to
               go idle, then submits the prompt
    BREAKS: a first launch in the agent directory, where the agent asks a startup
            question (folder trust), fails and closes the pane under the question
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir, record_dir, agents=_OTHER_AGENTS, on_start='fail("agent_not_ready")'
    )
    name = f"prismis-{item_id[:8]}"
    prompt = article_prompt(item_id, "Rates and taxes", "Short.", DEFAULT_STARTER)

    result = run_open(
        ["open", item_id], bin_dir=bin_dir, cwd=work_dir, env={"HERDR_ENV": "1"}
    )

    assert invocations(record_dir, "herdr")[2:] == [
        _start_call(name),
        ["agent", "wait", name, "--until", "idle", "--timeout", "120000"],
        _prompt_call(name, prompt),
    ], result.stderr
    assert result.returncode == 0, result.stderr
    assert result.stderr == (
        f"{name} in pane w9:p9 is asking a startup question: answer it in that pane"
        " (prismis waits up to 120 s)\n"
    )
    assert result.stdout == f"opened {name} in pane w9:p9\n"


def test_herdr_leaves_the_pane_open_when_the_startup_question_goes_unanswered(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
) -> None:
    """
    INVARIANT: when the startup wait times out, the run exits 1 saying the agent stays
               open without the prompt, and neither prompts nor closes the pane
    BREAKS: the agent the operator is still looking at is closed under them, or gets a
            prompt typed into its unanswered question
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=_OTHER_AGENTS,
        on_start='fail("agent_not_ready")',
        on_wait='sys.stderr.write(\'{"error":{"code":"timeout"}}\\n\'); sys.exit(1)',
    )
    status_file = tmp_path / "status.txt"
    name = f"prismis-{item_id[:8]}"

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert status_file.read_text() == (
        f"{name} did not get past its startup question within 120 s; it stays open"
        " in pane w9:p9 without the article prompt\n"
    )
    assert result.returncode == 1
    assert invocations(record_dir, "herdr")[2:] == [
        _start_call(name),
        ["agent", "wait", name, "--until", "idle", "--timeout", "120000"],
    ], result.stderr


@pytest.mark.parametrize(
    ("on_wait", "text"),
    [
        pytest.param('fail("agent_exited")', "agent_exited", id="agent-exited"),
        pytest.param(
            'sys.stderr.write("server not running\\n"); sys.exit(1)',
            "server not running",
            id="no-error-json",
        ),
    ],
)
def test_herdr_closes_the_pane_when_the_startup_wait_fails_other_than_timing_out(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
    on_wait: str,
    text: str,
) -> None:
    """
    INVARIANT: only herdr's `timeout` answer to the startup `agent wait` reads as an
               unanswered startup question; any other wait failure closes the pane and
               exits 1 with herdr's own `agent wait` error
    BREAKS: an agent that exited, or a herdr that broke, is reported as a question still
            waiting in the pane, and its bare shell pane is left behind
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=_OTHER_AGENTS,
        on_start='fail("agent_not_ready")',
        on_wait=on_wait,
    )
    status_file = tmp_path / "status.txt"
    name = f"prismis-{item_id[:8]}"

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert invocations(record_dir, "herdr")[2:] == [
        _start_call(name),
        ["agent", "wait", name, "--until", "idle", "--timeout", "120000"],
        ["pane", "close", "w9:p9"],
    ], result.stderr
    assert result.returncode == 1
    outcome = status_file.read_text()
    assert outcome.startswith("herdr agent wait failed: "), outcome
    assert text in outcome, outcome


@pytest.mark.parametrize(
    ("on_start", "on_prompt", "on_close", "failed", "codes"),
    [
        pytest.param(
            'fail("agent_exited")',
            "pass",
            "pass",
            "agent start",
            ["agent_exited"],
            id="start-fails",
        ),
        pytest.param(
            "pass",
            'fail("agent_prompt_stalled")',
            "pass",
            "agent prompt",
            ["agent_prompt_stalled"],
            id="prompt-stalls",
        ),
        pytest.param(
            'fail("agent_exited")',
            "pass",
            'fail("pane_not_found")',
            "agent start",
            ["agent_exited", "pane_not_found"],
            id="close-fails-too",
        ),
    ],
)
def test_herdr_closes_the_split_pane_when_a_later_step_fails(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
    on_start: str,
    on_prompt: str,
    on_close: str,
    failed: str,
    codes: list[str],
) -> None:
    """
    INVARIANT: a failed start or prompt submission is followed at once by `pane close`
               on the split pane, and the run exits 1 naming the failed step; a close
               that fails too is added to the same outcome line
    BREAKS: a failed launch leaves a bare shell pane behind in the operator's tab
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=_OTHER_AGENTS,
        on_start=on_start,
        on_prompt=on_prompt,
        on_close=on_close,
    )
    status_file = tmp_path / "status.txt"
    name = f"prismis-{item_id[:8]}"
    prompt = article_prompt(item_id, "Rates and taxes", "Short.", DEFAULT_STARTER)
    steps = [_start_call(name)]
    if failed == "agent prompt":
        steps.append(_prompt_call(name, prompt))

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert invocations(record_dir, "herdr")[2:] == [
        *steps,
        ["pane", "close", "w9:p9"],
    ], result.stderr
    assert result.returncode == 1
    outcome = status_file.read_text()
    assert outcome.startswith(f"herdr {failed} failed: "), outcome
    assert outcome.count("\n") == 1, outcome
    head, _, tail = outcome.partition("; closing pane w9:p9 also failed: ")
    assert codes[0] in head, outcome
    if len(codes) > 1:
        assert codes[1] in tail, outcome
    else:
        assert tail == "", outcome


def test_herdr_focuses_the_article_agent_that_is_already_open(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
) -> None:
    """
    INVARIANT: a live prismis-<8> agent is focused, and no pane is split
    BREAKS: every `A` on the same article opens another pane
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    name = f"prismis-{item_id[:8]}"
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=[*_OTHER_AGENTS, {"agent": "pi", "name": name, "pane_id": "w1:p2"}],
    )
    status_file = tmp_path / "status.txt"

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert invocations(record_dir, "herdr") == [
        ["agent", "list"],
        ["agent", "focus", name],
    ], result.stderr
    assert result.returncode == 0, result.stderr
    assert status_file.read_text() == f"{name} already open — focused it\n"


def test_herdr_outside_herdr_runs_nothing(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
) -> None:
    """
    INVARIANT: without HERDR_ENV=1 no herdr call is made and the outcome says why
    BREAKS: herdr is driven from a terminal it does not own
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(bin_dir, record_dir, agents=_OTHER_AGENTS)
    status_file = tmp_path / "status.txt"

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        drop=("HERDR_ENV",),
    )

    assert result.returncode == 1
    assert invocations(record_dir, "herdr") == []
    outcome = "herdr is not available (HERDR_ENV is not set)\n"
    assert status_file.read_text() == outcome
    assert result.stderr == outcome


def test_herdr_failed_split_stops_before_start(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    work_dir: Path,
    tmp_path: Path,
) -> None:
    """
    INVARIANT: a herdr call that exits non-zero ends the run with herdr's own error,
               and no later call is made
    BREAKS: an agent is started in a pane that was never created
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(
        bin_dir,
        record_dir,
        agents=_OTHER_AGENTS,
        on_split='fail("x", "split refused")',
    )
    status_file = tmp_path / "status.txt"

    result = run_open(
        ["open", item_id, "--status-file", str(status_file)],
        bin_dir=bin_dir,
        cwd=work_dir,
        env={"HERDR_ENV": "1"},
    )

    assert result.returncode == 1
    calls = invocations(record_dir, "herdr")
    assert [c[:2] for c in calls] == [["agent", "list"], ["pane", "split"]]
    outcome = status_file.read_text()
    assert outcome.startswith("herdr pane split failed: ")
    assert "split refused" in outcome


def test_a_herdr_call_that_hangs_is_cut_off_at_the_timeout(
    live_daemon: LiveDaemon,
    isolated_xdg_env: Path,
    bin_dir: Path,
    record_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    INVARIANT: a herdr call is bounded by HERDR_TIMEOUT_SECONDS; the run ends with the
               timeout outcome and makes no later call
    BREAKS: a wedged herdr freezes the CLI, and the TUI suspended behind it, forever
    """
    item_id = seed(live_daemon, title="Rates and taxes", summary="Short.")
    add_agent_section(isolated_xdg_env, 'target = "herdr"\nkind = "pi"\n')
    _herdr_stub(bin_dir, record_dir, agents=_OTHER_AGENTS, on_list="time.sleep(10)")
    monkeypatch.setattr("cli.agent.HERDR_TIMEOUT_SECONDS", 1)
    monkeypatch.setenv("PATH", str(bin_dir))
    monkeypatch.setenv("HERDR_ENV", "1")

    started = time.monotonic()
    result = CliRunner().invoke(app, ["open", item_id])
    elapsed = time.monotonic() - started

    assert elapsed < 5, f"open took {elapsed:.1f} s"
    assert result.exit_code == 1, result.output
    assert result.stderr == "herdr agent list did not answer within 1 s\n"
    assert [c[:2] for c in invocations(record_dir, "herdr")] == [["agent", "list"]]
