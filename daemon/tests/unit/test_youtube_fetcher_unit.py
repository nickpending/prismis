"""Unit tests for YouTubeFetcher logic functions."""

import subprocess
import sys

from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.models import ContentItem


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


def test_youtube_fetcher_yt_dlp_env_uses_the_daemon_own_python_module() -> None:
    """
    SC-5: the fetcher runs yt-dlp as `<this interpreter> -m yt_dlp`, not whatever
    `yt-dlp` resolves to first on PATH.
    BREAKS: `shutil.which("yt-dlp")` resolves to a separate `uv tool install
    yt-dlp` when one is on PATH ahead of the daemon's own venv, silently running
    an install with no curl-cffi extra regardless of what pyproject.toml declares.
    """
    fetcher = YouTubeFetcher()
    assert fetcher.yt_dlp_cmd == [sys.executable, "-m", "yt_dlp"]


def test_youtube_fetcher_yt_dlp_env_command_actually_runs(
    tmp_path,
) -> None:
    """
    SC-5: `<this interpreter> -m yt_dlp` is a real, runnable command in the
    daemon's own environment -- exercised through the real subprocess boundary
    with an outcome this test controls (--version makes no network call).
    """
    fetcher = YouTubeFetcher()

    result = subprocess.run(
        [*fetcher.yt_dlp_cmd, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        cwd=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip()


def _video_fixture() -> dict:
    return {
        "title": "Already Readable Video",
        "url": "https://www.youtube.com/watch?v=already-readable",
        "upload_date": "20240815",
        "id": "already-readable",
        "view_count": 10,
        "duration": 60,
    }


def _recording_yt_dlp_cmd(marker_path) -> list[str]:
    """A real, runnable subprocess that records whether it was ever invoked --
    swapped in for the daemon's own yt-dlp command (a public field this fetcher
    builds at construction) so the proof runs through the real subprocess
    boundary rather than patching `_extract_transcript` itself."""
    script = f"open({str(marker_path)!r}, 'a').write('called\\n')"
    return [sys.executable, "-c", script]


def test_process_video_skip_known_readable_skips_transcript_download(tmp_path) -> None:
    """
    SC-4: a video whose external_id (its URL) is already stored readably skips
    the transcript download entirely -- the orchestrator's dedup filter drops the
    result either way.
    BREAKS: Threading known_readable_ids through fetch_content but not
    _process_video still shells out to yt-dlp for every already-readable video on
    every single cycle.
    """
    fetcher = YouTubeFetcher()
    marker = tmp_path / "invoked.marker"
    fetcher.yt_dlp_cmd = _recording_yt_dlp_cmd(marker)
    video = _video_fixture()

    result = fetcher._process_video(
        video, "source-uuid", known_readable_ids={video["url"]}
    )

    assert result is not None
    assert "No transcript available" in (result.content or "")
    assert not marker.exists(), (
        "SC-4: yt-dlp must not run at all for a video already stored readably"
    )


def test_process_video_without_skip_still_invokes_yt_dlp(tmp_path) -> None:
    """Companion to the skip test above: proves the recording command actually
    would have been invoked absent the skip, so the previous test's negative
    assertion is meaningful rather than vacuously true."""
    fetcher = YouTubeFetcher()
    marker = tmp_path / "invoked.marker"
    fetcher.yt_dlp_cmd = _recording_yt_dlp_cmd(marker)
    video = _video_fixture()

    fetcher._process_video(video, "source-uuid", known_readable_ids=set())

    assert marker.exists(), "the recording command should have run"


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
    assert result.content is not None
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
