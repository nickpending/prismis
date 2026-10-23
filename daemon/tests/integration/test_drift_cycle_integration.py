"""Each daemon cycle records one drift check, through the real run_once path (C4).

A fetcher returns one item and the LLM is answered by `local_pipeline_stub`; the
orchestrator, storage, drift check and observability log are all real.

Success criteria covered: C4.
"""

import json
import logging
import os
import sqlite3
from pathlib import Path

import pytest

from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

_READABLE = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)


class _NoItems:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


class _OneItem:
    def __init__(self, source_id: str) -> None:
        self.source_id = source_id

    def fetch_content(self, source: dict, **kw: object) -> list[ContentItem]:
        return [
            ContentItem(
                source_id=self.source_id,
                external_id="drift-cycle-item",
                title="Drift cycle item",
                url="https://news.example.com/drift",
                content=_READABLE,
            )
        ]


@pytest.fixture
def orchestrator(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> DaemonOrchestrator:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    config = Config.from_file()
    storage = Storage(test_db)
    source_id = storage.add_source("https://news.example.com/feed", "reddit", "News")
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NoItems(),
        reddit_fetcher=_OneItem(source_id),
        youtube_fetcher=_NoItems(),
        file_fetcher=_NoItems(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )


def _drift_events() -> list[dict]:
    events = []
    for path in get_logger().base_dir.glob("*_events.jsonl"):
        for line in path.read_text().splitlines():
            event = json.loads(line)
            if event["event"] == "drift.check":
                events.append(event)
    return events


def test_a_cycle_stores_exactly_one_drift_check_and_logs_the_same_values(
    orchestrator: DaemonOrchestrator,
) -> None:
    """
    INVARIANT: a completed cycle adds exactly one drift_checks row, carrying the
               check's measurement and decision, and one drift.check event with the
               same values
    BREAKS: no drift record exists for a cycle, or the log and the table disagree
    """
    stats = orchestrator.run_once()

    assert stats["total_new"] == 1, stats["errors"]
    rows = orchestrator.storage.conn.execute("SELECT * FROM drift_checks").fetchall()
    assert len(rows) == 1
    row = dict(rows[0])
    assert row["window_items"] == 1
    assert row["window_null"] in (0, 1)
    assert row["share"] == row["window_null"]
    assert row["threshold"] == 0.20
    assert row["flagged"] == 0
    assert row["deviation"] is None
    assert row["baseline"] is None
    assert row["baseline_windows"] == 0
    assert row["reason"] is not None and "not judged" in row["reason"]
    assert row["checked_at"].endswith("+00:00")
    (event,) = _drift_events()
    for column in (
        "checked_at",
        "window_items",
        "window_null",
        "share",
        "baseline",
        "baseline_windows",
        "deviation",
        "threshold",
        "reason",
    ):
        assert event[column] == row[column], column
    assert event["flagged"] is False

    orchestrator.run_once()
    (count,) = orchestrator.storage.conn.execute(
        "SELECT COUNT(*) FROM drift_checks"
    ).fetchone()
    assert count == 2


def test_a_flagged_check_meeting_an_empty_window_keeps_the_flag_and_the_cycle_completes(
    orchestrator: DaemonOrchestrator, caplog: pytest.LogCaptureFixture
) -> None:
    """
    INVARIANT: a standing flag met by a window with no items (an outage) is carried
               with no share, and the cycle still returns its stats with no drift
               failure logged
    BREAKS: the console line formats a missing share and fails the cycle exactly when
            the daemon has been down
    """
    orchestrator.storage.insert_drift_check(
        {
            "checked_at": "2026-01-01T00:00:00+00:00",
            "window_items": 100,
            "window_null": 80,
            "share": 0.8,
            "baseline": 0.5,
            "baseline_windows": 28,
            "deviation": 0.3,
            "threshold": 0.20,
            "flagged": True,
            "reason": None,
        }
    )
    orchestrator.reddit_fetcher = _NoItems()

    with caplog.at_level(logging.ERROR):
        stats = orchestrator.run_once()

    assert stats["errors"] == []
    latest = orchestrator.storage.latest_drift_check()
    assert latest is not None
    assert latest["id"] == 2
    assert latest["flagged"] is True
    assert latest["share"] is None
    assert latest["baseline"] == 0.5
    assert [
        r for r in caplog.records if r.exc_info and "Drift check" in r.message
    ] == []


def test_a_drift_check_that_fails_is_logged_with_its_traceback_and_the_cycle_completes(
    orchestrator: DaemonOrchestrator, caplog: pytest.LogCaptureFixture
) -> None:
    """
    INVARIANT: when the check itself fails (here, its table is gone, a real sqlite
               error), the failure is logged with its traceback and the cycle still
               returns its stats, its item stored
    BREAKS: a bookkeeping failure stops the daemon's scoring, or vanishes unseen
    """
    orchestrator.storage.conn.execute("DROP TABLE drift_checks")
    orchestrator.storage.conn.commit()

    with caplog.at_level(logging.ERROR, logger="prismis_daemon.orchestrator"):
        stats = orchestrator.run_once()

    assert stats["total_new"] == 1, stats["errors"]
    (stored,) = orchestrator.storage.conn.execute(
        "SELECT COUNT(*) FROM content"
    ).fetchone()
    assert stored == 1
    failures = [r for r in caplog.records if r.exc_info and "Drift check" in r.message]
    assert len(failures) == 1, [r.message for r in caplog.records]
    exc_info = failures[0].exc_info
    assert isinstance(exc_info, tuple)
    assert isinstance(exc_info[1], sqlite3.OperationalError)
    assert _drift_events() == []
