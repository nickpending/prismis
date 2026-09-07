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
# The daemon half of the CLI/daemon normalization divergence (gh #65)
#
# The CLI half lives in cli/tests/unit/test_source_command_unit.py — the
# test_pl_prefix_is_treated_as_a_channel_id, test_protocol_url_trailing_slash_is_not_stripped
# and test_leading_whitespace_defeats_scheme_detection cases there. Pinning only that side
# documented the divergence without guarding it: the daemon could start matching PL and no
# test would fail. These assert what the daemon produces TODAY, so a change on either side
# turns something red. Neither implementation is changed here — which side is right is
# gh #65.
# ---------------------------------------------------------------------------


def test_daemon_matches_only_a_uc_channel_prefix() -> None:
    """CLI maps PL to /channel/; the daemon matches only UC and falls through to /@."""
    assert (
        normalize_source_url("youtube://PLabc123", "youtube")
        == "https://www.youtube.com/@PLabc123"
    )
    # UC is where the two agree.
    assert (
        normalize_source_url("youtube://UCabc123", "youtube")
        == "https://www.youtube.com/channel/UCabc123"
    )


def test_daemon_strips_a_protocol_url_trailing_slash() -> None:
    """CLI keeps the slash on a protocol URL; the daemon strips it."""
    assert (
        normalize_source_url("reddit://rust/", "reddit")
        == "https://www.reddit.com/r/rust"
    )


def test_daemon_strips_leading_whitespace_before_scheme_detection() -> None:
    """A leading space defeats the CLI's scheme detection; the daemon strips first."""
    assert (
        normalize_source_url(" reddit://rust", "reddit")
        == "https://www.reddit.com/r/rust"
    )
