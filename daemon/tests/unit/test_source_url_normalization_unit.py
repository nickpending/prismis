"""Unit tests for source URL normalization.

INVARIANT: Special protocol URLs must be normalized to real URLs.
BREAKS: Fetchers expect real URLs, not protocol URLs.

This is the deterministic half of the invariant that
test_api_integration.py::test_url_normalization proves end-to-end. That test goes through
POST /api/sources, which runs SourceValidator against live third-party endpoints, so it
cannot run in CI (gh #59, gh #60). The mapping itself is a pure function and is proved here.
"""

import pytest

from prismis_daemon.api import normalize_source_url


@pytest.mark.parametrize(
    ("input_url", "source_type", "expected"),
    [
        ("reddit://rust", "reddit", "https://www.reddit.com/r/rust"),
        ("reddit://python", "reddit", "https://www.reddit.com/r/python"),
        (
            "youtube://UC_x5XG1OV2P6uZZ5FSM9Ttw",
            "youtube",
            "https://www.youtube.com/channel/UC_x5XG1OV2P6uZZ5FSM9Ttw",
        ),
        ("youtube://@fireship", "youtube", "https://www.youtube.com/@fireship"),
        # Already-real URLs pass through untouched.
        (
            "https://simonwillison.net/atom/everything/",
            "rss",
            "https://simonwillison.net/atom/everything/",
        ),
        (
            "https://www.reddit.com/r/rust",
            "reddit",
            "https://www.reddit.com/r/rust",
        ),
    ],
)
def test_normalize_source_url(input_url: str, source_type: str, expected: str) -> None:
    assert normalize_source_url(input_url, source_type) == expected
