"""The api.request log line carries item_count without the middleware reading a body
(bounded-list-memory work order, SC-3).

The middleware used to buffer every /api/entries and /api/search body and parse it to
count the items, which held a second copy of the whole response. Now a list handler puts
the count on request state and the middleware logs that. Each test reads the real
observability JSONL the request wrote.
"""

import asyncio
import json
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi import Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.observability import get_logger
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

HEADERS = {"X-API-Key": TEST_API_KEY}


@pytest.fixture
def storage(test_db: Path) -> Generator[Storage]:
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)
    for i in range(3):
        add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=f"e{i}",
                title=f"Item {i}",
                url=f"https://example.com/{i}",
                content="body",
                summary="s",
                priority="high",
                published_at=now - timedelta(minutes=i),
                analysis={},
            ),
        )

    yield storage
    storage.close()


@pytest.fixture
def client(storage: Storage) -> Generator[TestClient]:
    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    yield TestClient(app)
    app.dependency_overrides.clear()


def _request_events(path: str) -> list[dict[str, Any]]:
    events = []
    for file in sorted(get_logger().base_dir.glob("*_events.jsonl")):
        for line in file.read_text().splitlines():
            event = json.loads(line)
            if event.get("event") == "api.request" and event.get("path") == path:
                events.append(event)
    return events


def _item_count_of(event: dict[str, Any]) -> int | None:
    return event["item_count"]


@pytest.mark.parametrize("view", ["full", "list"])
def test_entries_logs_the_number_of_items_it_sent(client: TestClient, view: str) -> None:
    """
    INVARIANT: api.request for /api/entries carries item_count equal to the items sent
    BREAKS: the count is missing (None) or differs from the body once the middleware
            stops parsing the body
    """
    response = client.get(f"/api/entries?view={view}&limit=2", headers=HEADERS)
    assert response.status_code == 200
    sent = len(response.json()["data"]["items"])
    assert sent == 2
    events = _request_events("/api/entries")
    assert [_item_count_of(e) for e in events] == [sent]


def test_search_logs_the_number_of_items_it_sent(
    client: TestClient, storage: Storage
) -> None:
    """
    INVARIANT: api.request for /api/search carries item_count equal to the items sent
    BREAKS: search results go uncounted in the log
    """
    # Real local embedder for the query (same pattern as the other search tests); every
    # item shares one embedding and min_score=0.0 keeps them all.
    for (content_id,) in storage.conn.execute("SELECT id FROM content").fetchall():
        embedding = [0.0] * 384
        embedding[0] = 1.0
        storage.add_embedding(content_id, embedding)

    response = client.get("/api/search?q=anything&min_score=0.0", headers=HEADERS)
    assert response.status_code == 200
    sent = len(response.json()["data"]["items"])
    assert sent == 3
    assert [_item_count_of(e) for e in _request_events("/api/search")] == [sent]


def test_a_count_on_request_state_is_logged_whatever_the_body_says(
    client: TestClient,
) -> None:
    """
    INVARIANT: the logged count is what the handler put on request state
    BREAKS: the middleware derives the count from the body, so a body that is not a
            list envelope logs None and the state is ignored
    """

    @app.get("/api/_probe_state_only")
    async def state_only(request: Request) -> PlainTextResponse:
        request.state.item_count = 7
        return PlainTextResponse("not json at all")

    # A catch-all static mount sits last; the probe route has to precede it.
    app.router.routes.insert(0, app.router.routes.pop())
    try:
        assert client.get("/api/_probe_state_only").status_code == 200
    finally:
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/_probe_state_only"
        ]

    assert [_item_count_of(e) for e in _request_events("/api/_probe_state_only")] == [7]


def test_the_middleware_passes_the_entries_body_through_as_it_is_written(
    client: TestClient,
) -> None:
    """
    INVARIANT: the logging middleware does not buffer the /api/entries body
    BREAKS: it drains the body and re-sends it as one message, so the response is held
            whole in memory before the first byte goes out
    """
    bodies: list[bool] = []

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            bodies.append(message.get("more_body", False))

    requested = False

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await asyncio.Event().wait()  # a connected client sends nothing more
        raise AssertionError("unreachable")

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/entries",
        "raw_path": b"/api/entries",
        "root_path": "",
        "query_string": b"limit=3",
        "headers": [(b"x-api-key", TEST_API_KEY.encode())],
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
    }
    asyncio.run(app(scope, receive, send))  # type: ignore[arg-type]

    assert len(bodies) > 3, bodies
    assert bodies[0] is True
    assert bodies[-1] is False
