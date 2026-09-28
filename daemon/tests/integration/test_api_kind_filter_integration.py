"""Integration tests for GET /api/entries kind filtering -- content-kind work order,
job 3 (SC-5).

Invariants protected:
- SC-5: /api/entries?kind=<one or more, comma-separated> returns only items whose
  analysis.kind is one of the requested kinds; every returned item carries a
  top-level kind field (mirrored from analysis.kind, the same convenience priority
  already gets); unclassified items never leak into a kind filter; and an unknown
  kind value gets a 422 naming the ten valid kinds, imported from kind_classifier's
  single declaration rather than a second hardcoded copy.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.kind_classifier import KINDS
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY


@pytest.fixture
def kind_populated_storage(test_db: Path) -> Storage:
    """Storage with content across several kinds, plus one unclassified item."""
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    recent_time = datetime.now(timezone.utc)

    test_content = [
        ContentItem(
            external_id="release-1",
            source_id=source_id,
            title="Prismis 2.0 released",
            url="https://example.com/release",
            content="Release notes",
            published_at=recent_time,
            priority="high",
            analysis={"kind": "release", "kind_confidence": 0.92},
        ),
        ContentItem(
            external_id="question-1",
            source_id=source_id,
            title="How do I configure this?",
            url="https://example.com/question",
            content="A question",
            published_at=recent_time,
            priority="medium",
            analysis={"kind": "question", "kind_confidence": 0.81},
        ),
        ContentItem(
            external_id="tutorial-1",
            source_id=source_id,
            title="How to set up prismis",
            url="https://example.com/tutorial",
            content="A tutorial",
            published_at=recent_time,
            priority="low",
            analysis={"kind": "tutorial", "kind_confidence": 0.75},
        ),
        ContentItem(
            external_id="unclassified-1",
            source_id=source_id,
            title="Ambiguous item",
            url="https://example.com/unclassified",
            content="Below threshold",
            published_at=recent_time,
            priority="low",
            analysis={"kind": None, "kind_confidence": 0.4},
        ),
    ]
    for item in test_content:
        storage.add_content(item)

    return storage


@pytest.fixture
def api_client(kind_populated_storage: Storage) -> Generator[TestClient, None, None]:
    """Test client for API with the kind-populated storage overridden in."""

    def override_get_storage() -> Generator[Storage, None, None]:
        yield kind_populated_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_single_kind_filter_returns_only_that_kind(api_client: TestClient) -> None:
    """
    INVARIANT: kind=<one kind> returns only items whose analysis.kind matches
    BREAKS: A caller asking for "release" gets other kinds or unclassified items
            mixed into the result
    """
    response = api_client.get(
        "/api/entries?kind=release", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["external_id"] == "release-1"
    assert items[0]["kind"] == "release"


def test_comma_separated_kind_filter_returns_the_union(api_client: TestClient) -> None:
    """
    INVARIANT: kind=a,b returns the union of items in either kind
    BREAKS: Comma-separated filtering, the same convention priority already supports,
            regresses to matching only the first value or rejecting the list outright
    """
    response = api_client.get(
        "/api/entries?kind=release,tutorial", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    items = response.json()["data"]["items"]
    external_ids = {item["external_id"] for item in items}
    assert external_ids == {"release-1", "tutorial-1"}
    for item in items:
        assert item["kind"] in {"release", "tutorial"}


def test_unclassified_items_excluded_from_a_kind_filter(api_client: TestClient) -> None:
    """
    INVARIANT: An item with no kind never matches a kind filter, whatever is requested
    BREAKS: Filtering leaks unclassified items into every kind bucket
    """
    response = api_client.get(
        "/api/entries?kind=release,question,tutorial",
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    external_ids = {item["external_id"] for item in response.json()["data"]["items"]}
    assert "unclassified-1" not in external_ids


def test_kind_field_appears_on_every_item_without_a_filter(
    api_client: TestClient,
) -> None:
    """
    INVARIANT: Every returned item carries a top-level kind field, whether classified
               or not, mirrored from analysis.kind the way priority already is
    BREAKS: A client has to reach into analysis itself to read the kind; the
            convenience SC-5 promises never lands
    """
    response = api_client.get("/api/entries", headers={"X-API-Key": TEST_API_KEY})
    assert response.status_code == 200, response.text
    items = {item["external_id"]: item for item in response.json()["data"]["items"]}

    assert items["release-1"]["kind"] == "release"
    assert items["question-1"]["kind"] == "question"
    assert items["tutorial-1"]["kind"] == "tutorial"
    assert items["unclassified-1"]["kind"] is None


def test_unknown_kind_rejected_with_422_naming_the_ten_valid_kinds(
    api_client: TestClient,
) -> None:
    """
    INVARIANT: A kind value outside the ten declared kinds is rejected before storage
               is ever queried, and the message names all ten so a caller can
               self-correct
    BREAKS: A typo'd kind value silently returns zero results instead of saying why,
            or the valid-kinds list in the error drifts from kind_classifier's single
            declaration
    """
    response = api_client.get(
        "/api/entries?kind=bogus", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 422, response.text
    data = response.json()
    assert data["success"] is False
    assert data["data"] is None
    assert "bogus" in data["message"]
    assert len(KINDS) == 10, (
        f"the ten kinds declaration drifted to {len(KINDS)} entries; SC-5's 422 "
        "names all of them, so this test's premise needs re-checking too"
    )
    for valid_kind in KINDS:
        assert valid_kind in data["message"], (
            f"422 message must name every valid kind, missing {valid_kind!r}: "
            f"{data['message']!r}"
        )


def test_one_unknown_kind_in_a_comma_separated_list_rejects_the_whole_request(
    api_client: TestClient,
) -> None:
    """
    INVARIANT: A mix of valid and invalid kind values in one request is rejected, not
               silently narrowed to the valid subset
    BREAKS: A caller with a typo in a multi-kind request gets a partial, unexplained
            result instead of a 422 naming the bad value
    """
    response = api_client.get(
        "/api/entries?kind=release,bogus", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 422, response.text
    assert "bogus" in response.json()["message"]


def _content_id_for(storage: Storage, external_id: str) -> str:
    """The UUID storage assigned a fixture row, looked up the way other tests in this
    suite (e.g. test_api_integration.py's vote tests) already do."""
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", (external_id,)
    ).fetchone()
    return row[0]


def test_single_entry_endpoint_carries_kind_for_a_classified_item(
    api_client: TestClient, kind_populated_storage: Storage
) -> None:
    """
    INVARIANT: GET /api/entries/{id} carries the same top-level kind mirror the list
               endpoint does, for a classified item
    BREAKS: ContentItemModel.kind defaults to None, so without the mirror this
            endpoint reports every entry as unclassified -- including ones the
            classifier answered confidently -- a false value on a live endpoint the
            CLI's single-entry path (cli/src/cli/api_client.py) calls.
    """
    content_id = _content_id_for(kind_populated_storage, "release-1")

    response = api_client.get(
        f"/api/entries/{content_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] == "release"


def test_single_entry_endpoint_reports_null_kind_for_an_unclassified_item(
    api_client: TestClient, kind_populated_storage: Storage
) -> None:
    """
    INVARIANT: GET /api/entries/{id} reports kind: null for an item the classifier
               never confidently kinded
    BREAKS: An unclassified item on this endpoint gets confused with a real answer,
            or the mirror is only wired for the list endpoint and drifts from it here
    """
    content_id = _content_id_for(kind_populated_storage, "unclassified-1")

    response = api_client.get(
        f"/api/entries/{content_id}", headers={"X-API-Key": TEST_API_KEY}
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["kind"] is None
