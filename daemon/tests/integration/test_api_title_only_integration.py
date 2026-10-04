"""Integration tests for the title_only convenience on the content read paths --
readable-content work order, job 2 (SC-6).

Invariants protected:
- SC-6: GET /api/entries, GET /api/entries/{id} and GET /api/search each mirror
  analysis.title_only onto a top-level title_only field, the same convenience
  kind already gets (item has no title_only column of its own). An item whose
  analysis carries no title_only key, or title_only: false, mirrors to False --
  the web card's "other items show none" half of SC-6 depends on that default,
  not just on the true case.

Real Storage against a real sealed test database throughout (Principle I).
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


@pytest.fixture
def title_only_populated_storage(test_db: Path) -> Storage:
    """Storage with a title-only item, an explicitly-readable item, and an item
    whose analysis carries no title_only key at all (pre-existing/legacy rows).
    Each gets a hand-set unit-vector embedding (same pattern as
    test_api_search_kind_integration.py) so /api/search can find them without a
    real embedding provider call.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    recent_time = datetime.now(timezone.utc)

    items = [
        ContentItem(
            external_id="title-only-1",
            source_id=source_id,
            title="Comments-stub item",
            url="https://example.com/title-only",
            content="Comments",
            published_at=recent_time,
            priority="low",
            analysis={
                "title_only": True,
                "title_only_reason": "fetch_failed:HTTP 429; content:no_prose",
                "kind": None,
            },
        ),
        ContentItem(
            external_id="readable-1",
            source_id=source_id,
            title="Real article",
            url="https://example.com/readable",
            content="A full article body.",
            published_at=recent_time,
            priority="high",
            analysis={"title_only": False, "kind": "news"},
        ),
        ContentItem(
            external_id="legacy-1",
            source_id=source_id,
            title="Analysed before title_only existed",
            url="https://example.com/legacy",
            content="An older article.",
            published_at=recent_time,
            priority="medium",
            analysis={"kind": "tutorial"},
        ),
    ]
    for dim, item in enumerate(items):
        content_id = add_new_content(storage, item)
        storage.add_embedding(content_id, _unit_vector(dim))

    return storage


@pytest.fixture
def api_client(
    title_only_populated_storage: Storage,
) -> Generator[TestClient, None, None]:
    def override_get_storage() -> Generator[Storage, None, None]:
        yield title_only_populated_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_entries_mirrors_title_only_true(api_client: TestClient) -> None:
    """
    INVARIANT: an item whose analysis.title_only is True mirrors to a top-level
    title_only: true on GET /api/entries.
    BREAKS: the web card has nothing to key its marker off, so a title-only item
            never shows the marker.
    """
    response = api_client.get("/api/entries", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    items = {i["external_id"]: i for i in response.json()["data"]["items"]}
    assert items["title-only-1"]["title_only"] is True


def test_entries_mirrors_title_only_false_when_explicit(api_client: TestClient) -> None:
    """
    INVARIANT: an item whose analysis.title_only is explicitly False mirrors to
    a top-level title_only: false.
    BREAKS: a readable item shows the "title only" marker it must not show.
    """
    response = api_client.get("/api/entries", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    items = {i["external_id"]: i for i in response.json()["data"]["items"]}
    assert items["readable-1"]["title_only"] is False


def test_entries_mirrors_title_only_false_when_absent(api_client: TestClient) -> None:
    """
    INVARIANT: an item whose analysis carries no title_only key at all (a row
    analysed before this work order) mirrors to a top-level title_only: false,
    not null and not a missing key.
    BREAKS: a legacy item with no title_only key would show a marker (if the web
            card treated a missing/null key as truthy) or crash the client
            (if it required the key to exist).
    """
    response = api_client.get("/api/entries", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    items = {i["external_id"]: i for i in response.json()["data"]["items"]}
    assert items["legacy-1"]["title_only"] is False


def test_single_entry_mirrors_title_only(api_client: TestClient) -> None:
    """
    INVARIANT: GET /api/entries/{id} carries the same top-level title_only
    convenience as the list endpoint (the same way it already mirrors kind).
    BREAKS: the web card's detail view disagrees with its own list view about
            whether an item is title-only.
    """
    list_response = api_client.get("/api/entries", headers={"X-API-Key": TEST_API_KEY})
    content_id = next(
        i["id"]
        for i in list_response.json()["data"]["items"]
        if i["external_id"] == "title-only-1"
    )

    response = api_client.get(
        f"/api/entries/{content_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["title_only"] is True


def test_search_mirrors_title_only(api_client: TestClient) -> None:
    """
    INVARIANT: GET /api/search carries the same top-level title_only convenience
    as GET /api/entries (the same way it already mirrors kind).
    BREAKS: the web card's search results disagree with the main feed about
            whether an item is title-only.
    """
    response = api_client.get(
        "/api/search",
        params={"q": "test", "min_score": 0.0},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = {i["external_id"]: i for i in response.json()["data"]["items"]}
    assert "readable-1" in items
    assert items["readable-1"]["title_only"] is False


_REASON = "fetch_failed:HTTP 429; content:no_prose"


def test_entries_and_single_entry_carry_the_title_only_reason(
    api_client: TestClient,
) -> None:
    """
    title-only-reasons SC-7: `title_only_reason` is a top-level field on the entries
    list and the single-entry read, the reason for a title-only item and null for
    a readable one or one analysed before reasons existed.
    BREAKS: a mirror that is added to one read path only leaves the CLI `get` (single
    entry) and `list` (entries) disagreeing about why an item is title-only.
    """
    headers = {"X-API-Key": TEST_API_KEY}
    listed = api_client.get("/api/entries", headers=headers)
    assert listed.status_code == 200, listed.text
    items = {i["external_id"]: i for i in listed.json()["data"]["items"]}
    assert items["title-only-1"]["title_only_reason"] == _REASON
    assert items["readable-1"]["title_only_reason"] is None
    assert items["legacy-1"]["title_only_reason"] is None

    single = api_client.get(f"/api/entries/{items['title-only-1']['id']}", headers=headers)
    assert single.status_code == 200, single.text
    assert single.json()["data"]["title_only_reason"] == _REASON


def test_search_carries_the_title_only_reason(api_client: TestClient) -> None:
    """The search read path mirrors the reason like the entries paths do."""
    response = api_client.get(
        "/api/search",
        params={"q": "test", "min_score": 0.0},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = {i["external_id"]: i for i in response.json()["data"]["items"]}
    assert items["title-only-1"]["title_only_reason"] == _REASON
    assert items["readable-1"]["title_only_reason"] is None
