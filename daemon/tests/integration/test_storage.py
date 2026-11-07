"""Integration tests for Storage class with real database."""

from pathlib import Path
from datetime import UTC, datetime, timedelta

from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem


def test_source_management_workflow(test_db: Path) -> None:
    """Test complete source management: add, retrieve, update status."""
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source(
        "https://example.com/feed.xml", "rss", "Example Feed"
    )
    assert source_id is not None
    assert len(source_id) == 36  # UUID string length

    # Add another source
    source_id2 = storage.add_source(
        "https://reddit.com/r/python", "reddit", "Python Reddit"
    )
    assert source_id2 is not None
    assert len(source_id2) == 36  # UUID string length
    assert source_id != source_id2  # Different UUIDs

    # Get active sources
    sources = storage.get_active_sources()
    assert len(sources) == 2

    # Find the RSS source (order isn't guaranteed with UUIDs)
    rss_source = next(s for s in sources if s["type"] == "rss")
    assert rss_source["url"] == "https://example.com/feed.xml"
    assert rss_source["name"] == "Example Feed"
    assert rss_source["active"] is True
    assert rss_source["error_count"] == 0

    # Update source status after successful fetch
    at = datetime.now(UTC)
    storage.record_fetch_success(source_id, 3, at, at)
    sources = storage.get_active_sources()
    rss_source = next(s for s in sources if s["type"] == "rss")
    assert rss_source["error_count"] == 0
    assert rss_source["last_fetched_at"] is not None
    assert rss_source["entries_served"] == 3

    # Update source status after failed fetch
    storage.record_fetch_failure(source_id, "Connection timeout", at, 30)
    sources = storage.get_active_sources()
    rss_source = next(s for s in sources if s["type"] == "rss")
    assert rss_source["error_count"] == 1
    assert rss_source["last_error"] == "Connection timeout"


def test_content_storage_with_deduplication(test_db: Path) -> None:
    """Test content storage and deduplication via external_id."""
    storage = Storage(test_db)

    # Add a source first
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Create content item
    item = ContentItem(
        source_id=source_id,
        external_id="unique-123",
        title="Test Article",
        url="https://example.com/article",
        content="Full article content here",
        summary="Article summary",
        priority="high",
        published_at=datetime(2024, 1, 15, 10, 0),
    )

    # First insert should succeed
    content_id = storage.add_content(item)
    assert content_id is not None
    assert len(content_id) == 36  # UUID string length

    # Duplicate insert should return None (deduplication)
    duplicate_id = storage.add_content(item)
    assert duplicate_id is None

    # Different external_id should succeed
    item.external_id = "unique-456"
    # Need to create a new ContentItem with new UUID for different external_id
    new_item = ContentItem(
        source_id=item.source_id,
        external_id="unique-456",
        title=item.title,
        url=item.url,
        content=item.content,
        summary=item.summary,
        priority=item.priority,
        published_at=item.published_at,
    )
    content_id2 = storage.add_content(new_item)
    assert content_id2 is not None
    assert len(content_id2) == 36  # UUID string length
    assert content_id != content_id2  # Different UUIDs


def test_priority_based_retrieval_and_marking_read(test_db: Path) -> None:
    """Test retrieving content by priority and marking as read."""
    storage = Storage(test_db)

    # Add source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    # Add content with different priorities
    high_item = ContentItem(
        source_id=source_id,
        external_id="high-1",
        title="High Priority Article",
        url="https://example.com/high",
        content="Important content",
        priority="high",
        published_at=datetime.now(),
    )
    storage.add_content(high_item)

    medium_item = ContentItem(
        source_id=source_id,
        external_id="medium-1",
        title="Medium Priority Article",
        url="https://example.com/medium",
        priority="medium",
        published_at=datetime.now(),
    )
    storage.add_content(medium_item)

    low_item = ContentItem(
        source_id=source_id,
        external_id="low-1",
        title="Low Priority Article",
        url="https://example.com/low",
        priority="low",
        published_at=datetime.now(),
    )
    storage.add_content(low_item)

    # Get high priority content
    high_content = storage.get_content_by_priority("high")
    assert len(high_content) == 1
    assert high_content[0]["title"] == "High Priority Article"
    assert high_content[0]["read"] is False

    # Get medium priority content
    medium_content = storage.get_content_by_priority("medium")
    assert len(medium_content) == 1
    assert medium_content[0]["title"] == "Medium Priority Article"

    # Mark high priority as read
    marked = storage.mark_content_read(high_content[0]["id"])
    assert marked is True

    # High priority should now be empty (unread only)
    high_content_after = storage.get_content_by_priority("high")
    assert len(high_content_after) == 0

    # Try marking non-existent content
    marked_missing = storage.mark_content_read("nonexistent-content-id-999")
    assert marked_missing is False


def test_source_failures_back_off_and_never_deactivate(test_db: Path) -> None:
    """
    INVARIANT: each recorded failure counts, keeps the source active and sets
               next_fetch_at to the failure time plus 30 x 2^(n-1) minutes capped at
               1440; a recorded success clears the count, the error and the wait
    BREAKS: a failing source is switched off, or a recovered one stays on its wait
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")
    at = datetime(2026, 11, 1, tzinfo=UTC)

    waits = []
    for i in range(8):
        storage.record_fetch_failure(source_id, f"Error {i + 1}", at, 30)
        (source,) = storage.get_active_sources()
        assert source["active"] is True
        assert source["error_count"] == i + 1
        assert source["last_error"] == f"Error {i + 1}"
        waits.append(datetime.fromisoformat(source["next_fetch_at"]) - at)
    assert waits == [
        timedelta(minutes=m) for m in (30, 60, 120, 240, 480, 960, 1440, 1440)
    ]

    storage.record_fetch_success(source_id, None, None, at)
    (source,) = storage.get_active_sources()
    assert (source["error_count"], source["last_error"], source["next_fetch_at"]) == (
        0,
        None,
        None,
    )


def test_add_source_returns_existing_id_for_duplicate(test_db: Path) -> None:
    """Test that add_source returns existing UUID for duplicate URLs."""
    storage = Storage(test_db)

    # Add a source
    id1 = storage.add_source("https://example.com/feed", "rss", "First")
    assert id1 is not None
    assert len(id1) == 36  # UUID string length

    # Try to add same URL again - should return same UUID
    id2 = storage.add_source("https://example.com/feed", "rss", "Second")
    assert id2 == id1  # Same UUID as first

    # Verify only one source exists
    sources = storage.get_active_sources()
    assert len(sources) == 1
    assert sources[0]["name"] == "First"  # Original name preserved


def test_content_with_json_analysis(test_db: Path) -> None:
    """Test storing and retrieving content with JSON analysis field."""
    storage = Storage(test_db)

    # Add source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    # Create content with analysis
    item = ContentItem(
        source_id=source_id,
        external_id="json-test",
        title="Article with Analysis",
        url="https://example.com/article",
        analysis={
            "topics": ["python", "testing", "database"],
            "relevance_score": 0.85,
            "sentiment": "positive",
        },
        priority="high",
    )

    storage.add_content(item)

    # Retrieve and verify JSON is properly stored/retrieved
    content = storage.get_content_by_priority("high")
    assert len(content) == 1
    assert content[0]["analysis"] == {
        "topics": ["python", "testing", "database"],
        "relevance_score": 0.85,
        "sentiment": "positive",
    }
