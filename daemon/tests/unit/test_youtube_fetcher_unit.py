"""Unit tests for YouTubeFetcher logic functions."""

from datetime import datetime

from fetchers.youtube import YouTubeFetcher
from models import ContentItem


def test_normalize_channel_url_with_handle() -> None:
    """Test channel URL normalization with @handle format."""
    fetcher = YouTubeFetcher()

    # Test @handle format
    url = "@LexClips"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/@LexClips"

    # Test @handle with mixed case
    url = "@SomeChannel"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/@SomeChannel"


def test_normalize_channel_url_bare_name() -> None:
    """Test channel URL normalization with bare channel names."""
    fetcher = YouTubeFetcher()

    # Test bare channel name
    url = "LexClips"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/@LexClips"

    # Test channel name with numbers
    url = "TechChannel123"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/@TechChannel123"


def test_normalize_channel_url_full_url() -> None:
    """Test channel URL normalization with full URLs."""
    fetcher = YouTubeFetcher()

    # Test full URL - should pass through
    url = "https://www.youtube.com/@SomeChannel"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/@SomeChannel"

    # Test URL with /c/ format
    url = "https://www.youtube.com/c/ChannelName"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "https://www.youtube.com/c/ChannelName"

    # Test URL without https
    url = "http://youtube.com/channel/UC123"
    normalized = fetcher._normalize_channel_url(url)
    assert normalized == "http://youtube.com/channel/UC123"


def test_parse_vtt_transcript_basic() -> None:
    """Test VTT transcript parsing removes headers and timestamps."""
    fetcher = YouTubeFetcher()

    vtt_content = """WEBVTT
Kind: captions
Language: en

00:00:00.000 --> 00:00:02.000
Hello world

00:00:02.000 --> 00:00:04.000
This is a test
"""

    result = fetcher._parse_vtt_transcript(vtt_content)
    assert result == "Hello world This is a test"


def test_parse_vtt_transcript_with_duplicates() -> None:
    """Test VTT transcript parsing removes duplicate lines."""
    fetcher = YouTubeFetcher()

    # YouTube often repeats lines in captions
    vtt_content = """WEBVTT

00:00:00.000 --> 00:00:02.000
This line appears once

00:00:02.000 --> 00:00:04.000
This line appears once

00:00:04.000 --> 00:00:06.000
This is different

00:00:06.000 --> 00:00:08.000
This is different
"""

    result = fetcher._parse_vtt_transcript(vtt_content)
    # Should remove consecutive duplicates
    assert result == "This line appears once This is different"


def test_parse_vtt_transcript_with_html_tags() -> None:
    """Test VTT transcript parsing removes HTML tags."""
    fetcher = YouTubeFetcher()

    vtt_content = """WEBVTT

00:00:00.000 --> 00:00:02.000
<b>Bold text</b> and <i>italic</i>

00:00:02.000 --> 00:00:04.000
Normal text with <00:00:03.500>timestamp tag
"""

    result = fetcher._parse_vtt_transcript(vtt_content)
    assert result == "Bold text and italic Normal text with timestamp tag"


def test_parse_vtt_transcript_with_cue_numbers() -> None:
    """Test VTT transcript parsing skips cue identifiers."""
    fetcher = YouTubeFetcher()

    vtt_content = """WEBVTT

1
00:00:00.000 --> 00:00:02.000
First subtitle

2
00:00:02.000 --> 00:00:04.000
Second subtitle
"""

    result = fetcher._parse_vtt_transcript(vtt_content)
    assert result == "First subtitle Second subtitle"


def test_parse_upload_date_valid() -> None:
    """Test parsing valid YouTube date format."""
    fetcher = YouTubeFetcher()

    # Test valid YYYYMMDD format
    date_str = "20240815"
    result = fetcher._parse_upload_date(date_str)
    assert result is not None
    assert result.year == 2024
    assert result.month == 8
    assert result.day == 15


def test_parse_upload_date_invalid() -> None:
    """Test parsing invalid date formats."""
    fetcher = YouTubeFetcher()

    # Test invalid format
    result = fetcher._parse_upload_date("2024-08-15")
    assert result is None

    # Test None input
    result = fetcher._parse_upload_date(None)
    assert result is None

    # Test empty string
    result = fetcher._parse_upload_date("")
    assert result is None

    # Test garbage input
    result = fetcher._parse_upload_date("notadate")
    assert result is None


def test_handle_missing_transcript() -> None:
    """Test ContentItem creation for videos without transcripts."""
    fetcher = YouTubeFetcher()

    video = {
        "title": "Test Video",
        "url": "https://www.youtube.com/watch?v=test123",
        "upload_date": "20240815",
        "id": "test123",
        "view_count": 1000,
        "duration": 300,
    }

    source_id = "source-uuid-123"

    result = fetcher._handle_missing_transcript(video, source_id)

    # Verify ContentItem created correctly
    assert isinstance(result, ContentItem)
    assert result.source_id == source_id
    assert result.title == "Test Video"
    assert result.url == "https://www.youtube.com/watch?v=test123"
    assert result.external_id == "https://www.youtube.com/watch?v=test123"
    assert result.priority == "low"  # Should be low priority
    assert result.notes == "No transcript available"
    assert "No transcript available" in result.content
    assert result.published_at is not None
    assert result.fetched_at is not None


def test_to_content_item_with_transcript() -> None:
    """Test ContentItem creation with transcript and metadata."""
    fetcher = YouTubeFetcher()

    video = {
        "title": "Test Video with Transcript",
        "url": "https://www.youtube.com/watch?v=abc123",
        "upload_date": "20240815",
        "id": "abc123",
        "view_count": 5000,
        "duration": 600,
    }

    transcript = "This is the video transcript content."
    source_id = "source-uuid-456"

    result = fetcher._to_content_item(video, transcript, source_id)

    # Verify ContentItem fields
    assert isinstance(result, ContentItem)
    assert result.source_id == source_id
    assert result.title == "Test Video with Transcript"
    assert result.url == "https://www.youtube.com/watch?v=abc123"
    assert result.external_id == "https://www.youtube.com/watch?v=abc123"
    assert result.content == transcript
    assert result.published_at is not None
    assert result.fetched_at is not None

    # Verify metrics in analysis
    assert result.analysis is not None
    assert "metrics" in result.analysis
    metrics = result.analysis["metrics"]
    assert metrics["video_id"] == "abc123"
    assert metrics["view_count"] == 5000
    assert metrics["duration"] == 600
