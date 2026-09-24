"""Unit tests for `prismis-cli source add` URL -> source-type detection.

INVARIANT: The CLI decides only the source type and leaves the URL as typed; the
daemon's normalize_source_url is the one place protocol URLs are expanded.
BREAKS: A misdetected type is fetched by the wrong fetcher. A second expansion in the
CLI is how the same source came to be stored under two URLs depending on which client
added it (#65).
"""

import pytest

from cli.source import detect_source_type


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("reddit://rust", "reddit"),
        ("reddit://rust/", "reddit"),
        ("https://reddit.com/r/rust", "reddit"),
        ("youtube://@mkbhd", "youtube"),
        ("youtube://UC9-y-6csu5WGm29I7JiwpnA", "youtube"),
        ("youtube://PLabc123", "youtube"),
        ("https://youtube.com/@mkbhd", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://example.com/CHANGELOG.md", "file"),
        ("https://simonwillison.net/atom/everything/", "rss"),
    ],
)
def test_source_type_detected_from_url(url: str, expected: str) -> None:
    assert detect_source_type(url) == expected
