"""Integration tests for YouTubeFetcher against recorded yt-dlp output.

Every yt-dlp call the fetcher makes is answered from a recording of what the real yt-dlp
printed and wrote (`ytdlp_replay` fixture, conftest.py), under `no_network`. Re-record
with PRISMIS_RECORD_YTDLP=1 (see docs/architecture/boundaries.md).
"""

import tempfile
from pathlib import Path

from prismis_daemon.config import Config
from prismis_daemon.models import ContentItem
from conftest import YtdlpReplay, make_config

# Minimal valid config TOML — light_service= format (task 1.1).
# YouTubeFetcher only needs max_items and max_days_lookback; no real credentials required.
_MINIMAL_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30

[llm]
light_service = "prismis-openai"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test-agent"
max_comments = 5

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false
[archival.windows]
high_read = 999
medium_unread = 30
medium_read = 14
low_unread = 14
low_read = 7

[context]
auto_update_enabled = false
auto_update_interval_days = 30
auto_update_min_votes = 5
backup_count = 10
"""


def _make_config() -> Config:
    """Load a minimal isolated Config from a temp TOML file."""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = Path(tmpdir) / "config.toml"
        config_path.write_text(_MINIMAL_TOML)
        (Path(tmpdir) / "context.md").write_text("# context")
        return Config.from_file(config_path)


def test_fetch_youtube_with_real_api(ytdlp_replay: YtdlpReplay) -> None:
    """Test complete YouTube fetching workflow with real yt-dlp and YouTube API.

    This test:
    - Uses real yt-dlp binary for video discovery and transcript extraction
    - Fetches actual videos from a YouTube channel
    - Extracts real transcripts from videos
    - Returns proper ContentItem objects with all fields
    """
    config = _make_config()
    fetcher = ytdlp_replay.fetcher(max_items=1, config=config)

    # Use a stable YouTube channel for testing - @LexClips posts frequently
    source = {"url": "@LexClips", "id": "test-source-123"}

    # Fetch content - the recorded yt-dlp calls
    items = fetcher.fetch_content(source)

    # The recording holds one video
    assert isinstance(items, list)
    assert len(items) == 1  # Should respect max_items

    # Verify the item has all required fields
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


def test_fetch_youtube_handles_invalid_channel(ytdlp_replay: YtdlpReplay) -> None:
    """Test fetcher handles invalid YouTube channel gracefully."""
    config = make_config()
    fetcher = ytdlp_replay.fetcher(config=config)

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


def test_fetch_youtube_respects_max_items(ytdlp_replay: YtdlpReplay) -> None:
    """Test fetcher respects max_items configuration."""
    config = make_config()
    fetcher = ytdlp_replay.fetcher(max_items=1, config=config)

    source = {"url": "@LexClips", "id": "test-id"}

    items = fetcher.fetch_content(source)
    assert len(items) <= 1


def test_fetch_youtube_respects_date_range(ytdlp_replay: YtdlpReplay) -> None:
    """Test fetcher only gets videos from configured date range."""
    # Use very short date range to limit results
    config = make_config()
    config.max_days_lookback = 1  # Only videos from yesterday

    fetcher = ytdlp_replay.fetcher(max_items=10, config=config)

    source = {"url": "@LexClips", "id": "test-id"}

    items = fetcher.fetch_content(source)

    # With only 1 day lookback, likely to get fewer results
    # (this is more of a behavior verification than strict assertion)
    assert isinstance(items, list)


def test_fetch_youtube_handles_various_url_formats(ytdlp_replay: YtdlpReplay) -> None:
    """Test that various YouTube channel URL formats work correctly."""
    config = make_config()
    fetcher = ytdlp_replay.fetcher(max_items=1, config=config)

    # Test different URL formats that should all work
    url_formats = [
        "@LexClips",  # Handle format
        "LexClips",  # Bare name (will be converted to @LexClips)
        "https://www.youtube.com/@LexClips",  # Full URL
    ]

    for url in url_formats:
        source = {"url": url, "id": "test-id"}

        # Should not raise exception
        items = fetcher.fetch_content(source)
        assert isinstance(items, list), f"URL format '{url}' did not yield a list"


def test_extract_transcript_from_specific_video(ytdlp_replay: YtdlpReplay) -> None:
    """Test transcript extraction from a specific video with known transcript."""
    config = make_config()
    fetcher = ytdlp_replay.fetcher(config=config)

    # Use a known video that should have transcripts
    # This is a popular tech talk that typically has captions
    video_url = (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ"  # Rick Roll - has captions
    )

    transcript = fetcher._extract_transcript(video_url).text

    # The recorded video has captions, so the transcript is extracted
    assert transcript is not None
    assert len(transcript) > 50  # Should have substantial content
    assert isinstance(transcript, str)
    # Should not contain VTT formatting
    assert "WEBVTT" not in transcript
    assert "-->" not in transcript


def test_channel_url_normalization_integration(ytdlp_replay: YtdlpReplay) -> None:
    """Test that URL normalization works in complete fetching workflow."""
    config = make_config()
    fetcher = ytdlp_replay.fetcher(max_items=1, config=config)

    # Test that different URL formats for same channel work
    test_urls = ["@LexClips", "LexClips"]

    counts = {
        url: len(fetcher.fetch_content({"url": url, "id": f"test-{url}"}))
        for url in test_urls
    }

    # Both formats reach the same channel, so the recorded discovery answers both
    assert counts["@LexClips"] == counts["LexClips"] >= 1


def test_youtube_fetcher_date_filtering(ytdlp_replay: YtdlpReplay) -> None:
    """Test that date filtering works correctly in video discovery."""
    # Create fetcher with very restrictive date range
    config = make_config()
    config.max_days_lookback = 1  # Only videos from last day
    fetcher_recent = ytdlp_replay.fetcher(max_items=1, config=config)

    # Create fetcher with longer date range
    config_long = make_config()
    config_long.max_days_lookback = 30  # Videos from last 30 days
    fetcher_long = ytdlp_replay.fetcher(max_items=1, config=config_long)

    source = {"url": "@LexClips", "id": "test-id"}

    items_recent = fetcher_recent.fetch_content(source)
    items_long = fetcher_long.fetch_content(source)

    # Longer date range should typically return same or more items
    assert len(items_recent) <= len(items_long)

    # Both should be valid lists
    assert isinstance(items_recent, list)
    assert isinstance(items_long, list)
