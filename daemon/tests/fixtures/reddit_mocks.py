"""Mock factory functions for PRAW submission objects in tests."""

from unittest.mock import Mock  # claudex-guard: allow-mock
from typing import Optional, Dict, Any


def create_base_submission_mock(**overrides) -> Mock:
    """Create base submission mock with common attributes.

    Args:
        **overrides: Override default values with custom ones

    Returns:
        Mock submission object with PRAW-like attributes
    """
    defaults = {
        "permalink": "/r/python/comments/123/test_title/",
        "title": "Test Post Title",
        "is_self": True,
        "url": "https://reddit.com/r/python/comments/123/test_title/",
        "created_utc": 1640995200.0,  # Jan 1, 2022
        "selftext": "",
        "score": 10,
        "upvote_ratio": 0.8,
        "num_comments": 5,
    }

    # Apply overrides
    config = {**defaults, **overrides}

    # Create mock with attributes
    submission = Mock()  # claudex-guard: allow-mock
    for key, value in config.items():
        setattr(submission, key, value)

    # Create mock subreddit object
    if "subreddit" not in overrides:
        subreddit_mock = Mock()  # claudex-guard: allow-mock
        subreddit_mock.__str__ = lambda: "python"
        submission.subreddit = subreddit_mock

    # Create mock author object
    if "author" not in overrides:
        author_mock = Mock()  # claudex-guard: allow-mock
        author_mock.__str__ = lambda: "test_user"
        submission.author = author_mock

    return submission


def create_self_post_mock(**overrides) -> Mock:
    """Create text post mock with selftext content."""
    defaults = {
        "is_self": True,
        "selftext": "This is the post body content.",
        "url": "https://reddit.com/r/python/comments/123/test_title/",
    }
    return create_base_submission_mock(**{**defaults, **overrides})


def create_link_post_mock(**overrides) -> Mock:
    """Create link post mock to external URL."""
    defaults = {
        "is_self": False,
        "selftext": "",  # Link posts usually have empty selftext
        "url": "https://example.com/external-article",
    }
    return create_base_submission_mock(**{**defaults, **overrides})


def create_deleted_post_mock(**overrides) -> Mock:
    """Create deleted/removed post mock."""
    defaults = {
        "is_self": True,
        "selftext": "[deleted]",
        "score": 0,
        "upvote_ratio": 0.5,
        "num_comments": 0,
        "author": None,  # Deleted author
    }
    return create_base_submission_mock(**{**defaults, **overrides})


def create_image_post_mock(domain: str = "i.redd.it", **overrides) -> Mock:
    """Create image/video post mock."""
    image_urls = {
        "i.redd.it": "https://i.redd.it/abc123.jpg",
        "i.imgur.com": "https://i.imgur.com/def456.png",
        "imgur.com": "https://imgur.com/ghi789",
        "youtube.com": "https://youtube.com/watch?v=abc123",
        "v.redd.it": "https://v.redd.it/video123",
    }

    defaults = {
        "is_self": False,
        "selftext": "",
        "url": image_urls.get(domain, f"https://{domain}/example.jpg"),
    }
    return create_base_submission_mock(**{**defaults, **overrides})


def create_submission_with_missing_fields(**overrides) -> Mock:
    """Create submission mock with missing fields to test getattr defaults."""
    submission = Mock()  # claudex-guard: allow-mock

    # Only set basic required fields
    submission.permalink = "/r/test/comments/999/minimal/"
    submission.title = "Minimal Post"
    submission.is_self = True
    submission.url = "https://reddit.com/r/test/comments/999/minimal/"
    submission.created_utc = 1640995200.0

    # Explicitly remove other attributes to simulate missing fields
    # This tests the getattr() fallbacks in _extract_metrics()

    return submission
