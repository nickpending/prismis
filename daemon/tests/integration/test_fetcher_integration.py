"""Integration tests for RSSFetcher against recorded RSS feeds and articles.

The feed and every article the fetcher extracts are replayed from a vcrpy cassette
(`http_cassette` fixture, conftest.py) under `no_network`, recorded once from the real
site; the article requests go through `extract_article`'s guarded urllib opener, which the
cassette serves like the feed request. Re-record with PRISMIS_RECORD_HTTP=1 (see
docs/architecture/boundaries.md).

The invalid-host and non-feed cases are not replayed: a host that does not resolve is the
reserved `.invalid` TLD, and a page that is not a feed is a loopback server serving one.
Neither goes under `no_network` or a cassette, which would make the dead proxy or the
replay the cause of the failure being asserted.
"""

import pytest
from conftest import LocalHttpServer, make_config
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.models import ContentItem

# The recorded feed's entries age every day the cassette is kept, and the fetcher drops
# entries older than the lookback, so the lookback is far wider than the recording will
# ever be old. What is under test is fetching, parsing and extracting, not the cutoff.
RECORDED_FEED_LOOKBACK_DAYS = 36500


def recorded_feed_fetcher(max_items: int) -> RSSFetcher:
    """A fetcher whose lookback still covers the recorded feed's entries."""
    return RSSFetcher(
        max_items=max_items,
        config=make_config(max_days_lookback=RECORDED_FEED_LOOKBACK_DAYS),
    )


@pytest.mark.usefixtures("http_cassette")
def test_fetch_rss_with_real_feed() -> None:
    """Test complete RSS fetching workflow with a real feed.

    This test:
    - Fetches a real RSS feed from the internet
    - Parses entries with feedparser
    - Extracts full content with trafilatura
    - Returns proper ContentItem objects
    """
    fetcher = recorded_feed_fetcher(max_items=3)

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
            "url": "https://no-such-host.invalid/feed.xml",
            "id": "test-id",
        }
        fetcher.fetch_content(source)

    # Should wrap error with context
    assert "Failed to fetch RSS feed" in str(exc_info.value)


def test_fetch_rss_handles_non_rss_content(local_http_server: LocalHttpServer) -> None:
    """Test fetcher handles non-RSS content gracefully."""
    fetcher = RSSFetcher()

    # An HTML page served where a feed was expected
    local_http_server.routes["/"] = (
        200,
        "text/html",
        b"<!doctype html><html><head><title>Example</title></head>"
        b"<body><h1>Example Domain</h1><p>Not a feed.</p></body></html>",
    )
    source = {"url": f"{local_http_server.base_url}/", "id": "test-id"}
    items = fetcher.fetch_content(source)

    # feedparser is forgiving, but a page with no feed entries yields no items
    assert local_http_server.requests == ["/"], "the fetcher must ask the server"
    assert items == []


@pytest.mark.usefixtures("http_cassette")
def test_fetch_rss_respects_max_items_limit() -> None:
    """Test fetcher respects max_items configuration."""
    # Test with very small limit
    fetcher = recorded_feed_fetcher(max_items=1)

    source_url = "https://simonwillison.net/atom/everything/"
    source = {"url": source_url, "id": "test-id"}
    items = fetcher.fetch_content(source)

    assert len(items) == 1


@pytest.mark.usefixtures("http_cassette")
def test_fetch_rss_cleanup_on_deletion() -> None:
    """Test fetcher properly cleans up HTTP client on deletion."""
    fetcher = recorded_feed_fetcher(max_items=1)

    # Fetch something to ensure client is created
    source = {"url": "https://simonwillison.net/atom/everything/", "id": "test-id"}
    fetcher.fetch_content(source)

    # Delete fetcher - should clean up client
    del fetcher

    # No exceptions should occur during cleanup
    assert True  # If we get here, cleanup worked
