"""GET /api/entries streams rows off a cursor after the handler returns, so the request's
Storage must stay open until the stream ends. That holds only because FastAPI runs a
yield dependency's exit after the response is sent; every other /api/entries test
overrides get_storage with one that never closes, so this one runs the real dependency.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

ROWS = Storage._LIST_FETCH_BATCH * 3 + 7


def test_full_list_streams_every_row_through_the_real_storage_dependency(
    test_db: Path,
) -> None:
    storage = Storage(test_db)
    source = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    now = datetime.now(timezone.utc)
    for i in range(ROWS):
        add_new_content(
            storage,
            ContentItem(
                source_id=source,
                external_id=f"item-{i}",
                title=f"Item {i}",
                url=f"https://example.com/{i}",
                content=f"body {i} " * 50,
                priority="high",
                published_at=now - timedelta(minutes=i),
                analysis={"kind": "news"},
            ),
        )
    storage.close()

    response = TestClient(app).get(
        "/api/entries",
        params={"limit": ROWS, "since": "2020-01-01T00:00:00Z"},
        headers={"X-API-Key": TEST_API_KEY},
    )

    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert sorted(item["title"] for item in items) == sorted(
        f"Item {i}" for i in range(ROWS)
    )
