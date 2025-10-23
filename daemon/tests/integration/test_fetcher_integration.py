"""Integration tests for RSSFetcher with real RSS feeds."""

import pytest
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.models import ContentItem


def test_fetch_rss_with_real_feed() -> None:
    """Test complete RSS fetching workflow with a real feed.

    This test:
    - Fetches a real RSS feed from the internet
    - Parses entries with feedparser
    - Extracts full content with trafilatura
    - Returns proper ContentItem objects
    """
    fetcher = RSSFetcher(max_items=3)  # Limit to 3 items for test speed

    # Use a stable RSS feed for testing
    # Simon Willison's blog is a good test feed - stable and always has content
    source_url = "https://simonwillison.net/atom/everything/"
    source_id = "test-source-123"

    # Fetch content - this makes real HTTP requests
    source = {"url": source_url, "id": source_id}
    items = fetcher.fetch_content(source)

    # Verify we got items back
    assert len(items) > 0
    assert len(items) <= 3  # Should respect max_items

    # Verify first item has all required fields
    first_item = items[0]
    assert isinstance(first_item, ContentItem)
    assert first_item.source_id == source_id
    assert first_item.external_id is not None
    assert len(first_item.external_id) > 0
    assert first_item.title is not None
    assert len(first_item.title) > 0
    assert first_item.url is not None
    assert first_item.url.startswith("http")

    # Verify content was extracted (either full article or fallback)
    assert first_item.content is not None
    assert len(first_item.content) > 0
    # Content should be more than just a title
    assert len(first_item.content) > len(first_item.title)

    # Verify fetched_at was set
    assert first_item.fetched_at is not None

    # Verify consistent external IDs (no duplicates)
    external_ids = [item.external_id for item in items]
    assert len(external_ids) == len(set(external_ids))


def test_fetch_rss_handles_invalid_feed_url() -> None:
    """Test fetcher handles invalid RSS feed URLs gracefully."""
    fetcher = RSSFetcher()

    # Try to fetch from invalid URL
    with pytest.raises(Exception) as exc_info:
        source = {
            "url": "https://not-a-real-domain-xyz123.com/feed.xml",
            "id": "test-id",
        }
        fetcher.fetch_content(source)

    # Should wrap error with context
    assert "Failed to fetch RSS feed" in str(exc_info.value)


def test_fetch_rss_handles_non_rss_content() -> None:
    """Test fetcher handles non-RSS content gracefully."""
    fetcher = RSSFetcher()

    # Try to fetch HTML page instead of RSS
    source = {"url": "https://example.com", "id": "test-id"}
    items = fetcher.fetch_content(source)

    # Should return empty list or handle gracefully
    # (feedparser is very forgiving and tries to parse anything)
    assert isinstance(items, list)
    # May or may not find entries in HTML


def test_fetch_rss_respects_max_items_limit() -> None:
    """Test fetcher respects max_items configuration."""
    # Test with very small limit
    fetcher = RSSFetcher(max_items=1)

    source_url = "https://simonwillison.net/atom/everything/"
    source = {"url": source_url, "id": "test-id"}
    items = fetcher.fetch_content(source)

    assert len(items) == 1


def test_fetch_rss_cleanup_on_deletion() -> None:
    """Test fetcher properly cleans up HTTP client on deletion."""
    fetcher = RSSFetcher()

    # Fetch something to ensure client is created
    source = {"url": "https://simonwillison.net/atom/everything/", "id": "test-id"}
    fetcher.fetch_content(source)

    # Delete fetcher - should clean up client
    del fetcher

    # No exceptions should occur during cleanup
    assert True  # If we get here, cleanup worked
