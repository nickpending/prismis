"""Integration tests for audio briefing API - protecting invariants."""

import pytest
from pathlib import Path
from datetime import datetime

from fastapi.testclient import TestClient
from prismis_daemon import api
from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem

app = api.app


@pytest.fixture
def api_client() -> TestClient:
    """Create test client for API."""
    return TestClient(app)


def test_audio_fails_without_high_priority(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: AudioScriptGenerator requires HIGH priority content
    BREAKS: User gets confusing error vs actionable message
    """
    # Ensure database has content but no HIGH priority
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add only MEDIUM priority content
    item = ContentItem(
        source_id=source_id,
        external_id="test-medium-1",
        title="Medium Priority Article",
        url="https://example.com/article",
        content="Test content",
        summary="Test summary",
        priority="medium",
        published_at=datetime.now(),
    )
    storage.add_content(item)
    storage.close()

    # Call audio endpoint
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": "prismis-api-4d5e"},
    )

    # Should fail with ValidationError and helpful message
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert "high priority" in data["message"].lower()
    assert "Add content sources" in data["message"] or "adjust" in data["message"]


def test_audio_generates_with_high_priority(
    api_client: TestClient, test_db: Path, full_config: dict
) -> None:
    """
    INVARIANT: Audio generation succeeds with HIGH priority content
    BREAKS: Feature unusable if broken
    """
    # Add HIGH priority content
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Rust Blog")

    item = ContentItem(
        source_id=source_id,
        external_id="test-high-1",
        title="Important Rust Release",
        url="https://example.com/rust",
        content="Rust 1.80 introduces significant performance improvements and new features for async programming.",
        summary="Rust 1.80 introduces significant performance improvements",
        priority="high",
        published_at=datetime.now(),
    )
    storage.add_content(item)
    storage.close()

    # Call audio endpoint (uses real lspeak with system TTS)
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": "prismis-api-4d5e"},
        timeout=90,  # Allow time for real TTS generation
    )

    # Should succeed
    assert response.status_code == 200, f"Failed: {response.json()}"
    data = response.json()
    assert data["success"] is True
    assert "briefing-" in data["data"]["filename"]
    assert data["data"]["filename"].endswith(".mp3")
    assert data["data"]["high_priority_count"] >= 1
    assert data["data"]["provider"] in ["system", "elevenlabs"]

    # Verify file was actually created
    file_path = Path(data["data"]["file_path"])
    assert file_path.exists(), f"Audio file not created: {file_path}"
    assert file_path.stat().st_size > 0, "Audio file is empty"


def test_audio_timeout_protection(api_client: TestClient, test_db: Path) -> None:
    """
    INVARIANT: HTTP timeout >= generation time (60s > 30s max)
    BREAKS: User experiences permanent hang vs graceful timeout
    """
    # Add HIGH priority content
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    item = ContentItem(
        source_id=source_id,
        external_id="test-timeout-1",
        title="Test Article for Timeout",
        url="https://example.com/test",
        content="Test content for timeout verification",
        summary="Test content for timeout",
        priority="high",
        published_at=datetime.now(),
    )
    storage.add_content(item)
    storage.close()

    # Verify endpoint completes within reasonable time
    import time

    start = time.time()

    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": "prismis-api-4d5e"},
        timeout=65,  # Slightly longer than backend's 60s
    )

    duration = time.time() - start

    # Should complete or timeout gracefully, not hang forever
    assert duration < 65, "Request should complete or timeout within 65s"

    # If succeeded, great. If timed out, should be graceful error
    if response.status_code == 200:
        assert response.json()["success"] is True
    else:
        # Should be proper error, not a hang
        assert response.status_code in [500, 503, 504]


def test_audio_requires_authentication(api_client: TestClient) -> None:
    """
    INVARIANT: Audio endpoint requires API key authentication
    BREAKS: Unauthorized access to resource-intensive operation
    """
    # Without API key
    response = api_client.post("/api/audio/briefings")
    assert response.status_code == 403
    data = response.json()
    assert data["success"] is False
    assert "API key" in data["message"]

    # With invalid API key
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 403
