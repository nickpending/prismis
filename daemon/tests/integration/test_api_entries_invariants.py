"""Integration tests for GET /api/entries endpoint - protecting invariants and handling failures."""

from datetime import datetime, timezone
from pathlib import Path
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


@pytest.fixture
def populated_storage(test_db: Path) -> Storage:
    """Storage with test content across all priorities."""
    storage = Storage(test_db)

    # Add test sources
    rss_source_id = storage.add_source("https://example.com/rss", "rss", "RSS Source")
    reddit_source_id = storage.add_source(
        "https://reddit.com/r/test", "reddit", "Reddit Source"
    )

    # Add content items with different priorities and read states
    # Use recent datetime so get_content_since() will find them
    recent_time = datetime.now(timezone.utc)

    test_content = [
        # High priority content
        ContentItem(
            external_id="high-1",
            source_id=rss_source_id,
            title="Critical Security Update",
            url="https://example.com/security",
            content="Important security content",
            published_at=recent_time,
            priority="high",
            read=False,
        ),
        ContentItem(
            external_id="high-2",
            source_id=reddit_source_id,
            title="Breaking News Alert",
            url="https://reddit.com/r/test/breaking",
            content="Breaking news content",
            published_at=recent_time,
            priority="high",
            read=True,  # This one is read
        ),
        # Medium priority content
        ContentItem(
            external_id="medium-1",
            source_id=rss_source_id,
            title="Interesting Development",
            url="https://example.com/dev",
            content="Medium priority content",
            published_at=recent_time,
            priority="medium",
            read=False,
        ),
        ContentItem(
            external_id="medium-2",
            source_id=reddit_source_id,
            title="Tech Update",
            url="https://reddit.com/r/test/tech",
            content="Another medium content",
            published_at=recent_time,
            priority="medium",
            read=True,  # This one is read
        ),
        # Low priority content
        ContentItem(
            external_id="low-1",
            source_id=rss_source_id,
            title="General Article",
            url="https://example.com/general",
            content="Low priority content",
            published_at=recent_time,
            priority="low",
            read=False,
        ),
    ]

    for item in test_content:
        storage.add_content(item)

    return storage


@pytest.fixture
def api_client(populated_storage: Storage) -> TestClient:
    """Create test client for API with overridden storage dependency."""

    def override_get_storage() -> Generator[Storage, None, None]:
        yield populated_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def test_api_content_auth_required(api_client: TestClient) -> None:
    """
    INVARIANT: API authentication required for content access
    BREAKS: Unauthorized content access on LAN exposes private data
    """
    # Test without API key
    response = api_client.get("/api/entries")
    assert response.status_code == 403, "Content endpoint must require auth"
    data = response.json()
    assert data["success"] is False
    assert "API key" in data["message"]

    # Test with invalid API key
    response = api_client.get("/api/entries", headers={"X-API-Key": "invalid-key"})
    assert response.status_code == 403, "Invalid API key must be rejected"
    data = response.json()
    assert data["success"] is False

    # Test with valid API key
    response = api_client.get("/api/entries", headers={"X-API-Key": "prismis-api-4d5e"})
    assert response.status_code == 200, "Valid API key must be accepted"
    data = response.json()
    assert data["success"] is True


def test_api_content_error_format_consistency(api_client: TestClient) -> None:
    """
    INVARIANT: All validation errors use consistent JSON format
    BREAKS: TUI/CLI expect specific error format, would crash on validation errors
    """
    # Test invalid priority parameter (should trigger FastAPI validation)
    response = api_client.get(
        "/api/entries?priority=invalid", headers={"X-API-Key": "prismis-api-4d5e"}
    )
    assert response.status_code == 422, "Invalid priority should return 422"
    data = response.json()

    # Verify consistent error format (same as custom APIError format)
    assert "success" in data, "Error response must have 'success' field"
    assert "message" in data, "Error response must have 'message' field"
    assert "data" in data, "Error response must have 'data' field"
    assert data["success"] is False, "Error response success must be False"
    assert data["data"] is None, "Error response data must be None"
    assert "validation error" in data["message"].lower(), (
        "Must identify as validation error"
    )

    # Test invalid limit parameter
    response = api_client.get(
        "/api/entries?limit=999",  # Over max limit of 100
        headers={"X-API-Key": "prismis-api-4d5e"},
    )
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert data["data"] is None
    assert "validation error" in data["message"].lower()


def test_api_content_data_consistency(
    api_client: TestClient, populated_storage: Storage
) -> None:
    """
    INVARIANT: Filtered results must exactly match database state
    BREAKS: Data inconsistency confuses users, breaks automation that relies on accurate filtering
    """
    # Test priority filtering matches direct database query
    for priority in ["high", "medium", "low"]:
        # Get from API (includes both read and unread by default)
        response = api_client.get(
            f"/api/entries?priority={priority}",
            headers={"X-API-Key": "prismis-api-4d5e"},
        )
        assert response.status_code == 200
        api_items = response.json()["data"]["items"]

        # Get from database using the same logic the API uses
        # API uses get_content_since() then filters by priority when unread_only=False
        all_recent = populated_storage.get_content_since(hours=24 * 30)
        db_items = [item for item in all_recent if item.get("priority") == priority]

        # Must have same count
        assert len(api_items) == len(db_items), (
            f"API returned {len(api_items)} {priority} items, DB has {len(db_items)}"
        )

        # Must have same external_ids (order may differ due to database sorting)
        api_ids = {item["external_id"] for item in api_items}
        db_ids = {item["external_id"] for item in db_items}
        assert api_ids == db_ids, f"API and DB {priority} items don't match"

    # Test unread_only filtering matches database state
    response = api_client.get(
        "/api/entries?unread_only=true", headers={"X-API-Key": "prismis-api-4d5e"}
    )
    assert response.status_code == 200
    api_unread = response.json()["data"]["items"]

    # Count unread items in database directly
    conn = populated_storage.conn
    cursor = conn.execute("SELECT COUNT(*) FROM content WHERE read = 0")
    db_unread_count = cursor.fetchone()[0]

    assert len(api_unread) == db_unread_count, (
        f"API returned {len(api_unread)} unread items, DB has {db_unread_count}"
    )

    # Verify all returned items are actually unread
    for item in api_unread:
        assert item["read"] is False, (
            f"Item {item['external_id']} marked as unread but read=True"
        )


def test_api_content_database_disconnect(test_db: Path) -> None:
    """
    FAILURE: Database disconnection during request processing
    GRACEFUL: Returns clear error, doesn't crash or corrupt state
    """
    # Create storage with valid database but then corrupt it
    corrupted_storage = Storage(test_db)

    # Drop the content table to simulate corruption
    corrupted_storage.conn.execute("DROP TABLE content")

    # Override dependency with corrupted storage
    def override_get_storage() -> Generator[Storage, None, None]:
        yield corrupted_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)

    try:
        response = client.get("/api/entries", headers={"X-API-Key": "prismis-api-4d5e"})

        # Should return server error, not crash
        assert response.status_code == 500, (
            "Database corruption should return 500 error"
        )
        data = response.json()
        assert data["success"] is False
        assert "Failed to get content" in data["message"]

        # Error should be actionable (mention database issue)
        error_msg = data["message"].lower()
        assert any(
            keyword in error_msg
            for keyword in ["database", "connection", "no such table", "error"]
        ), f"Error message should indicate database issue: {data['message']}"
    finally:
        app.dependency_overrides.clear()


def test_api_content_invalid_parameters(
    api_client: TestClient, populated_storage: Storage
) -> None:
    """
    FAILURE: Invalid parameter combinations and edge cases
    GRACEFUL: Validates and rejects properly with clear errors
    """
    invalid_requests = [
        # Invalid priority value
        ("/api/entries?priority=critical", "priority"),
        # Limit too low
        ("/api/entries?limit=0", "limit"),
        # Limit too high
        ("/api/entries?limit=1000", "limit"),
        # Invalid boolean for unread_only
        ("/api/entries?unread_only=maybe", "unread_only"),
    ]

    for url, param_name in invalid_requests:
        response = api_client.get(url, headers={"X-API-Key": "prismis-api-4d5e"})
        assert response.status_code == 422, f"Should reject invalid {param_name}: {url}"
        data = response.json()
        assert data["success"] is False
        assert "validation error" in data["message"].lower()
        assert param_name in data["message"].lower(), (
            f"Error should mention parameter {param_name}"
        )

    # Valid edge cases should work
    valid_requests = [
        "/api/entries?limit=1",  # Minimum limit
        "/api/entries?limit=100",  # Maximum limit
        "/api/entries?unread_only=false",  # Explicit false
        "/api/entries?unread_only=true",  # Explicit true
    ]

    for url in valid_requests:
        response = api_client.get(url, headers={"X-API-Key": "prismis-api-4d5e"})
        assert response.status_code == 200, f"Should accept valid request: {url}"
        data = response.json()
        assert data["success"] is True
