"""Integration tests for GET /api/search's kind parameter -- search-kind-filter work
order, job 1 (SC-3).

Protects:
- kind=<one or comma-separated kinds> narrows /api/search's results to those kinds
  only, each result carrying a top-level `kind` field (SC-5's mirror, extended to
  search).
- An unknown kind value gets a 422 naming the ten valid kinds -- exactly the same
  validation /api/entries uses (one shared helper, not a second hardcoded copy):
  the message is byte-for-byte comparable between the two endpoints for the same
  bad input.
- compact=true results still include kind.

Uses the real local Embedder for the query text (no external API cost, same pattern
as test_search_min_score_integration.py) with min_score=0.0 so the seeded items'
hand-set unit-vector embeddings are never excluded by the score floor -- the test is
about which kind matched, not about relevance ranking.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.kind_classifier import KINDS
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


@pytest.fixture
def kind_populated_search_storage(test_db: Path) -> Storage:
    """Storage with one item per kind (release, question, tutorial) plus one
    unclassified item, all sharing the same embedding so every item is an
    equally-close KNN candidate -- the test is about the kind filter, not ranking.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")

    seeds = [
        ("release-1", "release", 0.92),
        ("question-1", "question", 0.81),
        ("tutorial-1", "tutorial", 0.75),
        ("unclassified-1", None, 0.4),
    ]
    for external_id, kind, confidence in seeds:
        item = ContentItem(
            source_id=source_id,
            external_id=external_id,
            title=f"Item {external_id}",
            url=f"https://example.com/{external_id}",
            content="Body text",
            priority="high",
            published_at=None,
            analysis={"kind": kind, "kind_confidence": confidence},
        )
        content_id = add_new_content(storage, item)
        storage.add_embedding(content_id, _unit_vector(0))

    return storage


@pytest.fixture
def search_client(
    kind_populated_search_storage: Storage,
) -> Generator[TestClient, None, None]:
    def override_get_storage() -> Generator[Storage, None, None]:
        yield kind_populated_search_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_single_kind_filter_returns_only_that_kind_with_top_level_mirror(
    search_client: TestClient,
) -> None:
    """
    INVARIANT: kind=<one kind> returns only items whose analysis.kind matches, each
    carrying a top-level kind field.
    BREAKS: search returns every kind regardless of the filter, or omits the
    top-level kind mirror /api/entries already has.
    """
    response = search_client.get(
        "/api/search?q=test&min_score=0.0&kind=release",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["external_id"] == "release-1"
    assert items[0]["kind"] == "release"


def test_comma_separated_kind_filter_returns_the_union(
    search_client: TestClient,
) -> None:
    """
    INVARIANT: kind=a,b returns the union of items in either kind.
    BREAKS: comma-separated filtering regresses to matching only the first value.
    """
    response = search_client.get(
        "/api/search?q=test&min_score=0.0&kind=release,tutorial",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {"release-1", "tutorial-1"}
    for item in items:
        assert item["kind"] in {"release", "tutorial"}


def test_unclassified_items_excluded_from_a_kind_filter(
    search_client: TestClient,
) -> None:
    """
    INVARIANT: an item with no kind never matches a kind filter, whatever is
    requested.
    BREAKS: filtering leaks unclassified items into every kind bucket.
    """
    response = search_client.get(
        "/api/search?q=test&min_score=0.0&kind=release,question,tutorial",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    external_ids = {item["external_id"] for item in response.json()["data"]["items"]}
    assert "unclassified-1" not in external_ids


def test_no_kind_filter_still_mirrors_kind_onto_every_result(
    search_client: TestClient,
) -> None:
    """
    INVARIANT: without a kind filter, every result still carries the top-level kind
    mirror, including a null for the unclassified item.
    """
    response = search_client.get(
        "/api/search?q=test&min_score=0.0",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = {item["external_id"]: item for item in response.json()["data"]["items"]}
    assert items["release-1"]["kind"] == "release"
    assert items["question-1"]["kind"] == "question"
    assert items["tutorial-1"]["kind"] == "tutorial"
    assert items["unclassified-1"]["kind"] is None


def test_unknown_kind_rejected_with_422_naming_the_ten_valid_kinds(
    search_client: TestClient,
) -> None:
    """
    INVARIANT: a kind value outside the ten declared kinds is rejected before
    storage is ever queried, and the message names all ten -- exactly the same
    validation /api/entries applies for the identical bad input (one shared
    helper, not a second copy that could drift).
    BREAKS: a typo'd kind value silently returns zero results, or /api/search's
    error message diverges from /api/entries' for the same input.
    """
    response = search_client.get(
        "/api/search?q=test&kind=bogus",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 422, response.text
    data = response.json()
    assert data["success"] is False
    assert data["data"] is None
    assert "bogus" in data["message"]
    for valid_kind in KINDS:
        assert valid_kind in data["message"], (
            f"422 message must name every valid kind, missing {valid_kind!r}: "
            f"{data['message']!r}"
        )

    entries_response = search_client.get(
        "/api/entries?kind=bogus", headers={"X-API-Key": TEST_API_KEY}
    )
    assert entries_response.json()["message"] == data["message"], (
        "search-kind-filter SC-3: /api/search must reuse /api/entries' validation "
        "through one shared helper, not a second copy that could drift"
    )


def test_compact_mode_includes_kind(search_client: TestClient) -> None:
    """
    INVARIANT: compact=true results still carry the kind field.
    BREAKS: compact_fields drops kind, so LLM/CLI consumers using compact mode lose
    the field entirely.
    """
    response = search_client.get(
        "/api/search?q=test&min_score=0.0&kind=release&compact=true",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["kind"] == "release"
    # Fields the compact_fields set excludes still exist on the wire (every
    # ContentItemModel field is dumped) but are cleared to their default -- the
    # compact_fields filtering runs on the raw dict *before* ContentItemModel
    # construction, not on the final wire shape.
    assert items[0]["content"] is None
    assert items[0]["analysis"] is None
