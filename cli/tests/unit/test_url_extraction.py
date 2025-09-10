"""Unit tests for URL extraction and parsing logic."""

import sys
from pathlib import Path

# Add CLI src to path
cli_src = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(cli_src))

from cli.source import extract_name_from_url  # noqa: E402


def test_extract_name_from_rss_feeds() -> None:
    """Test name extraction from RSS feed URLs."""
    # Standard RSS feeds
    assert extract_name_from_url("https://example.com/feed.xml") == "Example"
    assert (
        extract_name_from_url("https://simonwillison.net/atom/everything/")
        == "Simonwillison"
    )
    assert extract_name_from_url("https://blog.rust-lang.org/feed.xml") == "Blog"

    # With www prefix
    assert extract_name_from_url("https://www.example.com/feed") == "Example"

    # Domain only
    assert extract_name_from_url("https://example.com") == "Example"

    # Complex domains
    assert extract_name_from_url("https://news.ycombinator.com/rss") == "News"


def test_extract_name_from_reddit_urls() -> None:
    """Test name extraction from Reddit URLs."""
    # reddit:// scheme - returns just the subreddit name after stripping prefix
    assert extract_name_from_url("reddit://rust") == "rust"
    assert extract_name_from_url("reddit://programming") == "programming"
    assert extract_name_from_url("reddit://python") == "python"

    # Standard Reddit URLs
    assert extract_name_from_url("https://reddit.com/r/rust") == "r/rust"
    assert (
        extract_name_from_url("https://www.reddit.com/r/programming") == "r/programming"
    )
    assert extract_name_from_url("https://reddit.com/r/golang/") == "r/golang"

    # With additional path segments
    assert extract_name_from_url("https://reddit.com/r/rust/hot") == "r/rust"
    assert extract_name_from_url("https://reddit.com/r/python/.json") == "r/python"


def test_extract_name_from_youtube_urls() -> None:
    """Test name extraction from YouTube URLs."""
    # youtube:// scheme
    assert extract_name_from_url("youtube://@mkbhd") == "@mkbhd"
    assert extract_name_from_url("youtube://@TwoMinutePapers") == "@TwoMinutePapers"

    # YouTube URLs with @ handles
    assert extract_name_from_url("https://youtube.com/@mkbhd") == "YouTube: @mkbhd"
    assert (
        extract_name_from_url("https://www.youtube.com/@veritasium")
        == "YouTube: @veritasium"
    )

    # YouTube channel URLs
    assert (
        extract_name_from_url("https://youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA")
        == "YouTube Channel: UC9-y-6csu5WGm29I7Ji"
    )
    assert (
        extract_name_from_url("https://youtube.com/channel/UCHnyfMqiRRG1u-2MsSQLbXA")
        == "YouTube Channel: UCHnyfMqiRRG1u-2MsSQ"
    )

    # Short youtube.com URLs
    assert extract_name_from_url("https://youtube.com/c/mkbhd") == "YouTube Channel"
    assert extract_name_from_url("https://youtu.be/watch?v=abc123") == "YouTube Channel"


def test_extract_name_edge_cases() -> None:
    """Test name extraction with edge cases."""
    # No protocol
    assert extract_name_from_url("example.com") == "Example"

    # Single word domains
    assert extract_name_from_url("localhost") == "localhost"

    # IP addresses
    assert extract_name_from_url("192.168.1.1") == "192"

    # With query parameters
    assert extract_name_from_url("https://example.com/feed?format=rss") == "Example"
    assert (
        extract_name_from_url("reddit://rust?sort=hot") == "rust"
    )  # Stripped prefix becomes just 'rust'
