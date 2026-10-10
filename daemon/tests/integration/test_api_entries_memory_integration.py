"""GET /api/entries costs memory in proportion to the rows and fields requested,
never to the corpus (memory-footprint work order, SC-1).

The measured defect: `get_content` loaded every non-archived row with its full
content and analysis, sorted in Python and only then sliced to `limit`, so one
`GET /api/entries?limit=10000` took cerebro's daemon from 561 MB to a 2,468 MB peak.

The database here holds 2,000 non-archived rows of 50 KB content and a 50 KB
analysis.full_text each (about 200 MB of text -- the shape of file-source items),
served by the real FastAPI app through its test client with no storage or API
internals stubbed. tracemalloc measures the peak Python allocation of each request;
the old path, which materialises the whole corpus, allocates well past the bound.
"""

import asyncio
import json
import tracemalloc
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY

ROW_COUNT = 2000
BLOB = "x" * 50_000
PEAK_BOUND_BYTES = 40 * 1024 * 1024


@pytest.fixture
def big_client(test_db: Path) -> Generator[TestClient]:
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/files", "file", "Files")
    now = datetime.now(timezone.utc)
    analysis = json.dumps({"kind": "reference", "full_text": BLOB})
    storage.conn.executemany(
        """
        INSERT INTO content (id, source_id, external_id, title, url, content, summary,
            analysis, priority, published_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                f"row-{i}",
                source_id,
                f"row-{i}",
                f"File {i}",
                f"file:///row-{i}",
                BLOB,
                "summary",
                analysis,
                ("high", "medium", "low", None)[i % 4],
                (now - timedelta(minutes=i)).isoformat(),
                now.isoformat(),
            )
            for i in range(ROW_COUNT)
        ),
    )
    storage.conn.commit()

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    yield TestClient(app)
    app.dependency_overrides.clear()
    storage.close()


def _peak_of_request(client: TestClient, path: str) -> tuple[int, list[dict]]:
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        response = client.get(path, headers={"X-API-Key": TEST_API_KEY})
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert response.status_code == 200, response.text
    return peak, response.json()["data"]["items"]


def test_default_view_with_small_limit_stays_under_the_bound(
    big_client: TestClient,
) -> None:
    peak, items = _peak_of_request(big_client, "/api/entries?limit=50")
    assert len(items) == 50
    assert peak < PEAK_BOUND_BYTES, f"peak {peak / 1e6:.1f} MB"


def test_list_view_over_the_whole_table_stays_under_the_bound(
    big_client: TestClient,
) -> None:
    peak, items = _peak_of_request(big_client, "/api/entries?limit=10000&view=list")
    assert len(items) == ROW_COUNT
    assert peak < PEAK_BOUND_BYTES, f"peak {peak / 1e6:.1f} MB"


def _peak_of_discarded_request(path: str, query: bytes) -> tuple[int, int]:
    """Peak allocation of one request whose body the caller throws away as it arrives.

    TestClient keeps the whole body, which is the client's memory and not the server's;
    here `send` counts the bytes and drops them, so the peak is the app's.
    """
    sent = 0

    async def send(message: dict[str, Any]) -> None:
        nonlocal sent
        if message["type"] == "http.response.body":
            sent += len(message.get("body", b""))

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
        "path": path,
        "raw_path": path.encode(),
        "root_path": "",
        "query_string": query,
        "headers": [(b"x-api-key", TEST_API_KEY.encode())],
        "client": ("testclient", 1234),
        "server": ("testserver", 80),
    }
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        asyncio.run(app(scope, receive, send))  # type: ignore[arg-type]
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return peak, sent


def test_full_view_over_the_whole_table_stays_under_the_bound(
    big_client: TestClient,
) -> None:
    """
    INVARIANT: a full-content 10,000-item request costs the app a few items, not the table
    BREAKS: the response is built or buffered whole -- about 200 MB of content and
            analysis.full_text here -- before the first byte goes out
    """
    query = b"limit=10000" + chr(38).encode() + b"view=full"
    peak, sent = _peak_of_discarded_request("/api/entries", query)
    assert sent > ROW_COUNT * 2 * len(BLOB)  # every row's content and full_text went out
    assert peak < PEAK_BOUND_BYTES, f"peak {peak / 1e6:.1f} MB"
