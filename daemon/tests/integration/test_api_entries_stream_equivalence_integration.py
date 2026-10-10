"""GET /api/entries streams its body item by item and still sends the bytes the dict
construction sent (bounded-list-memory work order, SC-1 and SC-2).

The previous construction -- every item built into one dict, then one JSON encode --
lives here as `_previous_body`, a copy of what the handler did before it streamed. A
request is the same request to both, over a real sealed database holding every priority,
long content and analysis, read, archived and near-duplicate rows, so any per-item
field, key order, filter, sort, dedup or limit that differs between the two shows up as
a byte difference in the body.
"""

import json
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismis_daemon import api
from prismis_daemon.api import app, get_storage
from prismis_daemon.api_models import (
    ContentItemModel,
    ContentResponse,
    ContentResponseData,
)
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

HEADERS = {"X-API-Key": TEST_API_KEY}
LONG = 'long body text, with unicode é中 and "quotes" \n' * 400
SINCE = "2020-01-01T00:00:00Z"

COMPACT_FIELDS = {
    "id", "title", "url", "priority", "kind", "title_only", "title_only_reason",
    "published_at", "source_name", "summary", "duplicate_count", "duplicate_sources",
}  # fmt: skip


@pytest.fixture
def seeded(test_db: Path) -> Generator[tuple[TestClient, Storage]]:
    storage = Storage(test_db)
    rss = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    reddit = storage.add_source("https://reddit.com/r/x", "reddit", "Reddit X")
    now = datetime.now(timezone.utc)
    specs: list[tuple[str, str, str | None, str, dict[str, Any]]] = [
        ("a", rss, "high", "Rust 1.80 released with new features", {"kind": "release"}),
        ("b", reddit, "high", "Rust 1.80 released with new features!", {"kind": "release"}),
        ("c", rss, "medium", "A tutorial on parsing", {"kind": "tutorial", "full_text": LONG}),
        ("d", reddit, "low", "Why is my build slow", {"kind": "question", "title_only": True,
                                                       "title_only_reason": "content:no_prose"}),
        ("e", rss, None, "Unclassified oddity", {}),
        ("f", rss, "high", "Deep dive", {"kind": "reference",
                                         "deep_extraction": {"synthesis": LONG}}),
        ("g", reddit, "medium", "Another medium item", {"kind": "news", "metrics": {"n": 1}}),
        ("h", rss, "low", "Archived thing", {"kind": "news"}),
    ]  # fmt: skip
    ids = {}
    for i, (name, source_id, priority, title, analysis) in enumerate(specs):
        ids[name] = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=name,
                title=title,
                url=f"https://example.com/{name}",
                content=LONG if i % 2 == 0 else f"short {name}",
                summary=f"summary {name}",
                priority=priority,
                published_at=now - timedelta(hours=i),
                analysis=analysis,
            ),
        )
    storage.mark_content_read(ids["c"])
    storage.conn.execute(
        "UPDATE content SET archived_at = ? WHERE id = ?", (now.isoformat(), ids["h"])
    )
    storage.conn.execute(
        "UPDATE content SET user_feedback = 'up' WHERE id = ?", (ids["g"],)
    )
    storage.conn.commit()

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    yield TestClient(app), storage
    app.dependency_overrides.clear()
    storage.close()


def _previous_body(storage: Storage, q: dict[str, Any]) -> bytes:
    """What /api/entries sent before it streamed: the whole envelope as one dict."""
    priority = q.get("priority")
    kind = q.get("kind")
    unread_only = q.get("unread_only", False)
    include_archived = q.get("include_archived", False)
    interesting_override = q.get("interesting_override")
    limit = q.get("limit", 50)
    since = q.get("since")
    sort_by = q.get("sort_by")
    source = q.get("source")
    compact = q.get("compact", False)
    skip_dedup = q.get("skip_dedup", True)
    view = q.get("view", "full")

    priorities = [p.strip() for p in priority.split(",")] if priority else []
    kinds = api._parse_kind_filter(kind)
    effective_sort = sort_by if sort_by in ("priority", "date", "unread") else "priority"
    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00")) if since else None
    items = storage.get_content_list(
        limit if skip_dedup else max(limit, api.DEDUP_WINDOW),
        view=view,
        sort_by=effective_sort,
        since=since_dt,
        include_archived=include_archived,
        source_filter=source,
        kind_filter=kinds or None,
        priorities=priorities or None,
        unread_only=unread_only,
        interesting=interesting_override is True,
    )
    for item in items:
        item["kind"] = api._item_kind(item)
        item["title_only"] = api._item_title_only(item)
        item["title_only_reason"] = api._item_title_only_reason(item)
    if not skip_dedup:
        items = api.deduplicate_content(items[: min(len(items), api.DEDUP_WINDOW)])
    items = items[:limit]
    if compact:
        items = [{k: v for k, v in i.items() if k in COMPACT_FIELDS} for i in items]
    body = ContentResponse(
        success=True,
        message=f"Retrieved {len(items)} content items",
        data=ContentResponseData(
            items=[ContentItemModel(**i) for i in items],
            total=len(items),
            filters_applied={
                "priority": priority,
                "kind": kind,
                "unread_only": unread_only,
                "include_archived": include_archived,
                "interesting_override": interesting_override,
                "limit": limit,
                "since": since,
                "since_hours": None,
                "sort_by": effective_sort,
                "source": source,
                "compact": compact,
            },
        ),
    ).model_dump(
        mode="json",
        exclude={"data": {"items": {"__all__": {"content"}}}} if view == "list" else None,
    )
    return json.dumps(
        body, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    ).encode("utf-8")


REQUESTS: list[dict[str, Any]] = [
    {},
    {"view": "list"},
    {"view": "full", "limit": 3},
    {"view": "list", "limit": 3},
    {"view": "list", "priority": "high,low"},
    {"priority": "medium"},
    {"view": "list", "kind": "release,question"},
    {"sort_by": "date"},
    {"view": "list", "sort_by": "date"},
    {"sort_by": "unread"},
    {"view": "list", "sort_by": "unread", "unread_only": True},
    {"since": SINCE},
    {"view": "list", "since": SINCE, "limit": 2},
    {"include_archived": True},
    {"view": "list", "include_archived": True},
    {"source": "reddit"},
    {"view": "list", "interesting_override": True},
    {"skip_dedup": False},
    {"view": "list", "skip_dedup": False},
    {"skip_dedup": False, "limit": 2},
    {"compact": True},
    {"view": "list", "compact": True, "skip_dedup": False},
    {"source": "no-such-source"},
    {"view": "list", "source": "no-such-source"},
]


@pytest.mark.parametrize("params", REQUESTS, ids=[json.dumps(r) for r in REQUESTS])
def test_streamed_body_is_byte_identical_to_the_dict_construction(
    seeded: tuple[TestClient, Storage], params: dict[str, Any]
) -> None:
    """
    INVARIANT: /api/entries sends exactly the bytes the one-dict construction sent
    BREAKS: a per-item field, the key order, a filter, the dedup window or the count
            in the message differs between the stream and the dict
    """
    client, storage = seeded
    query = {
        k: (str(v).lower() if isinstance(v, bool) else v) for k, v in params.items()
    }
    response = client.get("/api/entries", params=query, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert response.content == _previous_body(storage, params)


def test_the_comparison_covers_a_populated_response(
    seeded: tuple[TestClient, Storage],
) -> None:
    """Guard: the equality above is not two empty envelopes."""
    client, _ = seeded
    for view in ("full", "list"):
        data = client.get("/api/entries", params={"view": view}, headers=HEADERS).json()
        assert len(data["data"]["items"]) == 7
        assert data["message"] == "Retrieved 7 content items"


def test_validation_errors_are_the_same_error_envelope_not_a_broken_stream(
    seeded: tuple[TestClient, Storage],
) -> None:
    """
    INVARIANT: a request that fails validation gets the JSON error envelope and a 4xx
    BREAKS: the error surfaces after the stream opened (200 with a partial body) or as a
            different envelope
    """
    client, _ = seeded
    bad_priority = client.get("/api/entries?priority=urgent", headers=HEADERS)
    assert bad_priority.status_code == 422
    assert bad_priority.json() == {
        "success": False,
        "message": "Invalid priority value(s): urgent. Must be one of: high, medium, low",
        "data": None,
    }
    bad_kind = client.get("/api/entries?kind=nope", headers=HEADERS)
    assert bad_kind.status_code == 422
    assert bad_kind.json()["success"] is False
    bad_since = client.get("/api/entries?since=not-a-date", headers=HEADERS)
    assert bad_since.status_code == 422, bad_since.text
    body = bad_since.json()
    assert body["success"] is False
    assert "Invalid ISO8601 timestamp: not-a-date" in body["message"]
    assert body["data"] is None
