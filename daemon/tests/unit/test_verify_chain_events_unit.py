"""Unit tests for run-scoped event selection.

Invariants protected:
  - read_run_events returns only lines carrying this run's id
  - A line written by another run (a different id) is excluded
  - A line written with no run id at all — the shape every event the long-running
    daemon emits has, since nothing sets an id in its process — is excluded
  - The exclusion survives into the rendered report: a foreign run's cost is not
    counted as this run's

Success criteria covered:
  SC-8b (the report describes THIS run and no other)

Real collaborators: a real ObservabilityLogger writing a real JSONL file, and the real
set_run_id path — the file is written the same way production writes it.
"""

import uuid
from datetime import datetime
from pathlib import Path

import pytest

from prismis_daemon.observability import ObservabilityLogger, set_run_id
from prismis_daemon.verify_chain import read_run_events, render_report

_SOURCE_ID = "source-under-test"


@pytest.fixture(autouse=True)
def _clear_run_id():
    yield
    set_run_id(None)


@pytest.fixture
def seeded_log(tmp_path: Path) -> tuple[Path, str]:
    """A real JSONL holding three kinds of line: a foreign run's, an unstamped one,
    and this run's. Returns (base_dir, this run's id)."""
    logger = ObservabilityLogger(base_dir=tmp_path)
    run_id = str(uuid.uuid4())

    set_run_id("a-different-run")
    logger.log("llm.call", action="summarize", model="foreign", cost_usd=99.0)

    set_run_id(None)
    logger.log("llm.call", action="summarize", model="daemon", cost_usd=77.0)

    set_run_id(run_id)
    logger.log("fetcher.complete", source_id=_SOURCE_ID, items_count=1, duration_ms=5)
    logger.log(
        "llm.call",
        action="summarize",
        model="ours",
        cost_usd=1.0,
        duration_ms=10,
        status="success",
    )
    set_run_id(None)

    return tmp_path, run_id


def _all_lines(base_dir: Path) -> list[str]:
    log_file = base_dir / f"{datetime.now().strftime('%Y-%m-%d')}_events.jsonl"
    return [line for line in log_file.read_text().splitlines() if line]


def test_only_this_runs_events_are_returned(seeded_log) -> None:
    """
    SC-8b: foreign events are in the file and stay out of the result.

    The file really holds all four lines — asserted here, so the exclusion is a filter
    doing its job rather than an empty file passing by accident.

    BREAKS: a chain run overlapping a daemon cycle reports the daemon's spend as its
    own, in a report that looks right and is not.
    """
    base_dir, run_id = seeded_log

    assert len(_all_lines(base_dir)) == 4

    events = read_run_events(base_dir, run_id)

    assert len(events) == 2
    assert {e["event"] for e in events} == {"fetcher.complete", "llm.call"}
    assert all(e["run_id"] == run_id for e in events)
    assert {e.get("model") for e in events} == {"ours", None}


def test_an_unknown_run_id_selects_nothing(seeded_log) -> None:
    """
    INVARIANT: selection is by exact id, so an id nothing was stamped with matches
    nothing — including the unstamped daemon-shaped line.

    BREAKS: the filter is a no-op that happens to look right on the happy path.
    """
    base_dir, _ = seeded_log

    assert read_run_events(base_dir, str(uuid.uuid4())) == []


def test_a_missing_log_file_reads_as_no_events(tmp_path: Path) -> None:
    """
    INVARIANT: a run that logged nothing at all reads as no events, not a crash.
    BREAKS: the report dies instead of rendering every link as never-reached.
    """
    assert read_run_events(tmp_path / "nothing-here", "any-run") == []


def test_a_foreign_runs_cost_never_reaches_the_report(seeded_log) -> None:
    """
    SC-8b at the level the criterion names: the exclusion holds through the composition
    the operator actually reads, not only in the reader in isolation.

    The foreign line carries $99 and the unstamped one $77; the report must show $1.

    BREAKS: cost and item counts are inflated by another process's work.
    """
    base_dir, run_id = seeded_log
    stats = {
        "items_fetched": 1,
        "items_processed": 1,
        "items_new": 1,
        "items_updated": 0,
        "errors": [],
        "new_high_priority_items": [],
    }

    events = read_run_events(base_dir, run_id)
    links = {
        link.name: link for link in render_report(stats, events, _SOURCE_ID, full=False)
    }

    assert links["summarize"].status == "ran"
    assert links["summarize"].cost_usd == pytest.approx(1.0)
    assert links["summarize"].model == "ours"
    assert links["fetch"].status == "ran"
