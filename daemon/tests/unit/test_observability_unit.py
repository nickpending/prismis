"""Unit tests for observability's XDG resolution and process-scoped run id.

Invariants protected:
  - ObservabilityLogger() with no base_dir resolves under $XDG_DATA_HOME, not $HOME,
    so a run pointed at a temp data dir writes its JSONL there and never into the
    operator's real observability tree
  - With no run id set, a logged line carries no "run_id" key at all — the shape every
    existing reader already sees, and the shape the long-running daemon keeps emitting
  - With a run id set, every logged line carries it
  - Clearing the run id restores the no-key shape

Success criteria covered:
  SC-2 (observability honors XDG_DATA_HOME), SC-8b (run-id stamping)
"""

import json
import os
from pathlib import Path

import pytest

from prismis_daemon.observability import (
    ObservabilityLogger,
    get_run_id,
    set_run_id,
)


@pytest.fixture(autouse=True)
def _clear_run_id():
    """Reset the process-scoped run id after every test in this module.

    The run id is a module-level global and pytest runs the whole suite in one
    process, so a test that set one and did not clear it would stamp every test
    that ran after it. Mirrors circuit_breaker's reset-for-testing convention.
    """
    yield
    set_run_id(None)


def _lines(base_dir: Path) -> list[dict]:
    from datetime import datetime

    log_file = base_dir / f"{datetime.now().strftime('%Y-%m-%d')}_events.jsonl"
    return [json.loads(line) for line in log_file.read_text().splitlines() if line]


def test_default_base_dir_resolves_from_xdg_data_home() -> None:
    """
    SC-2: ObservabilityLogger() with no base_dir must resolve under $XDG_DATA_HOME.

    BREAKS: a chain run pointed at a temp data dir writes its JSONL into the
    operator's real ~/.local/share tree, so the report mines the wrong file and the
    "live database is never opened" promise is false for the observability path.
    """
    xdg_data_home = Path(os.environ["XDG_DATA_HOME"])
    home = Path(os.environ["HOME"])
    assert xdg_data_home != home / ".local" / "share", (
        "the sealed test env must point XDG_DATA_HOME somewhere other than the "
        "home-derived default, or this assertion cannot distinguish the two"
    )

    logger = ObservabilityLogger()

    assert logger.base_dir == xdg_data_home / "prismis" / "observability"


def test_logged_event_round_trips_through_the_real_file(tmp_path: Path) -> None:
    """
    INVARIANT: log() writes one parseable JSON object per event, carrying its metadata.
    BREAKS: the chain's reader finds nothing to report on.
    """
    logger = ObservabilityLogger(base_dir=tmp_path)

    logger.log("test.event", action="summarize", cost_usd=0.25)

    entries = _lines(tmp_path)
    assert len(entries) == 1
    assert entries[0]["event"] == "test.event"
    assert entries[0]["action"] == "summarize"
    assert entries[0]["cost_usd"] == 0.25
    assert entries[0]["ts"].endswith("+00:00")


def test_no_run_id_set_emits_no_run_id_key(tmp_path: Path) -> None:
    """
    SC-8b: with no run id set, the entry has no "run_id" key at all — not even null.

    This is what makes the daemon's own events unfilterable-into a chain run: nothing
    in the daemon's process sets a run id, so its lines can never match a chain's id.
    The guard is reachable — the sibling test below shows the same call site emitting
    the key once a run id is set.

    BREAKS: existing JSONL readers see a schema change, and a chain run could match
    daemon events.
    """
    logger = ObservabilityLogger(base_dir=tmp_path)

    assert get_run_id() is None
    logger.log("test.event")

    entry = _lines(tmp_path)[0]
    assert "run_id" not in entry, f"unset run id must not add a key, got {entry}"


def test_set_run_id_stamps_every_event(tmp_path: Path) -> None:
    """
    SC-8b: once set, the run id lands on every event logged in this process.
    BREAKS: the chain cannot attribute llm.call events, which carry no source id,
    so a concurrent daemon cycle's spend is reported as the chain's.
    """
    logger = ObservabilityLogger(base_dir=tmp_path)

    set_run_id("run-alpha")
    assert get_run_id() == "run-alpha"
    logger.log("llm.call", action="summarize")
    logger.log("db.insert", table="content")

    entries = _lines(tmp_path)
    assert [e["run_id"] for e in entries] == ["run-alpha", "run-alpha"]


def test_clearing_run_id_restores_the_no_key_shape(tmp_path: Path) -> None:
    """
    INVARIANT: set_run_id(None) clears the stamp for subsequent events.
    BREAKS: a run id leaks past the invocation that set it and every later event in
    the process is misattributed to a finished run.
    """
    logger = ObservabilityLogger(base_dir=tmp_path)

    set_run_id("run-beta")
    logger.log("first.event")
    set_run_id(None)
    logger.log("second.event")

    entries = _lines(tmp_path)
    assert entries[0]["run_id"] == "run-beta"
    assert "run_id" not in entries[1]
    assert get_run_id() is None
