"""Integration tests for RedditFetcher with real Reddit API."""

import pytest
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.models import ContentItem
from prismis_daemon.config import Config


def test_fetch_reddit_with_real_api() -> None:
    """Test complete Reddit fetching workflow with real API.

    This test:
    - Uses real Reddit API via PRAW
    - Fetches actual posts from a subreddit
    - Filters out image posts
    - Returns proper ContentItem objects
    """
    # Load config with Reddit credentials
    config = Config.from_file()
    fetcher = RedditFetcher(max_items=3, config=config)

    # Use a stable subreddit for testing
    source = {"url": "https://reddit.com/r/python", "id": "test-source-123"}

    # Fetch content - this makes real API calls
    items = fetcher.fetch_content(source)

    # Verify we got items back
    assert len(items) > 0
    assert len(items) <= 3  # Should respect max_items

    # Verify first item has all required fields
    if items:
        first_item = items[0]
        assert isinstance(first_item, ContentItem)
        assert first_item.source_id == "test-source-123"
        assert first_item.external_id is not None
        assert first_item.external_id.startswith("https://reddit.com/r/")
        assert first_item.title is not None
        assert len(first_item.title) > 0
        assert first_item.url is not None
        assert first_item.url.startswith("https://reddit.com")

        # Verify content was extracted
        assert first_item.content is not None
        assert len(first_item.content) > 0

        # Verify metrics were extracted
        assert first_item.analysis is not None
        assert "metrics" in first_item.analysis
        metrics = first_item.analysis["metrics"]
        assert "score" in metrics
        assert "upvote_ratio" in metrics
        assert "num_comments" in metrics
        assert "author" in metrics
        assert "subreddit" in metrics

        # Verify fetched_at was set
        assert first_item.fetched_at is not None

        # Verify consistent external IDs (no duplicates)
        external_ids = [item.external_id for item in items]
        assert len(external_ids) == len(set(external_ids))


def test_fetch_reddit_handles_invalid_subreddit() -> None:
    """Test fetcher handles invalid subreddit gracefully."""
    config = Config.from_file()
    fetcher = RedditFetcher(config=config)

    # Try to fetch from non-existent subreddit
    source = {
        "url": "https://reddit.com/r/thisubdoesnotexist123456789",
        "id": "test-id",
    }

    with pytest.raises(Exception) as exc_info:
        fetcher.fetch_content(source)

    # Should wrap error with context
    assert "Failed to fetch Reddit content" in str(exc_info.value)


def test_fetch_reddit_respects_max_items() -> None:
    """Test fetcher respects max_items configuration."""
    config = Config.from_file()
    fetcher = RedditFetcher(max_items=1, config=config)

    source = {"url": "r/python", "id": "test-id"}

    items = fetcher.fetch_content(source)
    assert len(items) <= 1


def test_fetch_reddit_filters_image_posts() -> None:
    """Test that image posts are filtered out."""
    config = Config.from_file()
    fetcher = RedditFetcher(max_items=10, config=config)

    # Use a subreddit that has mix of text and image posts
    source = {"url": "r/programming", "id": "test-id"}

    items = fetcher.fetch_content(source)

    # All returned items should be text posts (not image domains)
    image_domains = ["i.redd.it", "imgur.com", "v.redd.it"]
    for item in items:
        # Check that content doesn't start with image link
        for domain in image_domains:
            if item.content.startswith(f"Link: https://{domain}"):
                pytest.fail(
                    f"Found image post that should have been filtered: {item.url}"
                )


def test_fetch_reddit_handles_various_url_formats() -> None:
    """Test that various Reddit URL formats are parsed correctly."""
    config = Config.from_file()
    fetcher = RedditFetcher(max_items=1, config=config)

    url_formats = ["https://reddit.com/r/python", "r/python", "python"]

    for url in url_formats:
        source = {"url": url, "id": "test-id"}

        items = fetcher.fetch_content(source)
        assert len(items) > 0, f"Failed to fetch from URL format: {url}"
