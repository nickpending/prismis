"""Unit tests for source URL normalization.

INVARIANT: Special protocol URLs must be normalized to real URLs.
BREAKS: Fetchers expect real URLs, not protocol URLs.

This is the deterministic half of the invariant that the tests named
test_url_normalization and test_url_normalization_reddit, in the API integration suite,
prove end to end. Those go through POST /api/sources, so they carry that path's
requirements: the rss row validates against a live third-party feed, and the reddit rows
probe Reddit's authenticated API and need credentials as well. Neither runs in CI. The
mapping itself is a pure function and is proved here.
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


# ---------------------------------------------------------------------------
# normalize_source_url is the only protocol-URL expansion (#65). Every client sends the
# URL as typed; these pin the inputs the CLI used to expand differently.
# ---------------------------------------------------------------------------


def test_a_pl_id_expands_to_a_playlist_not_a_channel() -> None:
    """A playlist id must not become a channel or handle URL that fetches nothing."""
    assert (
        normalize_source_url("youtube://PLabc123", "youtube")
        == "https://www.youtube.com/playlist?list=PLabc123"
    )
    assert (
        normalize_source_url("youtube://UCabc123", "youtube")
        == "https://www.youtube.com/channel/UCabc123"
    )


def test_daemon_strips_a_protocol_url_trailing_slash() -> None:
    """A trailing slash must not make reddit://rust/ a second source."""
    assert (
        normalize_source_url("reddit://rust/", "reddit")
        == "https://www.reddit.com/r/rust"
    )


def test_daemon_strips_leading_whitespace_before_scheme_detection() -> None:
    """Surrounding whitespace must not defeat scheme detection."""
    assert (
        normalize_source_url(" reddit://rust", "reddit")
        == "https://www.reddit.com/r/rust"
    )
