"""Integration tests for REST API - protecting invariants and handling failures."""

import asyncio
import pytest
from pathlib import Path
import time

from fastapi.testclient import TestClient
from prismis_daemon.api import app
from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem


@pytest.fixture
def api_client() -> TestClient:
    """Create test client for API."""
    return TestClient(app)


def test_api_auth_required(api_client: TestClient) -> None:
    """
    INVARIANT: API key required for all protected endpoints
    BREAKS: Unauthorized access to source management
    """
    # Test all protected endpoints without API key
    protected_endpoints = [
        ("GET", "/api/sources"),
        ("POST", "/api/sources", {"url": "https://example.com", "type": "rss"}),
        ("DELETE", "/api/sources/test-id"),
    ]

    for method, path, *data in protected_endpoints:
        if method == "GET":
            response = api_client.get(path)
        elif method == "POST":
            response = api_client.post(path, json=data[0])
        elif method == "DELETE":
            response = api_client.delete(path)

        assert response.status_code == 403, f"{method} {path} must require auth"
        data = response.json()
        assert data["success"] is False
        assert "API key" in data["message"]

    # Test with invalid API key
    response = api_client.get("/api/sources", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 403

    # Test with valid API key
    response = api_client.get("/api/sources", headers={"X-API-Key": "prismis-api-4d5e"})
    assert response.status_code == 200


def test_url_normalization(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: Special protocol URLs must be normalized to real URLs
    BREAKS: Fetchers expect real URLs, not protocol URLs
    """
    test_cases = [
        # (input_url, type, expected_normalized)
        ("reddit://rust", "reddit", "https://www.reddit.com/r/rust"),
        ("reddit://python", "reddit", "https://www.reddit.com/r/python"),
        (
            "youtube://UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "youtube",
            "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
        ),
        ("youtube://@fireship", "youtube", "https://www.youtube.com/@fireship"),
        (
            "https://simonwillison.net/atom/everything/",
            "rss",
            "https://simonwillison.net/atom/everything/",
        ),  # Real RSS feed
    ]

    for input_url, source_type, expected_url in test_cases:
        # Add source via API
        response = api_client.post(
            "/api/sources",
            json={"url": input_url, "type": source_type},
            headers={"X-API-Key": "prismis-api-4d5e"},
        )

        # API should return normalized URL
        assert response.status_code == 200, f"Failed for {input_url}: {response.json()}"
        data = response.json()
        assert data["success"] is True
        assert data["data"]["url"] == expected_url, f"Failed to normalize {input_url}"

        # Verify database stores normalized URL
        storage = Storage(test_db)
        sources = storage.get_all_sources()
        source = next((s for s in sources if s["id"] == data["data"]["id"]), None)
        assert source is not None
        assert source["url"] == expected_url, "Database should store normalized URL"


def test_source_validation_blocks_invalid(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: Invalid sources never enter database
    BREAKS: Fetchers crash on invalid sources
    """
    invalid_sources = [
        # These should all be rejected
        ("not-a-url", "rss"),
        ("https://definitely-not-a-real-domain-12345.com/feed.xml", "rss"),
        ("reddit://", "reddit"),  # Empty subreddit
        ("youtube://", "youtube"),  # Empty channel
        (
            "https://simonwillison.net/atom/everything/",
            "reddit",
        ),  # RSS URL for reddit type
    ]

    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    for url, source_type in invalid_sources:
        response = api_client.post(
            "/api/sources",
            json={"url": url, "type": source_type},
            headers={"X-API-Key": "prismis-api-4d5e"},
        )

        # Should reject with 422 validation error
        assert response.status_code == 422, f"Should reject invalid source: {url}"
        data = response.json()
        assert data["success"] is False
        assert "validation failed" in data["message"].lower()

    # Verify no invalid sources were added
    final_count = len(storage.get_all_sources())
    assert final_count == initial_count, "No invalid sources should be added"


def test_cascade_delete(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: Deleting source removes ALL its content
    BREAKS: Orphaned content in database
    """
    storage = Storage(test_db)

    # Add a real source
    response = api_client.post(
        "/api/sources",
        json={"url": "https://simonwillison.net/atom/everything/", "type": "rss"},
        headers={"X-API-Key": "prismis-api-4d5e"},
    )
    assert response.status_code == 200
    source_id = response.json()["data"]["id"]

    # Add content for this source
    content_items = [
        ContentItem(
            external_id=f"item-{i}",
            source_id=source_id,
            title=f"Test Item {i}",
            url=f"https://example.com/item-{i}",
            content=f"Content {i}",
            published_at=None,
        )
        for i in range(5)
    ]

    for item in content_items:
        storage.add_content(item)

    # Verify content exists by checking each priority level
    # Since we didn't set priority, they should be None/low priority
    conn = storage.conn
    cursor = conn.execute(
        "SELECT COUNT(*) FROM content WHERE source_id = ?", (source_id,)
    )
    content_count = cursor.fetchone()[0]
    assert content_count == 5

    # Delete the source via API
    response = api_client.delete(
        f"/api/sources/{source_id}", headers={"X-API-Key": "prismis-api-4d5e"}
    )
    assert response.status_code == 200

    # Verify source is gone
    sources = storage.get_all_sources()
    assert not any(s["id"] == source_id for s in sources)

    # Verify ALL content is gone (cascade delete)
    conn = storage.conn
    cursor = conn.execute(
        "SELECT COUNT(*) FROM content WHERE source_id = ?", (source_id,)
    )
    content_count = cursor.fetchone()[0]
    assert content_count == 0, "All content should be cascade deleted"


### CHECKPOINT 7: Implement Failure Mode Tests


def test_concurrent_source_adds(api_client: TestClient, test_db: Path) -> None:
    """
    FAILURE: Database locks during concurrent writes
    GRACEFUL: All requests eventually succeed with retries
    """
    storage = Storage(test_db)
    initial_count = len(storage.get_all_sources())

    # Use different real RSS feeds for variety
    feeds = [
        "https://simonwillison.net/atom/everything/",
        "https://xkcd.com/rss.xml",
        "https://feeds.bbci.co.uk/news/rss.xml",
        "https://hnrss.org/frontpage",
        "https://www.reddit.com/r/programming/.rss",
    ]

    # TestClient doesn't support true concurrency, but we can test rapid sequential adds
    # which will still test database locking and retry logic
    successful_adds = 0
    for i in range(10):
        feed_url = feeds[i % len(feeds)]
        response = api_client.post(
            "/api/sources",
            json={
                "url": feed_url,
                "type": "rss",
                "name": f"Feed {i}",
            },
            headers={"X-API-Key": "prismis-api-4d5e"},
        )
        if response.status_code == 200:
            successful_adds += 1

    # Should successfully add at least the 5 unique feeds
    assert successful_adds >= 5, f"Only {successful_adds} adds succeeded"

    # Verify at least 5 unique sources were added
    final_count = len(storage.get_all_sources())
    assert final_count >= initial_count + 5, "At least 5 unique sources should be added"


def test_validation_timeout(api_client: TestClient) -> None:
    """
    FAILURE: Source validation exceeds 5 second timeout
    GRACEFUL: Returns error quickly, doesn't hang
    """
    # Use a URL that will timeout (non-routable IP)
    start_time = time.time()

    response = api_client.post(
        "/api/sources",
        json={
            "url": "http://192.0.2.1/feed.xml",  # Non-routable IP (TEST-NET-1)
            "type": "rss",
        },
        headers={"X-API-Key": "prismis-api-4d5e"},
    )

    elapsed = time.time() - start_time

    # Should fail with validation error
    assert response.status_code == 422

    # Should timeout within ~5 seconds (not hang forever)
    assert elapsed < 10, f"Validation took {elapsed}s, should timeout at 5s"
    data = response.json()
    assert data["success"] is False
    assert "validation failed" in data["message"].lower()


### CHECKPOINT 8: Implement Confidence Tests


def test_api_performance(api_client: TestClient, test_db: Path) -> None:
    """
    CONFIDENCE: API responses within reasonable time for CRUD operations
    THRESHOLD: Adjusted for real network validation
    """
    # Add some test data first using direct storage (to avoid network delays)
    storage = Storage(test_db)
    for i in range(20):
        storage.add_source(f"https://example{i}.com/feed.xml", "rss", f"Feed {i}")

    operations = []

    # Test GET performance (20 sources)
    start = time.time()
    response = api_client.get("/api/sources", headers={"X-API-Key": "prismis-api-4d5e"})
    elapsed = time.time() - start
    assert response.status_code == 200
    operations.append(("GET", elapsed))

    # Test POST performance with real URL
    start = time.time()
    response = api_client.post(
        "/api/sources",
        json={"url": "https://simonwillison.net/atom/everything/", "type": "rss"},
        headers={"X-API-Key": "prismis-api-4d5e"},
    )
    elapsed = time.time() - start
    assert response.status_code == 200
    source_id = response.json()["data"]["id"]
    operations.append(("POST", elapsed))

    # Test DELETE performance
    start = time.time()
    response = api_client.delete(
        f"/api/sources/{source_id}", headers={"X-API-Key": "prismis-api-4d5e"}
    )
    elapsed = time.time() - start
    assert response.status_code == 200
    operations.append(("DELETE", elapsed))

    # GET and DELETE should be fast, POST can take longer due to validation
    assert operations[0][1] < 0.5, (
        f"GET took {operations[0][1] * 1000:.0f}ms, should be <500ms"
    )
    assert operations[2][1] < 0.5, (
        f"DELETE took {operations[2][1] * 1000:.0f}ms, should be <500ms"
    )
    # POST with real validation might take up to 10 seconds
    assert operations[1][1] < 10, f"POST took {operations[1][1]:.1f}s, should be <10s"
