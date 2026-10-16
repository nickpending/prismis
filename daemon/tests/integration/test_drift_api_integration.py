"""GET /api/drift returns the latest drift check (C5, API half).

A real Storage against a real test database; the flagged check is produced by the real
check over seeded history, not written by hand.

Success criteria covered: C5.
"""

from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.drift import run_drift_check
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

NOW = datetime(2026, 6, 1, 12, tzinfo=UTC)


@pytest.fixture
def storage(test_db: Path) -> Generator[Storage, None, None]:
    store = Storage(test_db)

    def override_get_storage() -> Generator[Storage, None, None]:
        yield store

    app.dependency_overrides[get_storage] = override_get_storage
    yield store
    app.dependency_overrides.clear()


def _seed_day(storage: Storage, end: datetime, items: int, nulls: int, tag: str) -> None:
    """`items` items in the 24 hours ending at `end`, `nulls` of them unprioritized."""
    created = (end - timedelta(hours=1)).isoformat()
    storage.conn.executemany(
        "INSERT INTO content (id, external_id, title, url, priority, created_at)"
        " VALUES (?, ?, 't', 'https://example.com', ?, ?)",
        [
            (f"{tag}-{i}", f"{tag}-{i}", None if i < nulls else "low", created)
            for i in range(items)
        ],
    )
    storage.conn.commit()


def test_no_check_yet_returns_null_data(storage: Storage) -> None:
    """
    INVARIANT: before any check has run the endpoint answers success with data null
    BREAKS: a client cannot tell "no check yet" from an error and shows a fault
    """
    response = TestClient(app).get("/api/drift", headers={"X-API-Key": TEST_API_KEY})

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["success"] is True
    assert body["data"] is None


def test_latest_flagged_check_is_returned_with_its_fields(storage: Storage) -> None:
    """
    INVARIANT: the data is the latest stored check, with the measured share, the
               baseline, the deviation, the threshold and the flag
    BREAKS: the warning a client draws from this names the wrong shares
    """
    for day in range(1, 15):
        _seed_day(storage, NOW - timedelta(days=day), 30, 15, f"h{day}")
    _seed_day(storage, NOW, 30, 24, "today")
    run_drift_check(storage, 0.20, now=NOW)

    response = TestClient(app).get("/api/drift", headers={"X-API-Key": TEST_API_KEY})

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["flagged"] is True
    assert data["checked_at"] == NOW.isoformat()
    assert data["window_items"] == 30
    assert data["window_null"] == 24
    assert data["share"] == pytest.approx(0.8)
    assert data["baseline"] == pytest.approx(0.5)
    assert data["baseline_windows"] == 14
    assert data["deviation"] == pytest.approx(0.3)
    assert data["threshold"] == 0.20
    assert data["reason"] is None


def test_the_newest_check_is_the_one_returned(storage: Storage) -> None:
    """
    INVARIANT: with several checks stored the endpoint returns the most recent
    BREAKS: the warning shows a check from days ago
    """
    for day in range(1, 15):
        _seed_day(storage, NOW - timedelta(days=day), 30, 15, f"h{day}")
    _seed_day(storage, NOW, 30, 24, "today")
    run_drift_check(storage, 0.20, now=NOW)
    run_drift_check(storage, 0.20, now=NOW + timedelta(hours=2))

    response = TestClient(app).get("/api/drift", headers={"X-API-Key": TEST_API_KEY})

    assert response.json()["data"]["checked_at"] == (NOW + timedelta(hours=2)).isoformat()


def test_requires_the_api_key(storage: Storage) -> None:
    """
    INVARIANT: the endpoint refuses a request with no key or a wrong key
    BREAKS: an unauthenticated scanner reads the user's scoring health
    """
    client = TestClient(app)

    assert client.get("/api/drift").status_code == 403
    assert client.get("/api/drift", headers={"X-API-Key": "wrong"}).status_code == 403
