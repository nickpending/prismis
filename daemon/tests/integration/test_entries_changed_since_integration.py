"""The TUI refreshes by asking for what changed since its last look. `since` filters on
fetched_at, so an item whose row changed without a re-fetch (a vote cast from the web
view, a priority re-scored, a backfilled timestamp) never reached an open TUI until a
restart. `changed_since` filters on updated_at, which the update_content_timestamp
trigger stamps on every row change, and the list view carries updated_at so a client
can advance its marker from the rows it receives."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

_HEADERS = {"X-API-Key": TEST_API_KEY}
NOW = datetime.now(UTC)


def _plant(conn: sqlite3.Connection, source_id: str, name: str, fetched_days_ago: int) -> None:
    stamp = (NOW - timedelta(days=fetched_days_ago)).isoformat()
    conn.execute(
        """INSERT INTO content (id, source_id, external_id, title, url, content, summary,
               priority, published_at, fetched_at, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, 'body', 'sum', 'high', ?, ?, ?, ?)""",
        (name, source_id, name, f"Title {name}", f"https://example.com/{name}", stamp, stamp, stamp, stamp),
    )
    conn.commit()


def _seed(test_db: Path) -> Storage:
    s = Storage(test_db)
    src = s.add_source("https://alpha.example/rss", "rss", "Alpha Feed")
    _plant(s.conn, src, "old-untouched", fetched_days_ago=10)
    _plant(s.conn, src, "old-voted", fetched_days_ago=10)
    _plant(s.conn, src, "fresh", fetched_days_ago=0)
    # A vote cast now on a row fetched ten days ago: the trigger stamps updated_at.
    s.update_content_status("old-voted", user_feedback="up")
    return s


def _ids(rows: list[dict]) -> set[str]:
    return {r["id"] for r in rows}


def test_changed_since_returns_rows_changed_after_the_marker_even_if_fetched_long_ago(
    test_db: Path,
) -> None:
    s = _seed(test_db)
    marker = NOW - timedelta(hours=1)

    assert _ids(s.get_content_list(limit=100, since=marker)) == {"fresh"}
    assert _ids(s.get_content_list(limit=100, changed_since=marker)) == {"fresh", "old-voted"}


def test_list_view_rows_carry_updated_at(test_db: Path) -> None:
    s = _seed(test_db)
    rows = {r["id"]: r for r in s.get_content_list(limit=100, view="list")}
    assert rows["old-voted"]["updated_at"] is not None
    assert rows["old-voted"]["updated_at"] > rows["old-voted"]["fetched_at"]
    assert "content" not in rows["old-voted"]


def test_api_changed_since_param_and_list_view_updated_at(test_db: Path) -> None:
    _seed(test_db)
    marker = (NOW - timedelta(hours=1)).isoformat()
    client = TestClient(app)

    response = client.get(
        "/api/entries", params={"changed_since": marker, "view": "list", "limit": 100}, headers=_HEADERS
    )
    assert response.status_code == 200, response.text
    items = {i["id"]: i for i in response.json()["data"]["items"]}
    assert set(items) == {"fresh", "old-voted"}
    assert items["old-voted"]["updated_at"] is not None

    bad = client.get("/api/entries", params={"changed_since": "yesterday"}, headers=_HEADERS)
    assert bad.status_code == 422, bad.text
    assert "changed_since" in bad.text
