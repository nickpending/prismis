"""Unit tests for data models."""

from datetime import datetime
from models import ContentItem, Source


def test_content_item_to_dict() -> None:
    """Test ContentItem.to_dict() transforms all fields correctly."""
    # Create ContentItem with all fields
    item = ContentItem(
        source_id="550e8400-e29b-41d4-a716-446655440000",  # Example UUID
        external_id="test-123",
        title="Test Article",
        url="https://example.com/article",
        content="Full article content",
        summary="Article summary",
        analysis={"topics": ["python", "testing"], "score": 0.9},
        priority="high",
        published_at=datetime(2024, 1, 15, 10, 30),
        fetched_at=datetime(2024, 1, 15, 11, 0),
        read=True,
        favorited=False,
        notes="Test notes",
    )

    # Convert to dict
    result = item.to_dict()

    # Verify all fields present and correct
    assert result["id"] is not None  # UUID should be auto-generated
    assert len(result["id"]) == 36  # UUID string length
    assert result["source_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result["external_id"] == "test-123"
    assert result["title"] == "Test Article"
    assert result["url"] == "https://example.com/article"
    assert result["content"] == "Full article content"
    assert result["summary"] == "Article summary"
    assert result["analysis"] == {"topics": ["python", "testing"], "score": 0.9}
    assert result["priority"] == "high"
    assert result["published_at"] == datetime(2024, 1, 15, 10, 30)
    assert result["fetched_at"] == datetime(2024, 1, 15, 11, 0)
    assert result["read"] is True
    assert result["favorited"] is False
    assert result["notes"] == "Test notes"


def test_content_item_to_dict_with_minimal_fields() -> None:
    """Test ContentItem.to_dict() with only required fields."""
    # Create ContentItem with minimal fields
    item = ContentItem(
        source_id="550e8400-e29b-41d4-a716-446655440001",
        external_id="min-456",
        title="Minimal Article",
        url="https://example.com/minimal",
    )

    # Convert to dict
    result = item.to_dict()

    # Verify required fields
    assert result["id"] is not None  # UUID should be auto-generated
    assert len(result["id"]) == 36  # UUID string length
    assert result["source_id"] == "550e8400-e29b-41d4-a716-446655440001"
    assert result["external_id"] == "min-456"
    assert result["title"] == "Minimal Article"
    assert result["url"] == "https://example.com/minimal"

    # Verify optional fields are None/defaults
    assert result["content"] is None
    assert result["summary"] is None
    assert result["analysis"] is None
    assert result["priority"] is None
    assert result["published_at"] is None
    assert result["fetched_at"] is None
    assert result["read"] is False
    assert result["favorited"] is False
    assert result["notes"] is None


def test_source_to_dict() -> None:
    """Test Source.to_dict() transforms all fields correctly."""
    # Create Source with all fields - UUID will be auto-generated
    source = Source(
        url="https://example.com/feed.xml",
        type="rss",
        name="Example Feed",
        active=True,
        error_count=2,
        last_error="Connection timeout",
        last_fetched_at=datetime(2024, 1, 15, 9, 0),
    )

    # Convert to dict
    result = source.to_dict()

    # Verify all fields
    assert result["id"] is not None  # UUID should be auto-generated
    assert len(result["id"]) == 36  # UUID string length
    assert result["url"] == "https://example.com/feed.xml"
    assert result["type"] == "rss"
    assert result["name"] == "Example Feed"
    assert result["active"] is True
    assert result["error_count"] == 2
    assert result["last_error"] == "Connection timeout"
    assert result["last_fetched_at"] == datetime(2024, 1, 15, 9, 0)


def test_source_to_dict_with_defaults() -> None:
    """Test Source.to_dict() with default values."""
    # Create Source with minimal fields
    source = Source(url="https://reddit.com/r/python", type="reddit")

    # Convert to dict
    result = source.to_dict()

    # Verify required fields
    assert result["url"] == "https://reddit.com/r/python"
    assert result["type"] == "reddit"

    # Verify defaults
    assert result["id"] is not None  # UUID should be auto-generated
    assert len(result["id"]) == 36  # UUID string length
    assert result["name"] is None
    assert result["active"] is True
    assert result["error_count"] == 0
    assert result["last_error"] is None
    assert result["last_fetched_at"] is None
