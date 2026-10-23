"""Replay of cerebro's real history through the drift check (C1).

The fixture holds cerebro's hourly item and null-priority counts from the database
backed up before the 2026-10-15 re-score, which rewrote the scores that regression
poisoned. The regression (#96) began at 2026-09-30T17:00Z and took the unprioritized
share from about 55% to about 81%. Content rows are seeded one per counted item at its
hour, a real `Storage` answers the window queries, and the real `run_drift_check`
stores each check before the next runs.

Success criteria covered: C1.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from prismis_daemon.drift import DriftCheck, decide, run_drift_check
from prismis_daemon.storage import Storage

FIXTURE = (
    Path(__file__).parent.parent / "fixtures" / "drift" / "cerebro_hourly_counts.json"
)

REGRESSION_START = datetime(2026, 9, 30, 17, tzinfo=UTC)
FLAG_BY = datetime(2026, 10, 1, 17, tzinfo=UTC)
FIRST_CHECK = datetime(2026, 1, 20, tzinfo=UTC)
LAST_CHECK = datetime(2026, 10, 15, 12, tzinfo=UTC)


def _seed(storage: Storage) -> None:
    """One content row per counted item at its hour, null priority for the null ones."""
    hours = json.loads(FIXTURE.read_text())["hours"]
    rows = []
    for hour, items, nulls in hours:
        for i in range(items):
            rows.append(
                (
                    f"{hour}-{i}",
                    hour,
                    None if i < nulls else "medium",
                )
            )
    storage.conn.executemany(
        "INSERT INTO content (id, external_id, title, url, priority, created_at)"
        " VALUES (?, ?, 'item', 'https://example.com/item', ?, ?)",
        [(ext, ext, priority, hour) for ext, hour, priority in rows],
    )
    storage.conn.commit()


def _two_hourly() -> list[datetime]:
    marks = []
    moment = FIRST_CHECK
    while moment <= LAST_CHECK:
        marks.append(moment)
        moment += timedelta(hours=2)
    return marks


@pytest.fixture
def seeded(test_db: Path) -> Storage:
    storage = Storage(test_db)
    _seed(storage)
    return storage


def test_seeded_database_holds_the_fixture_counts(seeded: Storage) -> None:
    """
    INVARIANT: the replay seeds exactly the fixture's counts, so every C1 assertion
               below is about cerebro's data and not a partial copy of it
    BREAKS: a seeding bug shrinks the data and the replay passes on something else
    """
    hours = json.loads(FIXTURE.read_text())["hours"]
    total, nulls = seeded.conn.execute(
        "SELECT COUNT(*), SUM(priority IS NULL) FROM content"
    ).fetchone()

    assert total == sum(h[1] for h in hours)
    assert nulls == sum(h[2] for h in hours)


def test_replay_flags_the_regression_and_holds_the_flag(seeded: Storage) -> None:
    """
    INVARIANT: over cerebro's history at every 2-hour mark, no check before
               2026-09-30T17:00Z flags, the first flag lands by 2026-10-01T17:00Z,
               and every check after it stays flagged to 2026-10-15T12:00Z
    BREAKS: the warning fires on ordinary weeks (ignored like no check), misses
            the regression for days, or clears while the regression is still there
    """
    checks = [run_drift_check(seeded, 0.20, now=moment) for moment in _two_hourly()]

    flagged = [c for c in checks if c.flagged]
    assert flagged, "the regression was never flagged"
    first = datetime.fromisoformat(flagged[0].checked_at)
    early = [
        c for c in checks if datetime.fromisoformat(c.checked_at) < REGRESSION_START
    ]
    assert [c.checked_at for c in early if c.flagged] == []
    assert first <= FLAG_BY, f"first flag at {first}"
    after = [c for c in checks if datetime.fromisoformat(c.checked_at) >= first]
    unflagged_after = [
        (c.checked_at, c.reason, c.deviation) for c in after if not c.flagged
    ]
    assert unflagged_after == []
    assert len(checks) == len(_two_hourly())


def test_replay_stays_flagged_only_because_the_baseline_is_pinned(
    seeded: Storage,
) -> None:
    """
    INVARIANT: the same data checked by a check with no memory (baseline recomputed
               every time, as if nothing were flagged) does not hold the flag to
               2026-10-15
    BREAKS: the stays-flagged assertion above passes without the pinned baseline, so
            it would not notice a check that absorbs a persisting regression
    """
    memoryless: list[DriftCheck] = []
    for moment in _two_hourly():
        memoryless.append(
            decide(
                checked_at=moment.isoformat(),
                windows=seeded.drift_window_counts(moment, 29),
                threshold=0.20,
                previous=None,
            )
        )

    flagged = [c for c in memoryless if c.flagged]
    assert flagged, (
        "even the memoryless check never flagged; the data has no regression"
    )
    first = flagged[0].checked_at
    assert [c for c in memoryless if c.checked_at >= first and not c.flagged], (
        "a recomputed baseline kept the flag; the pinned baseline is not what holds it"
    )
