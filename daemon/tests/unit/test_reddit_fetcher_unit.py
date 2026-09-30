"""Unit tests for RedditFetcher logic functions."""

import http.server
import threading
from collections.abc import Iterator
from unittest.mock import Mock

import pytest

from prismis_daemon.config import REDDIT_NOT_CONFIGURED
from prismis_daemon.fetchers.reddit import RedditFetcher, RedditNotConfiguredError
from prismis_daemon.http_deadline import DeadlineAdapter
from prismis_daemon.models import ContentItem

from conftest import make_config

_ARTICLE_HTML = b"""<!doctype html>
<html><head><title>An External Article</title></head>
<body><article>
<h1>An External Article</h1>
<p>This is a genuine article body, long enough for trafilatura's extraction
heuristics to treat it as the main content rather than boilerplate chrome.</p>
<p>A second paragraph adds enough additional real prose that the extracted text is
unambiguously the article, not a stub.</p>
</article></body></html>
"""


@pytest.fixture
def link_post_article_server() -> Iterator[str]:
    """A local server standing in for the external site a Reddit link post
    points at (Principle I: real HTTP boundary, local server, not a mock)."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/article":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(_ARTICLE_HTML)))
                self.end_headers()
                self.wfile.write(_ARTICLE_HTML)
            else:
                self.send_error(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


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
    assert item.published_at is not None
    assert item.published_at.year == 2021 or item.published_at.year == 2022
    assert item.analysis is not None
    assert "metrics" in item.analysis
    assert item.analysis["metrics"]["score"] == 25


def _link_post_submission(url: str) -> Mock:
    submission = Mock()
    submission.permalink = "/r/programming/comments/456/cool_article/"
    submission.title = "Cool Programming Article"
    submission.is_self = False
    submission.url = url
    submission.selftext = ""
    submission.created_utc = 1640995200
    submission.score = 100
    submission.upvote_ratio = 0.95
    submission.num_comments = 20
    submission.subreddit = Mock()
    submission.subreddit.__str__ = lambda self: "programming"
    submission.author = Mock()
    submission.author.__str__ = lambda self: "developer456"
    return submission


def test_to_content_item_link_post_falls_back_to_link_only_on_failed_extraction(
    no_network: None,
) -> None:
    """
    SC-2: a failed extraction (here, every outbound request is routed at a proxy
    port nothing listens on) falls back to today's link-only content rather than
    dropping the item.
    """
    fetcher = RedditFetcher()
    submission = _link_post_submission("https://example.com/programming-article")

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.title == "Cool Programming Article"
    assert item.content == "Link: https://example.com/programming-article\n\n"
    assert item.url == "https://reddit.com/r/programming/comments/456/cool_article/"
    assert item.analysis is not None
    assert item.analysis["metrics"]["score"] == 100


def test_to_content_item_link_post_fetches_the_external_article(
    link_post_article_server: str,
) -> None:
    """
    SC-2: the external article's text becomes part of the item's content
    alongside the link, fetched with the same article extractor the RSS fetcher
    uses.
    BREAKS: A helper that never actually calls the extractor leaves the item's
    content at "Link: <url>" even when the article was fetchable.
    """
    fetcher = RedditFetcher()
    submission = _link_post_submission(f"{link_post_article_server}/article")

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.content is not None
    assert item.content.startswith(f"Link: {link_post_article_server}/article")
    assert "genuine article body" in item.content


def test_to_content_item_link_post_skips_fetch_for_reddit_internal_link() -> None:
    """
    SC-2: a link post to another reddit page behaves as it does today -- no
    article fetch attempted, content stays link-only.
    """
    fetcher = RedditFetcher()
    submission = _link_post_submission(
        "https://www.reddit.com/r/other/comments/999/crosspost/"
    )

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.content == (
        "Link: https://www.reddit.com/r/other/comments/999/crosspost/\n\n"
    )


def test_to_content_item_link_post_skips_fetch_for_image_link() -> None:
    """SC-2: link posts to images behave as they do today -- no fetch attempted."""
    fetcher = RedditFetcher()
    submission = _link_post_submission("https://i.redd.it/abc123.jpg")

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.content == "Link: https://i.redd.it/abc123.jpg\n\n"


class _TrackedComments:
    """Records whether comment replacement was ever attempted, without the
    swallow-everything try/except in `_fetch_comments` hiding the answer."""

    def __init__(self) -> None:
        self.accessed = False

    def replace_more(self, limit: int | None = None) -> None:
        self.accessed = True

    def list(self) -> list:
        return []


def test_to_content_item_link_post_skip_known_readable_skips_article_and_comments(
    link_post_article_server: str,
) -> None:
    """
    SC-4: a post already stored readably gets no article fetch and no comment
    read -- the orchestrator's dedup filter drops the result either way.
    BREAKS: Threading known_readable_ids through only the article-fetch branch
    (and not `_fetch_comments`) still reads comments for an item about to be
    discarded, on every single fetch cycle it stays title-only.
    """
    fetcher = RedditFetcher()
    submission = _link_post_submission(f"{link_post_article_server}/article")
    tracked_comments = _TrackedComments()
    submission.comments = tracked_comments
    external_id = "https://reddit.com/r/programming/comments/456/cool_article/"

    item = fetcher._to_content_item(
        submission, "test-source-id", known_readable_ids={external_id}
    )

    assert item.content == f"Link: {link_post_article_server}/article\n\n"
    assert tracked_comments.accessed is False, (
        "SC-4: comments must not be read for a post already stored readably"
    )


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
    assert item.analysis is not None
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


@pytest.mark.parametrize(
    "overrides",
    [
        {"reddit_client_id": "env:REDDIT_CLIENT_ID"},
        {"reddit_client_secret": "env:REDDIT_CLIENT_SECRET"},
        {"reddit_client_id": ""},
    ],
    ids=["placeholder-id", "placeholder-secret", "empty-id"],
)
def test_fetch_without_credentials_reports_them_absent(
    overrides: dict[str, str], no_network: None
) -> None:
    """
    INVARIANT: A fetch with unusable credentials raises "not configured" before any request
    BREAKS: PRAW accepts the env: placeholder, so every cycle reports Reddit's 401 and an
            install with no credentials is told its credentials are invalid (#68)
    """
    fields = {
        "reddit_client_id": "zzclientidzz-8f3a1c",
        "reddit_client_secret": "zzclientsecretzz-4b7e92",
    }
    config = make_config(**(fields | overrides))
    fetcher = RedditFetcher(config=config)

    with pytest.raises(RedditNotConfiguredError) as raised:
        fetcher.fetch_content(
            {"url": "https://www.reddit.com/r/python", "id": "source-1"}
        )

    assert str(raised.value) == REDDIT_NOT_CONFIGURED


class _FakeRedditClient:
    """A hand-written stand-in for praw.Reddit's `.submission()` call -- PRAW's
    network is the boundary refetch_one crosses (job 2's stake), not
    prismis_daemon's own code, so only that call is faked; everything else
    `refetch_one` touches (the real DeadlineAdapter below, `_to_content_item`)
    stays real. Records every call so the test proves the real argument, not
    just that *a* call happened."""

    def __init__(self, submission) -> None:
        self._submission = submission
        self.calls: list[str] = []

    def submission(self, url: str):
        self.calls.append(url)
        return self._submission


def test_refetch_one_rebuilds_the_item_via_praw_submission(
    link_post_article_server: str,
) -> None:
    """
    refetch-unreadable SC-2: refetch_one fetches the submission by permalink
    via PRAW (`reddit.submission(url=...)`) and rebuilds the item through the
    same `_to_content_item` path the bulk fetch uses, so a stored link post
    that's still unreadable gets a fresh article fetch.
    BREAKS: A refetch path that never calls `_to_content_item` (or calls it
    with `known_readable_ids` still containing this item) leaves the rebuilt
    item at today's link-only placeholder instead of the article text.
    """
    fetcher = RedditFetcher()
    fetcher.credentials_missing = False
    fetcher.fetch_budget = 60.0
    # A real DeadlineAdapter, not a mock -- refetch_one's own rearm() call is
    # exercised for real; only PRAW's client below is a hand-written stand-in.
    fetcher._deadline = DeadlineAdapter(60.0)

    submission = _link_post_submission(f"{link_post_article_server}/article")
    fake_reddit = _FakeRedditClient(submission)
    fetcher.reddit = fake_reddit

    external_id = "https://reddit.com/r/programming/comments/456/cool_article/"
    item = fetcher.refetch_one(external_id, "test-source-id")

    assert fake_reddit.calls == [external_id]
    assert item.external_id == external_id
    assert item.source_id == "test-source-id"
    assert item.content is not None
    assert "genuine article body" in item.content


def test_refetch_one_raises_when_reddit_not_configured() -> None:
    """
    refetch-unreadable SC-2: a refetch attempt with unusable Reddit
    credentials raises RedditNotConfiguredError rather than returning a
    placeholder item -- the caller (refetch.py) records this as a failed
    item instead of silently patching title_only onto one that was never
    actually re-extracted.
    """
    fields = {
        "reddit_client_id": "env:REDDIT_CLIENT_ID",
        "reddit_client_secret": "env:REDDIT_CLIENT_SECRET",
    }
    config = make_config(**fields)
    fetcher = RedditFetcher(config=config)

    with pytest.raises(RedditNotConfiguredError) as raised:
        fetcher.refetch_one(
            "https://reddit.com/r/test/comments/1/x/", "test-source-id"
        )

    assert str(raised.value) == REDDIT_NOT_CONFIGURED
