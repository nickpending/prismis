"""Unit tests for the drift check's decision, window counts and stored row.

Success criteria covered: C2, C3 (the decision), C4 (the stored row and event, below
the orchestrator).
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from prismis_daemon.drift import decide, run_drift_check
from prismis_daemon.observability import get_logger
from prismis_daemon.storage import Storage

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


def _history(days: int, share: float = 0.5, items: int = 100) -> list[tuple[int, int]]:
    return [(items, int(items * share))] * days


def _decide(
    window: tuple[int, int],
    history: list[tuple[int, int]],
    previous: dict[str, Any] | None = None,
    threshold: float = 0.20,
):
    return decide(
        checked_at=NOW.isoformat(),
        windows=[window, *history],
        threshold=threshold,
        previous=previous,
    )


def _flagged_previous(baseline: float = 0.5) -> dict[str, Any]:
    return {"flagged": True, "baseline": baseline, "baseline_windows": 28}


def test_unflagged_latest_recomputes_the_baseline_and_flags_at_the_full_threshold() -> (
    None
):
    """
    INVARIANT: with no standing flag the baseline is the median of the history shares,
               and the check flags exactly when share - baseline >= threshold
    BREAKS: a regression 20 points up goes unseen, or ordinary swings flag
    """
    at_threshold = _decide((100, 70), _history(28))
    below = _decide((100, 69), _history(28))

    assert at_threshold.baseline == 0.5
    assert at_threshold.deviation == pytest.approx(0.20)
    assert at_threshold.flagged is True
    assert below.flagged is False
    assert below.reason is None


def test_baseline_is_the_median_of_qualifying_windows_only() -> None:
    """
    INVARIANT: windows under 20 items are left out of the median, whatever their share
    BREAKS: a quiet holiday window of 3 items at 100% drags the usual level up
    """
    history = _history(20, 0.4) + [(3, 3)] * 8

    check = _decide((100, 40), history)

    assert check.baseline == 0.4
    assert check.baseline_windows == 20


def test_flagged_latest_uses_its_baseline_not_a_recomputed_one() -> None:
    """
    INVARIANT: after a flag the baseline is carried from that check; history that now
               looks like the regression does not move it
    BREAKS: a persisting regression is absorbed into the baseline and the warning clears
    """
    absorbed_history = _history(28, 0.8)

    check = _decide((100, 80), absorbed_history, _flagged_previous(baseline=0.5))

    assert check.baseline == 0.5
    assert check.deviation == pytest.approx(0.30)
    assert check.flagged is True


@pytest.mark.parametrize(
    ("window_nulls", "flagged"),
    [(60, True), (59, False)],
)
def test_flagged_latest_clears_only_below_half_the_threshold_over_its_baseline(
    window_nulls: int, flagged: bool
) -> None:
    """
    INVARIANT: with baseline 0.5 and threshold 0.20 a flagged check stays flagged at a
               share of 0.60 (S - B = T/2) and clears at 0.59
    BREAKS: the warning flaps around the threshold, or never clears
    """
    check = _decide((100, window_nulls), _history(28), _flagged_previous(0.5))

    assert check.flagged is flagged


def test_unflagged_previous_check_pins_nothing() -> None:
    """
    INVARIANT: a previous check that did not flag leaves the next one to recompute
    BREAKS: a stale baseline from a cleared flag is used forever
    """
    previous = {"flagged": False, "baseline": 0.1, "baseline_windows": 28}

    check = _decide((100, 55), _history(28), previous)

    assert check.baseline == 0.5
    assert check.flagged is False


@pytest.mark.parametrize("items", [0, 19])
def test_thin_window_is_stored_as_not_judged_and_names_the_item_minimum(
    items: int,
) -> None:
    """
    INVARIANT: fewer than 20 items in the window: no deviation, not flagged, and the
               reason names the item minimum
    BREAKS: a quiet night reads as a clean bill of health (or as a regression)
    """
    check = _decide((items, items), _history(28))

    assert check.deviation is None
    assert check.flagged is False
    assert check.reason is not None
    assert f"window holds {items} items" in check.reason
    assert "fewer than the 20 needed" in check.reason
    assert "daily windows" not in check.reason


def test_thin_history_is_not_judged_and_names_the_history_minimum() -> None:
    """
    INVARIANT: fewer than 14 qualifying daily windows: not judged, no baseline, the
               reason names the history minimum and the count found
    BREAKS: a new install flags (or passes) against a baseline of three days
    """
    check = _decide((100, 100), _history(13))

    assert check.deviation is None
    assert check.baseline is None
    assert check.flagged is False
    assert check.reason is not None
    assert "13 qualifying daily windows" in check.reason
    assert "fewer than the 14 needed" in check.reason
    assert "items" not in check.reason


def test_fourteen_qualifying_windows_are_enough() -> None:
    """
    INVARIANT: 14 qualifying windows is the boundary at which the check judges
    BREAKS: the history minimum is off by one in either direction
    """
    check = _decide((100, 100), _history(14))

    assert check.reason is None
    assert check.flagged is True


def test_both_minimums_unmet_are_both_named() -> None:
    """
    INVARIANT: when items and history are both short the reason names both
    BREAKS: the operator fixes one shortage and is surprised by the other
    """
    check = _decide((5, 5), _history(3))

    assert check.reason is not None
    assert "5 items" in check.reason
    assert "3 qualifying daily windows" in check.reason


def test_thin_window_does_not_clear_a_standing_flag() -> None:
    """
    INVARIANT: a check that cannot judge because the window is thin keeps a standing
               flag and its pinned baseline, with the reason saying it did not judge
    BREAKS: an outage (cerebro's 2026-10-05) clears a live regression warning for
            one cycle, after which the next judged check starts from a fresh baseline
    """
    check = _decide((12, 12), _history(28, 0.8), _flagged_previous(0.5))

    assert check.flagged is True
    assert check.baseline == 0.5
    assert check.deviation is None
    assert check.reason is not None and check.reason.startswith("not judged")


def _seed_content(
    storage: Storage, created_at: datetime, items: int, nulls: int, tag: str
) -> None:
    for i in range(items):
        storage.conn.execute(
            "INSERT INTO content (id, external_id, title, url, priority, created_at)"
            " VALUES (?, ?, 't', 'https://example.com', ?, ?)",
            (
                f"{tag}-{i}",
                f"{tag}-{i}",
                None if i < nulls else "low",
                created_at.isoformat(),
            ),
        )
    storage.conn.commit()


def test_window_counts_bucket_items_by_created_at(test_db: Path) -> None:
    """
    INVARIANT: window 0 is (now - 24h, now], window k the 24 hours before window k-1;
               each reports its item count and its null-priority count
    BREAKS: a window leaks across a day boundary and shares are measured on the wrong
            items
    """
    storage = Storage(test_db)
    _seed_content(storage, NOW, 4, 1, "at-now")
    _seed_content(storage, NOW - timedelta(hours=23, minutes=59), 3, 3, "in-w0")
    _seed_content(storage, NOW - timedelta(hours=24), 5, 2, "edge-w1")
    _seed_content(storage, NOW - timedelta(hours=47), 2, 0, "in-w1")
    _seed_content(storage, NOW + timedelta(minutes=1), 9, 9, "future")
    _seed_content(storage, NOW - timedelta(days=3, hours=1), 6, 6, "w3")

    counts = storage.drift_window_counts(NOW, 5)

    assert counts == [(7, 4), (7, 2), (0, 0), (6, 6), (0, 0)]


def test_run_drift_check_stores_the_row_and_logs_the_same_values(
    test_db: Path,
) -> None:
    """
    INVARIANT: one run stores exactly one row, readable as the latest check, and writes
               a drift.check event carrying the same values
    BREAKS: the API and verify show a different decision than the log recorded
    """
    storage = Storage(test_db)
    _seed_content(storage, NOW - timedelta(hours=1), 30, 12, "today")

    check = run_drift_check(storage, 0.25, now=NOW)

    (count,) = storage.conn.execute("SELECT COUNT(*) FROM drift_checks").fetchone()
    stored = storage.latest_drift_check()
    assert count == 1
    assert stored is not None
    assert {k: v for k, v in stored.items() if k != "id"} == {
        "checked_at": NOW.isoformat(),
        "window_items": 30,
        "window_null": 12,
        "share": 0.4,
        "baseline": None,
        "baseline_windows": 0,
        "deviation": None,
        "threshold": 0.25,
        "flagged": False,
        "reason": check.reason,
    }
    events = [
        line
        for path in get_logger().base_dir.glob("*_events.jsonl")
        for line in path.read_text().splitlines()
        if '"drift.check"' in line
    ]
    assert len(events) == 1
    event = json.loads(events[0])
    assert event["window_items"] == 30
    assert event["share"] == 0.4
    assert event["threshold"] == 0.25
    assert event["reason"] == check.reason
