"""Stored timestamps compare by time, whatever string shape they were written in (#61).

Invariant protected:
  - GET /api/entries with a time bound returns exactly the items fetched after it

Driven through `since` with a fixed bound so the test does not depend on the clock:
`since_hours` only computes now-minus-N and hands the same datetime to the same storage
call, and a bound tied to the real clock would stop discriminating whenever the window
crossed midnight UTC.

fetched_at is a TEXT column holding four historical shapes on the deploy host: space or
T separator, with or without a +00:00 offset. A raw string compare against a bound of
another shape treats every row sharing the bound's calendar date as newer than it,
because 'T' sorts after ' '. Each row here is written in one of those shapes, and the
old and new rows sit on the SAME calendar date as the bound, which is the only place
the defect shows.

Real API, real Storage, real SQLite; rows are stamped with raw SQL only to reproduce the
legacy shapes the current writer no longer produces.
"""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

_SHAPES = {
    "space-naive": lambda d: d.strftime("%Y-%m-%d %H:%M:%S.%f"),
    "space-offset": lambda d: d.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"),
    "t-naive": lambda d: d.replace(tzinfo=None).isoformat(),
    "t-offset": lambda d: d.isoformat(),
}


@pytest.mark.parametrize("shape", sorted(_SHAPES))
def test_time_bound_keeps_only_the_window(shape: str, test_db) -> None:
    """
    INVARIANT: The item fetched 1h after the bound is returned, the one 1h before is not
    BREAKS: Every item fetched on the bound's calendar date passes the filter, so
            since_hours=1, 6 and 24 return the same set
    """
    # All three instants share one calendar date, the only place the defect shows.
    bound = datetime(2026, 5, 10, 12, 0, tzinfo=UTC)
    fresh, stale = bound + timedelta(hours=1), bound - timedelta(hours=1)

    storage = Storage()
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Feed")
    ids = {}
    for label, fetched in (("fresh", fresh), ("stale", stale)):
        item_id, _ = storage.create_or_update_content(
            ContentItem(
                source_id=source_id,
                external_id=label,
                title=label,
                url=f"https://example.com/{label}",
                priority="high",
            )
        )
        storage.conn.execute(
            "UPDATE content SET fetched_at = ? WHERE id = ?",
            (_SHAPES[shape](fetched), item_id),
        )
        ids[label] = item_id
    storage.conn.commit()

    response = TestClient(app).get(
        "/api/entries",
        params={"since": bound.isoformat(), "skip_dedup": "true"},
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 200, response.text
    returned = {item["id"] for item in response.json()["data"]["items"]}
    assert ids["fresh"] in returned, f"{shape}: the item inside the window is missing"
    assert ids["stale"] not in returned, f"{shape}: the item fetched before the bound passed"


def test_archival_ages_a_t_separated_row_by_its_time_not_its_date(test_db) -> None:
    """
    INVARIANT: An unread LOW item fetched 7 days and 1 hour ago is archived by a 7-day window
    BREAKS: datetime('now', '-7 days') renders with a space, so a T-separated row from
            the boundary date compares as newer and survives up to a day past its window
    NOTE: The bound comes from SQLite's clock. When the UTC hour is 0 the row falls on
          the previous date and string order is right anyway, so this discriminates in
          23 of 24 hours and never fails spuriously.
    """
    storage = Storage()
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Feed")
    item_id, _ = storage.create_or_update_content(
        ContentItem(
            source_id=source_id,
            external_id="old",
            title="old",
            url="https://example.com/old",
            priority="low",
        )
    )
    fetched = datetime.now(UTC) - timedelta(days=7, hours=1)
    storage.conn.execute(
        "UPDATE content SET fetched_at = ? WHERE id = ?", (fetched.isoformat(), item_id)
    )
    storage.conn.commit()

    archived = storage.archive_old_content(
        {
            "high_read": None,
            "medium_unread": 7,
            "medium_read": 7,
            "low_unread": 7,
            "low_read": 7,
        }
    )

    assert archived == 1, "the row past its 7-day window was not archived"


def test_prune_age_reads_a_space_separated_published_at_by_its_time(test_db) -> None:
    """
    INVARIANT: An unprioritized item published 1 hour after the prune cutoff is not counted
    BREAKS: The cutoff binds with a T separator, so a space-separated published_at from
            the cutoff's date sorts before it and a newer item is pruned as old
    NOTE: Clock-relative like the archival test; discriminates except when the cutoff
          and the row straddle midnight UTC.
    """
    storage = Storage()
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Feed")
    item_id, _ = storage.create_or_update_content(
        ContentItem(
            source_id=source_id,
            external_id="recent",
            title="recent",
            url="https://example.com/recent",
        )
    )
    published = datetime.now(UTC) - timedelta(days=7) + timedelta(hours=1)
    storage.conn.execute(
        "UPDATE content SET published_at = ? WHERE id = ?",
        (published.strftime("%Y-%m-%d %H:%M:%S"), item_id),
    )
    storage.conn.commit()

    assert storage.count_unprioritized(days=7) == 0, (
        "an item newer than the cutoff was counted as prunable"
    )
