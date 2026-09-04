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

from cli.source import detect_and_normalize_source_url


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
