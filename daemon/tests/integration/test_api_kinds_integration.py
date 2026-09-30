"""Integration tests for GET /api/kinds -- web-kind work order, job 1 (SC-1).

Invariants protected:
- SC-1: GET /api/kinds returns the sorted, de-duplicated kinds of non-archived
  items in the (optional) since_hours window, with no null or empty entry for
  unclassified items, an empty list (not an error) when there are none, and
  requires the API key like /api/entries.

Real Storage against a real sealed test database throughout (Principle I).
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content


def _client_for(storage: Storage) -> Generator[TestClient, None, None]:
    def override_get_storage() -> Generator[Storage, None, None]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _archive(storage: Storage, external_id: str) -> None:
    """Mark a fixture row archived the way a real archival pass would."""
    storage.conn.execute(
        "UPDATE content SET archived_at = CURRENT_TIMESTAMP WHERE external_id = ?",
        (external_id,),
    )
    storage.conn.commit()


@pytest.fixture
def kinds_storage(test_db: Path) -> Storage:
    """Several kinds, a duplicate kind, an unclassified item, an archived item of
    its own kind, and an item older than any window a test asks for.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)

    items = [
        # Two items of the same kind -- proves de-duplication, not just presence.
        ContentItem(
            source_id=source_id,
            external_id="release-1",
            title="Prismis 2.0 released",
            url="https://example.com/release-1",
            content="Release notes",
            fetched_at=now,
            analysis={"kind": "release", "kind_confidence": 0.92},
        ),
        ContentItem(
            source_id=source_id,
            external_id="release-2",
            title="Prismis 2.1 released",
            url="https://example.com/release-2",
            content="Release notes",
            fetched_at=now,
            analysis={"kind": "release", "kind_confidence": 0.9},
        ),
        ContentItem(
            source_id=source_id,
            external_id="tutorial-1",
            title="How to set up prismis",
            url="https://example.com/tutorial-1",
            content="A tutorial",
            fetched_at=now,
            analysis={"kind": "tutorial", "kind_confidence": 0.75},
        ),
        # Unclassified: kind key present but null.
        ContentItem(
            source_id=source_id,
            external_id="unclassified-1",
            title="Ambiguous item",
            url="https://example.com/unclassified-1",
            content="Below threshold",
            fetched_at=now,
            analysis={"kind": None, "kind_confidence": 0.4},
        ),
        # Archived: would be "incident" if it counted, but it must not.
        ContentItem(
            source_id=source_id,
            external_id="archived-incident-1",
            title="An old incident",
            url="https://example.com/archived-incident-1",
            content="Archived",
            fetched_at=now,
            analysis={"kind": "incident", "kind_confidence": 0.8},
        ),
        # Old: outside a since_hours=24 window, its own kind ("research").
        ContentItem(
            source_id=source_id,
            external_id="old-research-1",
            title="An old research item",
            url="https://example.com/old-research-1",
            content="Old",
            fetched_at=now - timedelta(days=10),
            analysis={"kind": "research", "kind_confidence": 0.85},
        ),
    ]
    for item in items:
        add_new_content(storage, item)

    _archive(storage, "archived-incident-1")

    return storage


@pytest.fixture
def kinds_client(kinds_storage: Storage) -> Generator[TestClient, None, None]:
    yield from _client_for(kinds_storage)


def test_returns_sorted_deduplicated_kinds_without_since_hours(
    kinds_client: TestClient,
) -> None:
    """
    INVARIANT (SC-1): without since_hours, every non-archived item's kind is
    represented exactly once, sorted, whatever its age.
    BREAKS: a duplicate kind appears twice, the list is unsorted, or the older
    "research" item is silently dropped even though no window was requested.
    """
    response = kinds_client.get("/api/kinds", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    kinds = response.json()["data"]["kinds"]
    assert kinds == ["release", "research", "tutorial"]


def test_since_hours_excludes_items_outside_the_window(
    kinds_client: TestClient,
) -> None:
    """
    INVARIANT (SC-1): since_hours narrows to items fetched within the window --
    the 10-day-old "research" item drops out at since_hours=24.
    BREAKS: since_hours is ignored, or it also drops kinds that ARE in the window.
    """
    response = kinds_client.get(
        "/api/kinds?since_hours=24", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    kinds = response.json()["data"]["kinds"]
    assert kinds == ["release", "tutorial"]
    assert "research" not in kinds


def test_archived_items_never_contribute_a_kind(kinds_client: TestClient) -> None:
    """
    INVARIANT (SC-1): an archived item's kind never appears, even though it would
    otherwise qualify (recent, classified).
    BREAKS: the query forgets the archived_at IS NULL scope the TUI's own
    GetDistinctKinds applies, leaking an archived-only kind into the filter.
    """
    response = kinds_client.get("/api/kinds", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    assert "incident" not in response.json()["data"]["kinds"]


def test_unclassified_items_never_produce_a_null_or_empty_entry(
    kinds_client: TestClient,
) -> None:
    """
    INVARIANT (SC-1): an item with kind=null contributes no entry at all --
    never a None/null slot and never an empty string in the list.
    BREAKS: json_extract's NULL for a missing/null key becomes a literal null or
    "" entry in the returned list instead of being excluded.
    """
    response = kinds_client.get("/api/kinds", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    kinds = response.json()["data"]["kinds"]
    assert None not in kinds
    assert "" not in kinds


def test_empty_list_not_an_error_when_nothing_matches(test_db: Path) -> None:
    """
    INVARIANT (SC-1): a database with no qualifying content returns 200 with an
    empty list, never an error status.
    BREAKS: the query raises or 500s on an empty/absent result instead of
    returning [].
    """
    empty_storage = Storage(test_db)
    for client in _client_for(empty_storage):
        response = client.get("/api/kinds", headers={"X-API-Key": TEST_API_KEY})
        assert response.status_code == 200, response.text
        assert response.json()["data"]["kinds"] == []


def test_requires_api_key_like_entries(kinds_client: TestClient) -> None:
    """
    INVARIANT (SC-1): GET /api/kinds requires X-API-Key, same as /api/entries.
    BREAKS: the endpoint is wired without the verify_api_key dependency, so it
    answers content-classification data to an unauthenticated caller.
    """
    no_key = kinds_client.get("/api/kinds")
    assert no_key.status_code == 403, no_key.text

    wrong_key = kinds_client.get("/api/kinds", headers={"X-API-Key": "wrong-key"})
    assert wrong_key.status_code == 403, wrong_key.text
