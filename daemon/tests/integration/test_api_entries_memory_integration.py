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

import json
import tracemalloc
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
