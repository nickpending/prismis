"""Unit tests for `prismis-cli source add` URL → source-type detection.

INVARIANT: Source type MUST be correctly identified from the URL, and protocol URLs MUST
expand to the same real URLs the daemon produces.
BREAKS: A misdetected type is fetched by the wrong fetcher and the source never yields
content; a URL the daemon normalizes differently forks the two.

This replaces the coverage deleted with tests/integration/test_source_validation.py and
tests/integration/test_source_commands.py. Those patched `cli.source.SourceValidator` and
`cli.source.VALIDATOR_AVAILABLE`, neither of which exists — the CLI no longer validates
locally or writes SQLite directly; the daemon owns validation. They also hit the live
network, which a CI-run gate rules out.
"""

import pytest

from cli.source import detect_and_normalize_source_url, resolve_source


@pytest.mark.parametrize(
    ("input_url", "expected_type", "expected_url"),
    [
        # Protocol URLs are expanded to real ones and typed from the scheme.
        ("reddit://rust", "reddit", "https://www.reddit.com/r/rust"),
        ("youtube://@mkbhd", "youtube", "https://www.youtube.com/@mkbhd"),
        (
            "youtube://UC9-y-6csu5WGm29I7JiwpnA",
            "youtube",
            "https://www.youtube.com/channel/UC9-y-6csu5WGm29I7JiwpnA",
        ),
        # Real URLs are typed from the host.
        ("https://reddit.com/r/rust", "reddit", "https://reddit.com/r/rust"),
        ("https://youtube.com/@mkbhd", "youtube", "https://youtube.com/@mkbhd"),
        # File sources are typed from the extension.
        ("https://example.com/CHANGELOG.md", "file", "https://example.com/CHANGELOG.md"),
        # Anything else is a feed.
        (
            "https://simonwillison.net/atom/everything/",
            "rss",
            "https://simonwillison.net/atom/everything/",
        ),
    ],
)
def test_source_type_detected_from_url(
    input_url: str, expected_type: str, expected_url: str
) -> None:
    source_type, url = detect_and_normalize_source_url(input_url)

    assert source_type == expected_type, (
        f"{input_url} must be detected as {expected_type}, got {source_type}"
    )
    assert url == expected_url, (
        f"{input_url} must normalize to {expected_url}, got {url}"
    )


def test_youtube_bare_handle_gets_an_at_prefix() -> None:
    """A youtube:// handle without '@' is still a handle, not a channel id."""
    assert detect_and_normalize_source_url("youtube://mkbhd") == (
        "youtube",
        "https://www.youtube.com/@mkbhd",
    )


def test_reddit_url_trailing_slash_is_stripped() -> None:
    """PRAW is handed the URL as-is, so the trailing slash must not survive."""
    assert detect_and_normalize_source_url("https://reddit.com/r/rust/") == (
        "reddit",
        "https://reddit.com/r/rust",
    )


# ---------------------------------------------------------------------------
# The name a user actually gets from `source add`
#
# `add` rebinds url from detect_and_normalize_source_url BEFORE calling
# extract_name_from_url, so production only ever derives a name from the NORMALIZED
# url. Testing the two halves separately missed that: test_url_extraction.py pins
# extract_name_from_url("reddit://rust") == "rust", but no user ever sees that name —
# `prismis-cli source add reddit://rust` names the source "r/rust".
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw_url", "expected_name"),
    [
        # NOT "rust" — normalization runs first and turns this into a reddit.com URL.
        ("reddit://rust", "r/rust"),
        ("youtube://@mkbhd", "@mkbhd"),
        ("https://simonwillison.net/atom/everything/", "Simonwillison"),
    ],
)
def test_name_derived_for_a_source_the_user_adds(
    raw_url: str, expected_name: str
) -> None:
    """Calls the production composition, not a copy of it.

    This test previously defined its own `_name_add_would_derive` calling the two helpers
    in the documented order, while `add` held a separate copy — so it asserted that the
    TEST behaved as documented, and reordering or dropping normalization in `add` left it
    green. That is the exact bug this test exists to catch.
    """
    _, _, name = resolve_source(raw_url)
    assert name == expected_name


def test_explicit_name_is_not_overwritten() -> None:
    """A user-supplied name wins; derivation only fills the gap."""
    source_type, url, name = resolve_source("reddit://rust", name="My Feed")
    assert (source_type, url, name) == (
        "reddit",
        "https://www.reddit.com/r/rust",
        "My Feed",
    )


# ---------------------------------------------------------------------------
# Divergence from the daemon's normalize_source_url (gh #65)
#
# Pinned so that either side changing shows up as a failing test rather than as two
# components silently disagreeing about the same URL.
# ---------------------------------------------------------------------------


def test_pl_prefix_is_treated_as_a_channel_id() -> None:
    """CLI produces /channel/PL...; the daemon matches only UC and produces /@PL...."""
    assert detect_and_normalize_source_url("youtube://PLabc123") == (
        "youtube",
        "https://www.youtube.com/channel/PLabc123",
    )


def test_protocol_url_trailing_slash_is_not_stripped() -> None:
    """The daemon strips it; this function does not, so the slash survives."""
    assert detect_and_normalize_source_url("reddit://rust/") == (
        "reddit",
        "https://www.reddit.com/r/rust/",
    )


def test_leading_whitespace_defeats_scheme_detection() -> None:
    """The daemon strips first and still sees reddit://; this function does not."""
    assert detect_and_normalize_source_url(" reddit://rust") == (
        "rss",
        " reddit://rust",
    )
