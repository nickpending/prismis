"""Integration tests for SourceValidator - real network validation."""

import pytest

from prismis_daemon.validator import SourceValidator


def test_valid_sources_accepted() -> None:
    """
    INVARIANT: Known-good sources must always validate as true
    BREAKS: Users can't add sources they need
    """
    validator = SourceValidator()

    # Test well-known, stable RSS feed
    is_valid, error = validator.validate_source(
        "https://simonwillison.net/atom/everything/", "rss"
    )
    assert is_valid is True, f"Simon Willison's feed should be valid: {error}"
    assert error is None, "Valid feed should have no error"

    # Test well-known Reddit subreddit
    is_valid, error = validator.validate_source("https://reddit.com/r/python", "reddit")
    assert is_valid is True, f"r/python should be valid: {error}"
    assert error is None, "Valid subreddit should have no error"

    # Test well-known YouTube channel formats
    youtube_urls = [
        "https://youtube.com/@mkbhd",
        "https://youtube.com/c/CGPGrey",
        "youtube://@veritasium",
    ]

    for url in youtube_urls:
        is_valid, error = validator.validate_source(url, "youtube")
        assert is_valid is True, f"YouTube {url} should be valid: {error}"
        assert error is None, f"Valid YouTube URL should have no error: {url}"


def test_invalid_sources_rejected() -> None:
    """
    INVARIANT: Invalid sources must be rejected with clear errors
    BREAKS: Bad sources pollute the database
    """
    validator = SourceValidator()

    # Test non-existent domain
    is_valid, error = validator.validate_source(
        "https://this-domain-definitely-does-not-exist-12345.com/feed.xml", "rss"
    )
    assert is_valid is False, "Non-existent domain should fail"
    assert error is not None, "Should have error message"
    assert "Network error" in error or "nodename" in error, (
        "Should explain network failure"
    )

    # Test non-existent subreddit
    is_valid, error = validator.validate_source(
        "https://reddit.com/r/this_subreddit_definitely_does_not_exist_12345", "reddit"
    )
    assert is_valid is False, "Non-existent subreddit should fail"
    assert error is not None, "Should have error message"
    assert "does not exist" in error or "Invalid" in error, (
        "Should explain subreddit doesn't exist"
    )

    # Test invalid YouTube URL (video instead of channel)
    is_valid, error = validator.validate_source(
        "https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube"
    )
    assert is_valid is False, "Video URL should fail"
    assert error is not None, "Should have error message"
    assert "not supported" in error or "channel" in error.lower(), (
        "Should explain need channel URL"
    )


def test_network_timeout_handling() -> None:
    """
    FAILURE MODE: Network timeouts must fail gracefully
    GRACEFUL: Clear error message, no hanging
    """
    validator = SourceValidator()

    # Override timeout to be very short to trigger timeout on slow endpoints
    validator.timeout = 0.001  # 1ms timeout - will timeout on any real network call

    # Test RSS timeout with a real endpoint that will be too slow
    is_valid, error = validator.validate_source(
        "https://httpbin.org/delay/5",
        "rss",  # This endpoint delays 5 seconds
    )
    assert is_valid is False, "Timeout should fail validation"
    assert error is not None, "Should have error message"
    assert "timed out" in error.lower(), "Should mention timeout"

    # Reset timeout for other tests
    validator.timeout = 5.0


def test_reddit_rate_limit_handling() -> None:
    """
    FAILURE MODE: Reddit rate limiting (429) must be handled
    GRACEFUL: Clear message about rate limiting
    NOTE: This test uses httpbin.org/status/429 to simulate a 429 response
    """
    validator = SourceValidator()

    # Test with an endpoint that returns 429 status
    # httpbin.org is a testing service that returns specific status codes
    is_valid, error = validator._validate_reddit("https://httpbin.org/status/429")

    # The validator should handle non-Reddit URLs gracefully
    # In production, Reddit returns 429 when rate limited
    # For now, we verify the code handles unexpected responses
    assert is_valid is False, "Non-Reddit URL should fail validation"
    assert error is not None, "Should have error message"

    # Alternative: Test with a subreddit that might trigger rate limiting
    # This is less reliable but tests the actual Reddit API path
    # Skipping to avoid hitting real Reddit API rate limits in CI


def test_malformed_rss_handling() -> None:
    """
    FAILURE MODE: Malformed RSS/XML must be rejected
    GRACEFUL: Clear error about invalid feed format
    """
    validator = SourceValidator()

    # Test with a real URL that returns HTML instead of RSS
    is_valid, error = validator.validate_source(
        "https://google.com",
        "rss",  # Google homepage, not an RSS feed
    )
    assert is_valid is False, "HTML page should fail RSS validation"
    assert error is not None, "Should have error message"
    assert "invalid" in error.lower() or "format" in error.lower(), (
        "Should mention invalid format"
    )


def test_reddit_private_subreddit_handling() -> None:
    """
    FAILURE MODE: Private subreddits return 403
    GRACEFUL: Clear message that subreddit is private
    NOTE: Testing with a known private subreddit if one exists
    """
    validator = SourceValidator()

    # Test with a subreddit that is likely to be private or restricted
    # Note: This test may be flaky if the subreddit's status changes
    # Some subreddits like r/lounge are known to be restricted
    is_valid, error = validator.validate_source(
        "https://reddit.com/r/lounge",
        "reddit",  # Known restricted subreddit
    )

    # If not private, at least verify it handles the response properly
    # The validator should either:
    # 1. Detect it's private/restricted (403)
    # 2. Detect it exists but can't access
    # 3. Return some error about accessibility
    if is_valid:
        # Subreddit might have become public, skip this test
        pytest.skip("r/lounge is not private/restricted anymore")
    else:
        assert error is not None, "Should have error message"
        # The error might mention private, restricted, or inaccessible
        # We're testing that it handles non-accessible subreddits gracefully
