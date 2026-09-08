"""Integration tests: the chain end to end on a fetch failure, and its CLI surface.

Invariants protected:
  - A source URL that cannot be fetched stops the run before any LLM call, and the
    report names the fetch failure as the reason — absence of spend alone is not the
    evidence, because a circuit-open skip also spends nothing
  - The chain module never reads the sources table and observability honors
    XDG_DATA_HOME (SC-2's own script, run here so the gate carries it)
  - `--chain` without `--source`, and an unsupported `--type`, are refused before any
    config or database work
  - `verify` with no flags still takes the original path (SC-7)

Success criteria covered:
  SC-2, SC-4, SC-7

Real collaborators throughout: the real RSSFetcher raises on the malformed URL without
opening a socket, and the whole run goes through the real run_chain, the real
DaemonOrchestrator and a real temp-XDG SQLite database. Credential-free and
network-free.
"""

import io
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest
from rich.console import Console

from prismis_daemon.__main__ import verify
from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.verify_chain import run_chain

_BAD_URL = "not-a-real-url"
_REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Bind the observability singleton to this test's sealed data dir, and unbind
    after — it caches the directory it resolved when first constructed."""
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _log_path() -> Path:
    base = get_logger().base_dir
    return base / f"{datetime.now().strftime('%Y-%m-%d')}_events.jsonl"


def _lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line for line in path.read_text().splitlines() if line]


# --- SC-2 ------------------------------------------------------------------------------


def test_sc2_chain_module_never_reads_the_sources_table() -> None:
    """
    SC-2, run as the work order's own script writes it: the chain module must not
    mention get_active_sources, and observability must read XDG_DATA_HOME.

    BREAKS: the chain reads the operator's real source list, or writes its JSONL into
    the operator's real data tree — either one breaks the isolation promise.
    """
    chain = _REPO_ROOT / "daemon/src/prismis_daemon/verify_chain.py"
    observability = _REPO_ROOT / "daemon/src/prismis_daemon/observability.py"

    assert chain.is_file(), f"chain module not found at {chain}"
    assert "get_active_sources" not in chain.read_text()
    assert "XDG_DATA_HOME" in observability.read_text()


def test_sc5_the_dark_links_now_call_obs_log() -> None:
    """
    SC-5, run as the work order's own script writes it.
    BREAKS: a link ships dark again and only a manual grep would notice.
    """
    for name in ("embeddings.py", "notifier.py"):
        source = (_REPO_ROOT / "daemon/src/prismis_daemon" / name).read_text()
        assert source.count("obs_log(") > 0, f"{name} emits no observability event"


# --- SC-4 ------------------------------------------------------------------------------


def test_fetch_failure_stops_the_run_before_any_llm_call(tmp_path: Path) -> None:
    """
    SC-4: a URL that cannot be fetched stops the chain having spent nothing, and the
    report says the fetch failed rather than leaving a blank.

    Absence of llm.call is not the whole evidence — a circuit-open skip emits nothing
    either. So this also asserts the positive: a fetcher.error event for this run, and
    a fetch row reading "error" with the real reason in the rendered report.

    The window is controlled: only lines appended during the call are examined, and
    they are further required to share one run id, so nothing written before this test
    can satisfy or spoil the assertion.

    BREAKS: the chain burns real money on a run whose first link already failed, or
    reports a fetch failure as "nothing to summarize".
    """
    before = len(_lines(_log_path()))
    out = io.StringIO()
    buffer = Console(file=out, width=200)

    exit_code = run_chain(_BAD_URL, "rss", full=False, console=buffer)

    new_events = [json.loads(line) for line in _lines(_log_path())[before:]]
    assert new_events, "the run must have written events, or nothing is being checked"

    run_ids = {e.get("run_id") for e in new_events}
    assert len(run_ids) == 1 and None not in run_ids, (
        f"every event this run wrote must carry one stamped run id, got {run_ids}"
    )

    assert [e for e in new_events if e["event"] == "fetcher.error"], (
        f"expected a fetcher.error event, got {[e['event'] for e in new_events]}"
    )
    assert not [e for e in new_events if e["event"] == "llm.call"], (
        "the run made an LLM call after the fetch failed"
    )

    report = out.getvalue()
    assert "fetch" in report
    assert "error" in report
    assert "never-reached" in report
    assert exit_code == 1


# --- CLI argument surface --------------------------------------------------------------


def test_chain_without_source_exits_before_touching_config(monkeypatch, capsys) -> None:
    """
    INVARIANT: --chain with no --source is refused on the arguments alone.

    XDG_CONFIG_HOME points at an empty directory, so had the branch fallen through to
    Config.from_file the output would say "config" instead — which the sibling test
    below demonstrates it does.

    BREAKS: a missing argument surfaces as a confusing config error.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(__file__).parent / "nonexistent"))

    with pytest.raises(SystemExit) as exc_info:
        verify(chain=True, source=None)

    out = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "--chain requires --source" in out
    assert "config" not in out


def test_verify_with_no_flags_still_takes_the_original_path(monkeypatch, capsys) -> None:
    """
    SC-7: calling verify() with no arguments must run the original checks.

    This is the guard for the typer default trap: written as
    `chain: bool = typer.Option(False, ...)` the Python default is an OptionInfo
    object, which is truthy, so every existing direct caller would silently take the
    chain branch. Against the same empty XDG_CONFIG_HOME as the test above, the
    original path reports a config failure — proof the chain branch was not taken.

    BREAKS: every existing caller of verify() changes behavior, which is exactly what
    SC-7 forbids.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(Path(__file__).parent / "nonexistent"))

    with pytest.raises(SystemExit) as exc_info:
        verify()

    out = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "config" in out
    assert "--chain requires --source" not in out


def test_invalid_type_is_refused_at_the_cli(capsys) -> None:
    """
    INVARIANT: an unsupported --type is named in the error, not swallowed.
    BREAKS: a typo silently routes to the RSS fetcher.
    """
    with pytest.raises(SystemExit) as exc_info:
        verify(chain=True, source=_BAD_URL, source_type="gopher")

    out = capsys.readouterr().out
    assert exc_info.value.code == 1
    assert "invalid --type: gopher" in out


def test_cli_chain_dispatches_end_to_end_and_exits_non_zero() -> None:
    """
    INVARIANT: the CLI branch really reaches run_chain and propagates its exit code.
    BREAKS: the flag is wired to nothing, or a failing run exits 0 and a broken build
    passes its post-deploy check.
    """
    with pytest.raises(SystemExit) as exc_info:
        verify(chain=True, source=_BAD_URL)

    assert exc_info.value.code == 1


def test_verify_help_lists_the_chain_options() -> None:
    """
    SC-7: the new options are discoverable and the command still runs.
    BREAKS: an operator cannot find the flag the work order exists to add.
    """
    result = subprocess.run(
        ["uv", "run", "python", "-m", "prismis_daemon", "verify", "--help"],
        cwd=_REPO_ROOT / "daemon",
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    for flag in ("--chain", "--source", "--type", "--full"):
        assert flag in result.stdout
