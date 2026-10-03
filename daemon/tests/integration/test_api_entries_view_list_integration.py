"""GET /api/entries `view` parameter: the slim list shape beside the unchanged
default (memory-footprint work order, SC-3).

Real FastAPI app over a real sealed test database holding classified, title-only,
deep-extracted and plain items.
"""

import re
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

HEADERS = {"X-API-Key": TEST_API_KEY}
LIST_ANALYSIS_KEYS = {
    "kind",
    "kind_confidence",
    "title_only",
    "metrics",
    "metadata",
    "matched_interests",
    "preference_influenced",
}
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


@pytest.fixture
def seeded(test_db: Path) -> Generator[tuple[TestClient, dict[str, str]]]:
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)
    specs: dict[str, dict[str, object]] = {
        "classified": {
            "kind": "news",
            "kind_confidence": 0.9,
            "full_text": "FULLTEXT classified",
            "topics": ["a"],
        },
        "title-only": {"kind": "question", "title_only": True, "full_text": "FULLTEXT"},
        "deep": {
            "kind": "tutorial",
            "full_text": "FULLTEXT deep",
            "deep_extraction": {"synthesis": "S"},
        },
        "plain": {"full_text": "FULLTEXT plain"},
    }
    ids: dict[str, str] = {}
    for i, (name, analysis) in enumerate(specs.items()):
        ids[name] = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=name,
                title=f"Item {name}",
                url=f"https://example.com/{name}",
                content=f"BODY of {name}",
                summary=f"summary {name}",
                priority="high",
                published_at=now - timedelta(minutes=i),
                analysis=analysis,
            ),
        )

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    yield TestClient(app), ids
    app.dependency_overrides.clear()
    storage.close()


def _items(client: TestClient, query: str) -> dict[str, dict]:
    response = client.get(f"/api/entries?{query}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return {i["external_id"]: i for i in response.json()["data"]["items"]}


def test_view_list_has_no_content_and_only_list_analysis_keys(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    items = _items(client, "view=list")
    assert set(items) == {"classified", "title-only", "deep", "plain"}
    for item in items.values():
        assert "content" not in item
        assert set(item["analysis"]) <= LIST_ANALYSIS_KEYS
        assert "FULLTEXT" not in str(item)
        assert item["summary"] == f"summary {item['external_id']}"
    assert items["classified"]["analysis"] == {"kind": "news", "kind_confidence": 0.9}


def test_view_list_mirrors_kind_title_only_and_has_deep_extraction(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    items = _items(client, "view=list")
    assert items["classified"]["kind"] == "news"
    assert items["title-only"]["title_only"] is True
    assert items["classified"]["title_only"] is False
    assert items["deep"]["has_deep_extraction"] is True
    for name in ("classified", "title-only", "plain"):
        assert items[name]["has_deep_extraction"] is False
    assert items["plain"]["kind"] is None
    assert items["plain"]["analysis"] == {}


def test_view_list_datetimes_are_rfc3339_through_the_model(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    for item in _items(client, "view=list").values():
        for field in ("published_at", "fetched_at"):
            assert RFC3339_RE.match(item[field]), (field, item[field])


def test_default_view_keeps_content_and_full_analysis(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    for query in ("", "view=full"):
        items = _items(client, query)
        assert items["classified"]["content"] == "BODY of classified"
        assert items["classified"]["analysis"]["full_text"] == "FULLTEXT classified"
        assert items["classified"]["analysis"]["topics"] == ["a"]
        assert items["deep"]["analysis"]["deep_extraction"] == {"synthesis": "S"}
        assert items["deep"]["has_deep_extraction"] is True
        assert items["classified"]["has_deep_extraction"] is False


def test_compact_keeps_its_field_set(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    response = client.get("/api/entries?compact=true", headers=HEADERS)
    assert response.status_code == 200, response.text
    items = {i["title"]: i for i in response.json()["data"]["items"]}
    item = items["Item classified"]
    populated = {k for k, v in item.items() if v not in (None, False, [])}
    assert populated <= {
        "id",
        "title",
        "url",
        "priority",
        "kind",
        "title_only",
        "published_at",
        "source_name",
        "summary",
    }
    assert item["content"] is None
    assert item["analysis"] is None
    assert item["kind"] == "news"


def test_kind_filter_applies_before_limit_in_list_view(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    # "deep" is the third-newest of four; limit=1 with its kind must still find it
    items = _items(client, "view=list&kind=tutorial&limit=1")
    assert set(items) == {"deep"}


def test_limit_above_ceiling_is_still_refused(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    response = client.get("/api/entries?view=list&limit=10001", headers=HEADERS)
    assert response.status_code == 422


def test_unknown_view_is_refused(seeded) -> None:  # type: ignore[no-untyped-def]
    client, _ = seeded
    response = client.get("/api/entries?view=everything", headers=HEADERS)
    assert response.status_code == 422


def test_detail_endpoint_returns_full_content_and_analysis(seeded) -> None:  # type: ignore[no-untyped-def]
    client, ids = seeded
    response = client.get(f"/api/entries/{ids['deep']}?include=content", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["content"] == "BODY of deep"
    assert data["analysis"]["full_text"] == "FULLTEXT deep"
    assert data["analysis"]["deep_extraction"] == {"synthesis": "S"}
    assert data["has_deep_extraction"] is True
