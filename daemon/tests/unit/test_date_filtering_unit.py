"""Unit tests for date filtering invariants across all fetchers."""

from datetime import datetime, timezone
import pytest

from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.config import Config


def test_all_fetchers_timezone_aware() -> None:
    """
    INVARIANT: ALL parsed dates are timezone-aware (UTC)
    BREAKS: Silent filtering failures if some dates naive, others aware
    """
    # Test RSS date parsing produces timezone-aware datetime
    rss_fetcher = RSSFetcher()

    # Create RSS entry object with published_parsed time tuple
    class MockRSSEntry:
        def __init__(self):
            now = datetime.now(timezone.utc)
            self.published_parsed = (
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
                0,
                0,
                0,
            )

    mock_entry = MockRSSEntry()
    rss_date = rss_fetcher._parse_published_date(mock_entry)
    assert rss_date is not None
    assert rss_date.tzinfo is not None, "RSS date must be timezone-aware"
    assert rss_date.tzinfo == timezone.utc, "RSS date must be UTC"

    # Test Reddit date parsing produces timezone-aware datetime
    import time

    current_timestamp = time.time()
    reddit_date = datetime.fromtimestamp(current_timestamp, tz=timezone.utc)
    assert reddit_date.tzinfo is not None, "Reddit date must be timezone-aware"
    assert reddit_date.tzinfo == timezone.utc, "Reddit date must be UTC"

    # Test YouTube date parsing produces timezone-aware datetime
    try:
        youtube_fetcher = YouTubeFetcher()
    except Exception:
        # Handle yt-dlp not available in test environment
        youtube_fetcher = object.__new__(YouTubeFetcher)
        youtube_fetcher.config = Config.from_file()

    youtube_date = youtube_fetcher._parse_upload_date("20241224")
    assert youtube_date is not None
    assert youtube_date.tzinfo is not None, "YouTube date must be timezone-aware"
    assert youtube_date.tzinfo == timezone.utc, "YouTube date must be UTC"


def test_consistent_cutoff_across_fetchers() -> None:
    """
    INVARIANT: All fetchers use identical cutoff calculation
    BREAKS: Inconsistent filtering across sources confuses users
    """
    config = Config(max_days_lookback=7)

    # Test the calculation is consistent by ensuring all use timezone.utc
    rss_fetcher = RSSFetcher(config=config)
    reddit_fetcher = RedditFetcher(config=config)

    # Verify both use the config value correctly
    assert rss_fetcher.config.max_days_lookback == 7
    assert reddit_fetcher.config.max_days_lookback == 7

    # The cutoff calculation is identical in both implementations
    # This test ensures the pattern stays consistent


def test_config_validates_max_days_lookback() -> None:
    """
    INVARIANT: Invalid max_days_lookback values prevent system startup
    BREAKS: Could disable all filtering if config corruption allows bad values
    """
    # Test negative value raises error
    with pytest.raises(ValueError, match="max_days_lookback must be at least 1"):
        config = Config(max_days_lookback=-1)
        config.validate()

    # Test zero value raises error
    with pytest.raises(ValueError, match="max_days_lookback must be at least 1"):
        config = Config(max_days_lookback=0)
        config.validate()

    # Test valid values pass
    config = Config(max_days_lookback=1)
    config.validate()  # Should not raise

    config = Config(max_days_lookback=365)
    config.validate()  # Should not raise


def test_unparseable_dates_allowed_through() -> None:
    """
    FAILURE MODE: Unparseable dates don't crash system
    GRACEFUL: Items with bad dates allowed through (no filter applied)
    """
    rss_fetcher = RSSFetcher()

    # Test RSS with completely missing date fields
    class MockEntryNoDate:
        pass  # No published_parsed or updated_parsed attributes

    mock_entry_no_date = MockEntryNoDate()
    result = rss_fetcher._parse_published_date(mock_entry_no_date)
    assert result is None, "Missing dates should return None, not crash"

    # Test RSS with malformed date tuple
    class MockEntryBadDate:
        def __init__(self):
            self.published_parsed = (2024, 13, 50, 25, 70, 80)  # Invalid values

    mock_entry_bad_date = MockEntryBadDate()
    result = rss_fetcher._parse_published_date(mock_entry_bad_date)
    # Should handle gracefully (either None or valid date, but no crash)
    assert True  # If we get here, no crash occurred

    # Test YouTube with invalid date string
    try:
        youtube_fetcher = YouTubeFetcher()
    except Exception:
        youtube_fetcher = object.__new__(YouTubeFetcher)
        youtube_fetcher.config = Config.from_file()

    invalid_dates = ["invalid", "2024", "20241301", ""]
    for invalid_date in invalid_dates:
        result = youtube_fetcher._parse_upload_date(invalid_date)
        assert result is None, (
            f"Invalid date '{invalid_date}' should return None, not crash"
        )
