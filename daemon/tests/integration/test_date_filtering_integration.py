"""Integration tests for date filtering with real feeds."""

from datetime import datetime, timedelta, timezone
import pytest

from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.config import Config


def test_date_filtering_prevents_old_content() -> None:
    """
    INVARIANT: NO content older than max_days_lookback ever fetched
    BREAKS: Could cost hundreds in API charges if violated
    """
    # Create config with short lookback for testing
    config = Config(max_days_lookback=7, max_items=10)
    rss_fetcher = RSSFetcher(config=config)

    # Calculate expected cutoff
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)

    # Test with real RSS feed (Simon Willison's blog - reliable for testing)
    test_source = {
        "url": "https://simonwillison.net/atom/everything/",
        "id": "test-source-id",
    }

    try:
        # Fetch content with date filtering
        items = rss_fetcher.fetch_content(test_source)

        # CRITICAL INVARIANT: NO items older than cutoff
        old_items = []
        for item in items:
            if item.published_at and item.published_at < cutoff_date:
                old_items.append(item)

        assert len(old_items) == 0, (
            f"Found {len(old_items)} items older than {config.max_days_lookback} days - API cost protection FAILED"
        )

        # Verify we actually got some items (feed is active)
        assert len(items) > 0, "Should fetch some recent items"

        # Verify all fetched items have valid dates
        items_with_dates = [item for item in items if item.published_at is not None]
        assert len(items_with_dates) > 0, "Should have some items with valid dates"

        # Verify all dates are timezone-aware
        for item in items_with_dates:
            assert item.published_at.tzinfo is not None, (
                f"Item '{item.title}' has timezone-naive date"
            )

        print(
            f"✅ SUCCESS: Fetched {len(items)} items, all within {config.max_days_lookback} days"
        )

    except Exception as e:
        # Network failures are acceptable - skip test
        if any(
            keyword in str(e).lower()
            for keyword in ["network", "timeout", "connection", "dns"]
        ):
            pytest.skip(f"Network issue during test: {e}")
        else:
            raise


def test_network_timeout_graceful() -> None:
    """
    FAILURE MODE: Network timeout during fetch
    GRACEFUL: System continues, logs error, doesn't crash
    """
    # Use config with very short timeout to force failure
    config = Config(max_days_lookback=7)
    rss_fetcher = RSSFetcher(config=config, timeout=1)  # 1 second timeout

    # Use a slow/non-existent feed
    test_source = {
        "url": "https://httpstat.us/200?sleep=5000",  # Takes 5 seconds, will timeout
        "id": "test-timeout-source",
    }

    # Should raise exception but not crash the process
    with pytest.raises(Exception) as exc_info:
        rss_fetcher.fetch_content(test_source)

    # Verify error is wrapped with context (not bare network error)
    error_msg = str(exc_info.value)
    assert "Failed to fetch RSS feed" in error_msg, (
        "Error should be wrapped with context"
    )
    assert test_source["url"] in error_msg, "Error should include source URL"
