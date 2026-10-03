"""Integration tests for GET /api/entries: the kind filter runs before the limit --
search-kind-filter work order, job 1 (SC-2).

Reproduces the measured defect (work order's `why`): the kind filter used to run as a
Python pass over content already bounded by a SQL `LIMIT` at the storage layer (newest
`limit` items overall, or per priority, or per flagged-item query). When the newest
`limit` items of that SQL fetch contain none of the requested kind, the Python
post-filter has nothing to find -- even though older matching items exist. The fix
pushes the kind filter into the storage query itself (`json_extract(c.analysis,
'$.kind') IN (...)`), so it narrows the candidates the SQL `LIMIT` is taken from,
rather than the rows that already survived it.

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


# ---------------------------------------------------------------------------
# Scenario 1: unread_only=true, no priority filter. Every scenario below goes
# through the one `Storage.get_content_list` query, whose WHERE (kind included)
# runs before its LIMIT.
# ---------------------------------------------------------------------------


@pytest.fixture
def unread_only_storage(test_db: Path) -> Storage:
    """30 recent unread 'news' items (medium priority) crowd out 5 older unread
    'question' items (also medium priority) from the newest-20 SQL window.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)

    for i in range(30):
        item = ContentItem(
            source_id=source_id,
            external_id=f"news-{i}",
            title=f"News item {i}",
            url=f"https://example.com/news/{i}",
            content="News content",
            priority="medium",
            published_at=now - timedelta(minutes=i),
            analysis={"kind": "news", "kind_confidence": 0.9},
        )
        add_new_content(storage, item)

    for i in range(5):
        item = ContentItem(
            source_id=source_id,
            external_id=f"question-{i}",
            title=f"Question item {i}",
            url=f"https://example.com/question/{i}",
            content="Question content",
            priority="medium",
            published_at=now - timedelta(days=10, minutes=i),
            analysis={"kind": "question", "kind_confidence": 0.85},
        )
        add_new_content(storage, item)

    return storage


@pytest.fixture
def unread_only_client(
    unread_only_storage: Storage,
) -> Generator[TestClient, None, None]:
    yield from _client_for(unread_only_storage)


def test_unread_only_all_priorities_kind_filter_finds_older_items_past_the_window(
    unread_only_client: TestClient,
) -> None:
    """
    INVARIANT (SC-2): unread_only=true with a kind filter and no priority returns
    the matching items even though they are older than the newest `limit` items of
    a different kind.
    BREAKS: a Python post-filter over the newest-20 SQL fetch finds zero 'question'
    items, since all 20 of the newest medium-priority unread items are 'news'.
    """
    response = unread_only_client.get(
        "/api/entries?kind=question&unread_only=true&limit=20",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {f"question-{i}" for i in range(5)}
    for item in items:
        assert item["kind"] == "question"


# ---------------------------------------------------------------------------
# Scenario 2: unread_only=true WITH an explicit priority filter.
# ---------------------------------------------------------------------------


def test_unread_only_with_priority_kind_filter_finds_older_items_past_the_window(
    unread_only_client: TestClient,
) -> None:
    """
    INVARIANT (SC-2): unread_only=true with BOTH a priority and a kind filter still
    finds kind matches older than the newest `limit` items of that priority.
    BREAKS: same defect as the all-priorities case, reached through the
    per-priority storage call instead.
    """
    response = unread_only_client.get(
        "/api/entries?kind=question&priority=medium&unread_only=true&limit=20",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {f"question-{i}" for i in range(5)}


# ---------------------------------------------------------------------------
# Scenario 3: interesting_override=true (user_feedback = 'up' in the same WHERE).
# ---------------------------------------------------------------------------


@pytest.fixture
def flagged_storage(test_db: Path) -> Storage:
    """10 recently-fetched flagged 'news' items crowd out 2 older flagged
    'question' items from a limit=5 fetch.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)

    for i in range(10):
        item = ContentItem(
            source_id=source_id,
            external_id=f"flagged-news-{i}",
            title=f"Flagged news {i}",
            url=f"https://example.com/flagged-news/{i}",
            content="News content",
            fetched_at=now - timedelta(minutes=i),
            analysis={"kind": "news", "kind_confidence": 0.9},
        )
        content_id = add_new_content(storage, item)
        storage.update_content_status(content_id, user_feedback="up")

    for i in range(2):
        item = ContentItem(
            source_id=source_id,
            external_id=f"flagged-question-{i}",
            title=f"Flagged question {i}",
            url=f"https://example.com/flagged-question/{i}",
            content="Question content",
            fetched_at=now - timedelta(days=10, minutes=i),
            analysis={"kind": "question", "kind_confidence": 0.85},
        )
        content_id = add_new_content(storage, item)
        storage.update_content_status(content_id, user_feedback="up")

    return storage


@pytest.fixture
def flagged_client(flagged_storage: Storage) -> Generator[TestClient, None, None]:
    yield from _client_for(flagged_storage)


def test_flagged_items_kind_filter_finds_older_items_past_the_window(
    flagged_client: TestClient,
) -> None:
    """
    INVARIANT (SC-2): interesting_override=true with a kind filter and a small
    limit still finds the older flagged items of that kind.
    BREAKS: a limit taken before the kind filter keeps only the 5 newest flagged
    items (all 'news'), and finds no 'question' items among them.
    """
    response = flagged_client.get(
        "/api/entries?interesting_override=true&kind=question&limit=5",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {f"flagged-question-{i}" for i in range(2)}


# ---------------------------------------------------------------------------
# Scenario 4: no unread_only, explicit priority (read and unread rows alike).
# ---------------------------------------------------------------------------


def test_priority_without_unread_only_kind_filter_still_applies(
    unread_only_client: TestClient,
) -> None:
    """
    INVARIANT (SC-2): kind filtering also works with a priority filter over read
    and unread rows alike.
    """
    response = unread_only_client.get(
        "/api/entries?kind=question&priority=medium&limit=20",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {f"question-{i}" for i in range(5)}
