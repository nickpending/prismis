"""Source health: when a failing source is fetched next, and what state each source is in.

A failed fetch never switches a source off. Its next fetch waits fetch_interval x
2^(n-1) after its nth consecutive failure, capped at a day, and lands on the cycle
nearest that time. A source is broken at the failure whose wait first reaches the cap;
before that its failures are evidence only and it keeps the health its last successful
fetch earned.

Every function here is pure. The orchestrator asks `is_due` per source per cycle, and
storage records the failure wait from `backoff_minutes` and the state from `classify`.
"""

from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

DORMANT_DAYS = 90
BACKOFF_CAP_MINUTES = 1440

HEALTH_STATES = ("healthy", "broken", "empty", "dormant")
# The states that raise the main-screen warning; the schema's attention trigger spells
# the same three.
FLAGGED_STATES = ("broken", "empty", "dormant")

# 2^MAX_DOUBLINGS minutes already exceeds the cap at a one-minute interval, so a larger
# failure count never needs a larger power.
_MAX_DOUBLINGS = 11


def backoff_minutes(failures: int, fetch_interval: int) -> int:
    """The wait after the `failures`th consecutive failure: interval x 2^(n-1), capped."""
    doublings = min(max(failures - 1, 0), _MAX_DOUBLINGS)
    return min(fetch_interval * 2**doublings, BACKOFF_CAP_MINUTES)


def broken_after(fetch_interval: int) -> int:
    """The failure count at which a source is broken: the first whose wait reaches the cap.

    5 at a 120-minute interval (waits of 2, 4, 8, 16 hours, then the day), 7 at 30.
    """
    failures = 1
    while backoff_minutes(failures, fetch_interval) < BACKOFF_CAP_MINUTES:
        failures += 1
    return failures


def is_due(
    next_fetch_at: str | None, cycle_start: datetime, fetch_interval: int
) -> bool:
    """Whether a source is fetched in the cycle that starts at `cycle_start`.

    A source with no wait is fetched every cycle. A waiting one is fetched by the first
    cycle that starts less than half an interval before its due time, so a retry lands
    on the cycle nearest it and the scheduler starting a cycle a second early never
    pushes it a whole cycle late.
    """
    if next_fetch_at is None:
        return True
    due = datetime.fromisoformat(next_fetch_at)
    return due < cycle_start + timedelta(minutes=fetch_interval / 2)


def _older_than_dormant(stamp: str | None, now: datetime) -> bool:
    return stamp is not None and datetime.fromisoformat(stamp) < now - timedelta(
        days=DORMANT_DAYS
    )


def classify(row: Mapping[str, Any], now: datetime, fetch_interval: int) -> str | None:
    """The health a source's stored record earns at `now`, or None before its first fetch.

    Rules in order: broken at broken_after failures; empty when the last successful
    fetch served zero entries; dormant when the newest item seen is older than 90 days,
    or nothing dated was ever seen and the source is older than 90 days; healthy once
    it has fetched successfully.

    Args:
        row: The source's columns: error_count, entries_served, newest_item_at,
            created_at and last_fetched_at.
        now: The time the classification is made at.
        fetch_interval: The daemon's fetch interval in minutes.
    """
    if (row["error_count"] or 0) >= broken_after(fetch_interval):
        return "broken"
    if row["entries_served"] == 0:
        return "empty"
    newest = row["newest_item_at"]
    if _older_than_dormant(newest, now) or (
        newest is None and _older_than_dormant(row["created_at"], now)
    ):
        return "dormant"
    if row["last_fetched_at"] is not None:
        return "healthy"
    return None
