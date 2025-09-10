"""Unit tests for RedditFetcher logic functions."""

from datetime import datetime
from unittest.mock import Mock

from fetchers.reddit import RedditFetcher
from models import ContentItem
from tests.fixtures.reddit_mocks import (
    create_self_post_mock,
    create_link_post_mock,
    create_deleted_post_mock,
    create_image_post_mock,
    create_submission_with_missing_fields,
)


def test_parse_subreddit_name_full_url() -> None:
    """Test subreddit parsing from full Reddit URLs."""
    fetcher = RedditFetcher()

    # Test various full URL formats
    url = "https://reddit.com/r/python"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "python"

    url = "https://www.reddit.com/r/MachineLearning"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "MachineLearning"

    url = "http://reddit.com/r/programming"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "programming"


def test_parse_subreddit_name_short_formats() -> None:
    """Test subreddit parsing from short formats."""
    fetcher = RedditFetcher()

    # Test r/subreddit format
    url = "r/python"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "python"

    # Test just subreddit name
    url = "python"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "python"

    # Test with underscores and numbers
    url = "r/test_sub123"
    subreddit = fetcher._parse_subreddit_name(url)
    assert subreddit == "test_sub123"


def test_parse_subreddit_name_invalid_formats() -> None:
    """Test subreddit parsing handles invalid formats."""
    fetcher = RedditFetcher()

    # Test empty string
    subreddit = fetcher._parse_subreddit_name("")
    assert subreddit == ""

    # Test invalid URL
    subreddit = fetcher._parse_subreddit_name("https://example.com/not-reddit")
    assert subreddit == ""

    # Test malformed reddit URL
    subreddit = fetcher._parse_subreddit_name("reddit.com/not-a-subreddit")
    assert subreddit == ""


def test_is_image_post_self_posts() -> None:
    """Test image detection correctly identifies self posts as text."""
    fetcher = RedditFetcher()

    # Mock self post
    submission = Mock()
    submission.is_self = True
    submission.url = "https://reddit.com/r/python/comments/123/title"

    result = fetcher._is_image_post(submission)
    assert result is False


def test_is_image_post_image_domains() -> None:
    """Test image detection identifies common image hosting domains."""
    fetcher = RedditFetcher()

    # Mock submission with image domains
    submission = Mock()
    submission.is_self = False

    image_urls = [
        "https://i.redd.it/abc123.jpg",
        "https://i.imgur.com/def456.png",
        "https://imgur.com/ghi789",
        "https://gfycat.com/example",
        "https://v.redd.it/video123",
        "https://youtube.com/watch?v=abc",
        "https://youtu.be/def123",
        "https://streamable.com/example",
    ]

    for url in image_urls:
        submission.url = url
        result = fetcher._is_image_post(submission)
        assert result is True, f"Should detect {url} as image/video"


def test_is_image_post_file_extensions() -> None:
    """Test image detection identifies image file extensions."""
    fetcher = RedditFetcher()

    submission = Mock()
    submission.is_self = False

    image_extensions = [
        "https://example.com/image.jpg",
        "https://example.com/image.jpeg",
        "https://example.com/image.png",
        "https://example.com/image.gif",
        "https://example.com/image.webp",
        "https://example.com/video.mp4",
        "https://example.com/video.webm",
    ]

    for url in image_extensions:
        submission.url = url
        result = fetcher._is_image_post(submission)
        assert result is True, f"Should detect {url} as image/video"


def test_is_image_post_text_links() -> None:
    """Test image detection correctly identifies text/article links."""
    fetcher = RedditFetcher()

    submission = Mock()
    submission.is_self = False

    text_urls = [
        "https://github.com/python/cpython",
        "https://docs.python.org/3/tutorial/",
        "https://news.ycombinator.com/item?id=123",
        "https://medium.com/article-title",
        "https://stackoverflow.com/questions/123",
    ]

    for url in text_urls:
        submission.url = url
        result = fetcher._is_image_post(submission)
        assert result is False, f"Should not detect {url} as image/video"


def test_extract_metrics_all_fields_present() -> None:
    """Test metrics extraction with all fields available."""
    fetcher = RedditFetcher()

    # Mock submission with all fields
    submission = Mock()
    submission.score = 42
    submission.upvote_ratio = 0.85
    submission.num_comments = 15
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "python"
    submission.author = Mock()
    submission.author.__str__ = lambda self: "test_user"

    metrics = fetcher._extract_metrics(submission)

    assert metrics["score"] == 42
    assert metrics["upvote_ratio"] == 0.85
    assert metrics["num_comments"] == 15
    assert metrics["subreddit"] == "python"
    assert metrics["author"] == "test_user"


def test_extract_metrics_missing_fields() -> None:
    """Test metrics extraction handles missing fields gracefully."""
    fetcher = RedditFetcher()

    # Mock submission with missing fields
    submission = Mock()
    # Remove attributes to simulate missing fields
    del submission.score
    del submission.upvote_ratio
    del submission.num_comments
    submission.author = None

    metrics = fetcher._extract_metrics(submission)

    assert metrics["score"] == 0  # Default value
    assert metrics["upvote_ratio"] == 0.0  # Default value
    assert metrics["num_comments"] == 0  # Default value
    assert metrics["author"] == "[deleted]"


def test_to_content_item_self_post() -> None:
    """Test ContentItem conversion for self posts with text."""
    fetcher = RedditFetcher()

    # Mock self post submission
    submission = Mock()
    submission.permalink = "/r/python/comments/123/test_title/"
    submission.title = "How to learn Python?"
    submission.is_self = True
    submission.selftext = (
        "I'm new to programming and want to learn Python. Any recommendations?"
    )
    submission.url = "https://reddit.com/r/python/comments/123/test_title/"
    submission.created_utc = 1640995200  # Jan 1, 2022
    submission.score = 25
    submission.upvote_ratio = 0.9
    submission.num_comments = 5
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "python"
    submission.author = Mock()
    submission.author.__str__ = lambda self: "learner123"

    item = fetcher._to_content_item(submission, "test-source-id")

    assert isinstance(item, ContentItem)
    assert item.external_id == "https://reddit.com/r/python/comments/123/test_title/"
    assert item.title == "How to learn Python?"
    assert item.url == "https://reddit.com/r/python/comments/123/test_title/"
    assert (
        item.content
        == "I'm new to programming and want to learn Python. Any recommendations?"
    )
    assert item.source_id == "test-source-id"
    # Check timestamp is converted correctly (account for timezone)
    assert item.published_at.year == 2021 or item.published_at.year == 2022
    assert "metrics" in item.analysis
    assert item.analysis["metrics"]["score"] == 25


def test_to_content_item_link_post() -> None:
    """Test ContentItem conversion for link posts."""
    fetcher = RedditFetcher()

    # Mock link post submission
    submission = Mock()
    submission.permalink = "/r/programming/comments/456/cool_article/"
    submission.title = "Cool Programming Article"
    submission.is_self = False
    submission.url = "https://example.com/programming-article"
    submission.selftext = ""
    submission.created_utc = 1640995200
    submission.score = 100
    submission.upvote_ratio = 0.95
    submission.num_comments = 20
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "programming"
    submission.author = Mock()
    submission.author.__str__ = lambda self: "developer456"

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.title == "Cool Programming Article"
    assert item.content == "Link: https://example.com/programming-article\n\n"
    assert item.url == "https://reddit.com/r/programming/comments/456/cool_article/"
    assert item.analysis["metrics"]["score"] == 100


def test_to_content_item_deleted_content() -> None:
    """Test ContentItem conversion handles deleted/removed content."""
    fetcher = RedditFetcher()

    # Mock submission with deleted content
    submission = Mock()
    submission.permalink = "/r/test/comments/789/deleted/"
    submission.title = "Deleted Post"
    submission.is_self = True
    submission.selftext = "[deleted]"
    submission.url = "https://example.com/external-link"
    submission.created_utc = 1640995200
    submission.score = 0
    submission.upvote_ratio = 0.5
    submission.num_comments = 0
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "test"
    submission.author = None

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.content == "Link post to: https://example.com/external-link"
    assert item.analysis["metrics"]["author"] == "[deleted]"


def test_to_content_item_date_parsing_error() -> None:
    """Test ContentItem conversion handles date parsing errors gracefully."""
    fetcher = RedditFetcher()

    # Mock submission with invalid timestamp
    submission = Mock()
    submission.permalink = "/r/test/comments/999/no_date/"
    submission.title = "Post Without Date"
    submission.is_self = True
    submission.selftext = "Content here"
    submission.url = "https://reddit.com/r/test/comments/999/no_date/"
    # Invalid timestamp that will cause datetime.fromtimestamp to fail
    submission.created_utc = "invalid"
    submission.score = 1
    submission.upvote_ratio = 0.6
    submission.num_comments = 1
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "test"
    submission.author = Mock()
    submission.author.__str__ = lambda self: "user123"

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.published_at is None  # Should handle error gracefully
    assert item.content == "Content here"  # Other fields should still work
