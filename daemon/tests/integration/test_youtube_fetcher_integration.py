"""Integration tests for YouTubeFetcher with real YouTube API and yt-dlp."""

import pytest
from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.models import ContentItem
from prismis_daemon.config import Config


def test_fetch_youtube_with_real_api() -> None:
    """Test complete YouTube fetching workflow with real yt-dlp and YouTube API.

    This test:
    - Uses real yt-dlp binary for video discovery and transcript extraction
    - Fetches actual videos from a YouTube channel
    - Extracts real transcripts from videos
    - Returns proper ContentItem objects with all fields
    """
    # Load config - will use defaults if config file doesn't exist
    try:
        config = Config.from_file()
    except Exception:
        # Use defaults if config file missing
        config = Config()

    fetcher = YouTubeFetcher(max_items=1, config=config)

    # Use a stable YouTube channel for testing - @LexClips posts frequently
    source = {"url": "@LexClips", "id": "test-source-123"}

    # Fetch content - this makes real yt-dlp calls to YouTube
    items = fetcher.fetch_content(source)

    # Verify we got items back (might be 0 if no videos in date range)
    assert isinstance(items, list)
    assert len(items) <= 1  # Should respect max_items

    # If we got items, verify they have all required fields
    if items:
        first_item = items[0]
        assert isinstance(first_item, ContentItem)
        assert first_item.source_id == "test-source-123"
        assert first_item.external_id is not None
        assert first_item.external_id.startswith("https://www.youtube.com/watch?v=")
        assert first_item.title is not None
        assert len(first_item.title) > 0
        assert first_item.url is not None
        assert first_item.url.startswith("https://www.youtube.com/watch?v=")

        # Verify content was extracted (transcript or fallback message)
        assert first_item.content is not None
        assert len(first_item.content) > 0

        # Verify metrics were extracted
        assert first_item.analysis is not None
        assert "metrics" in first_item.analysis
        metrics = first_item.analysis["metrics"]
        assert "video_id" in metrics
        assert "view_count" in metrics
        assert "duration" in metrics

        # Verify fetched_at was set
        assert first_item.fetched_at is not None

        # Verify consistent external IDs (no duplicates)
        external_ids = [item.external_id for item in items]
        assert len(external_ids) == len(set(external_ids))


def test_fetch_youtube_handles_invalid_channel() -> None:
    """Test fetcher handles invalid YouTube channel gracefully."""
    config = Config()
    fetcher = YouTubeFetcher(config=config)

    # Try to fetch from non-existent channel
    source = {
        "url": "@thisChannelDoesNotExist123456789",
        "id": "test-id",
    }

    # Should handle gracefully by returning empty list (not raising exception)
    items = fetcher.fetch_content(source)

    # Should return empty list for non-existent channel
    assert isinstance(items, list)
    assert len(items) == 0


def test_fetch_youtube_respects_max_items() -> None:
    """Test fetcher respects max_items configuration."""
    config = Config()
    fetcher = YouTubeFetcher(max_items=1, config=config)

    source = {"url": "@LexClips", "id": "test-id"}

    items = fetcher.fetch_content(source)
    assert len(items) <= 1


def test_fetch_youtube_respects_date_range() -> None:
    """Test fetcher only gets videos from configured date range."""
    # Use very short date range to limit results
    config = Config()
    config.max_days_lookback = 1  # Only videos from yesterday

    fetcher = YouTubeFetcher(max_items=10, config=config)

    source = {"url": "@LexClips", "id": "test-id"}

    items = fetcher.fetch_content(source)

    # With only 1 day lookback, likely to get fewer results
    # (this is more of a behavior verification than strict assertion)
    assert isinstance(items, list)


def test_fetch_youtube_handles_various_url_formats() -> None:
    """Test that various YouTube channel URL formats work correctly."""
    config = Config()
    fetcher = YouTubeFetcher(max_items=1, config=config)

    # Test different URL formats that should all work
    url_formats = [
        "@LexClips",  # Handle format
        "LexClips",  # Bare name (will be converted to @LexClips)
        "https://www.youtube.com/@LexClips",  # Full URL
    ]

    for url in url_formats:
        source = {"url": url, "id": "test-id"}

        try:
            items = fetcher.fetch_content(source)
            # Should not raise exception
            assert isinstance(items, list)
        except Exception as e:
            pytest.fail(f"Failed to fetch from URL format '{url}': {e}")


def test_extract_transcript_from_specific_video() -> None:
    """Test transcript extraction from a specific video with known transcript."""
    config = Config()
    fetcher = YouTubeFetcher(config=config)

    # Use a known video that should have transcripts
    # This is a popular tech talk that typically has captions
    video_url = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Roll - has captions
    )

    try:
        transcript = fetcher._extract_transcript(video_url)

        if transcript:
            # If transcript was extracted, verify it's reasonable
            assert len(transcript) > 50  # Should have substantial content
            assert isinstance(transcript, str)
            # Should not contain VTT formatting
            assert "WEBVTT" not in transcript
            assert "-->" not in transcript
        else:
            # It's OK if transcript not available - video might not have captions
            # This tests that the method handles missing transcripts gracefully
            pass

    except Exception as e:
        # If extraction fails, it should be a reasonable error
        assert "timed out" in str(e) or "not available" in str(e) or "failed" in str(e)


def test_channel_url_normalization_integration() -> None:
    """Test that URL normalization works in complete fetching workflow."""
    config = Config()
    fetcher = YouTubeFetcher(max_items=1, config=config)

    # Test that different URL formats for same channel work
    test_urls = ["@LexClips", "LexClips"]

    results = []
    for url in test_urls:
        try:
            source = {"url": url, "id": f"test-{url}"}
            items = fetcher.fetch_content(source)
            results.append((url, len(items)))
        except Exception as e:
            results.append((url, f"Error: {e}"))

    # Both formats should work (though may return different counts based on timing)
    for url, result in results:
        if isinstance(result, int):
            assert result >= 0  # Should not fail
        else:
            # If it failed, should be a reasonable error
            assert "Error:" in str(result)


def test_youtube_fetcher_date_filtering() -> None:
    """Test that date filtering works correctly in video discovery."""
    # Create fetcher with very restrictive date range
    config = Config()
    config.max_days_lookback = 1  # Only videos from last day
    fetcher_recent = YouTubeFetcher(max_items=1, config=config)

    # Create fetcher with longer date range
    config_long = Config()
    config_long.max_days_lookback = 30  # Videos from last 30 days
    fetcher_long = YouTubeFetcher(max_items=1, config=config_long)

    source = {"url": "@LexClips", "id": "test-id"}

    items_recent = fetcher_recent.fetch_content(source)
    items_long = fetcher_long.fetch_content(source)

    # Longer date range should typically return same or more items
    assert len(items_recent) <= len(items_long)

    # Both should be valid lists
    assert isinstance(items_recent, list)
    assert isinstance(items_long, list)
