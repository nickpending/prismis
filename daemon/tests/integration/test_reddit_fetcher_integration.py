"""Integration tests for RedditFetcher against recorded Reddit API answers.

Reddit's OAuth handshake, its listing responses and the outbound link pages the fetcher
extracts are replayed from a vcrpy cassette (`http_cassette` fixture, conftest.py) under
`no_network`, recorded once from the real service with the credentials of the host that
holds them. The cassette keeps no credential: the Authorization and Set-Cookie headers
are dropped and the token the handshake returned is replaced. Re-record with
PRISMIS_RECORD_HTTP=1 (see docs/architecture/boundaries.md).
"""

import json

import pytest
from conftest import HttpCassette, make_config
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.models import ContentItem

pytestmark = pytest.mark.usefixtures("reddit_credentials", "http_cassette")

# The recorded posts age every day the cassette is kept, and the fetcher drops posts older
# than the lookback, so the lookback is far wider than the recording will ever be old.
# What is under test is fetching, filtering and shaping, not the cutoff.
RECORDED_LOOKBACK_DAYS = 36500
IMAGE_DOMAINS = (
    "i.redd.it",
    "i.imgur.com",
    "imgur.com",
    "gfycat.com",
    "v.redd.it",
    "youtube.com",
    "youtu.be",
    "streamable.com",
)
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm")


def test_fetch_reddit_with_real_api() -> None:
    """Test complete Reddit fetching workflow with real API.

    This test:
    - Uses real Reddit API via PRAW
    - Fetches actual posts from a subreddit
    - Filters out image posts
    - Returns proper ContentItem objects
    """
    config = make_config(max_days_lookback=RECORDED_LOOKBACK_DAYS)
    fetcher = RedditFetcher(max_items=3, config=config)

    # Use a stable subreddit for testing
    source = {"url": "https://reddit.com/r/python", "id": "test-source-123"}

    # Fetch content - the recorded API answers
    items = fetcher.fetch_content(source)

    # Verify we got items back
    assert len(items) > 0
    assert len(items) <= 3  # Should respect max_items

    # Verify first item has all required fields
    first_item = items[0]
    assert isinstance(first_item, ContentItem)
    assert first_item.source_id == "test-source-123"
    assert first_item.external_id is not None
    assert first_item.external_id.startswith("https://reddit.com/r/")
    assert first_item.title is not None
    assert len(first_item.title) > 0
    assert first_item.url is not None
    assert first_item.url.startswith("https://reddit.com")

    # Verify content was extracted
    assert first_item.content is not None
    assert len(first_item.content) > 0

    # Verify metrics were extracted
    assert first_item.analysis is not None
    assert "metrics" in first_item.analysis
    metrics = first_item.analysis["metrics"]
    assert "score" in metrics
    assert "upvote_ratio" in metrics
    assert "num_comments" in metrics
    assert "author" in metrics
    assert "subreddit" in metrics

    # Verify fetched_at was set
    assert first_item.fetched_at is not None

    # Verify consistent external IDs (no duplicates)
    external_ids = [item.external_id for item in items]
    assert len(external_ids) == len(set(external_ids))


def test_fetch_reddit_handles_invalid_subreddit() -> None:
    """Test fetcher handles invalid subreddit gracefully."""
    config = make_config(max_days_lookback=RECORDED_LOOKBACK_DAYS)
    fetcher = RedditFetcher(config=config)

    # Try to fetch from non-existent subreddit
    source = {
        "url": "https://reddit.com/r/thisubdoesnotexist123456789",
        "id": "test-id",
    }

    with pytest.raises(Exception) as exc_info:
        fetcher.fetch_content(source)

    # Should wrap error with context
    assert "Failed to fetch Reddit content" in str(exc_info.value)


def test_fetch_reddit_respects_max_items() -> None:
    """Test fetcher respects max_items configuration."""
    config = make_config(max_days_lookback=RECORDED_LOOKBACK_DAYS)
    fetcher = RedditFetcher(max_items=1, config=config)

    source = {"url": "r/python", "id": "test-id"}

    items = fetcher.fetch_content(source)
    assert len(items) <= 1


def _listing_in_order(http_cassette: HttpCassette) -> list[tuple[str, bool]]:
    """The cassette's subreddit listing as (external id, is an image/video post)."""
    posts: list[tuple[str, bool]] = []
    for _request, response in http_cassette.cassette.data:
        try:
            listing = json.loads(response["body"]["string"])
        except ValueError:
            continue
        if not isinstance(listing, dict):
            continue
        for child in listing.get("data", {}).get("children", []):
            post = child["data"]
            url = str(post.get("url", "")).lower()
            is_image = not post.get("is_self") and (
                any(d in url for d in IMAGE_DOMAINS) or url.endswith(IMAGE_EXTENSIONS)
            )
            posts.append((f"https://reddit.com{post['permalink']}", is_image))
    return posts


def test_fetch_reddit_filters_image_posts(http_cassette: HttpCassette) -> None:
    """Test that image posts are filtered out.

    The fetcher stops walking the listing once it has `max_items` posts, so only image
    posts listed before its last kept post were ever its to drop. The recorded listing
    must hold such a post, or the assertion below would pass with the filter deleted.
    """
    config = make_config(max_days_lookback=RECORDED_LOOKBACK_DAYS)
    fetcher = RedditFetcher(max_items=10, config=config)

    # A subreddit that mixes text, link and image posts
    source = {"url": "r/linux", "id": "test-id"}

    items = fetcher.fetch_content(source)

    assert items, "the fetcher kept nothing from the recorded listing"
    kept = {item.external_id for item in items}
    listing = _listing_in_order(http_cassette)
    last_kept = max(i for i, (post_id, _img) in enumerate(listing) if post_id in kept)
    dropped_images = {
        post_id for post_id, is_image in listing[:last_kept] if is_image
    }
    assert dropped_images, (
        "no image post precedes the last kept post in the recorded listing, so the "
        "filter had nothing to drop"
    )
    assert not kept & dropped_images, (
        f"image posts were not filtered: {kept & dropped_images}"
    )


def test_fetch_reddit_handles_various_url_formats() -> None:
    """Test that various Reddit URL formats are parsed correctly."""
    config = make_config(max_days_lookback=RECORDED_LOOKBACK_DAYS)
    fetcher = RedditFetcher(max_items=1, config=config)

    url_formats = ["https://reddit.com/r/python", "r/python", "python"]

    for url in url_formats:
        source = {"url": url, "id": "test-id"}

        items = fetcher.fetch_content(source)
        assert len(items) > 0, f"Failed to fetch from URL format: {url}"
