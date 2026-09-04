"""Unit tests for `prismis-cli source add` URL → source_type detection.

INVARIANT: Source type MUST be correctly identified from the URL.
BREAKS: A misdetected type is fetched by the wrong fetcher and the source never yields
content.

This replaces the coverage deleted with tests/integration/test_source_validation.py and
tests/integration/test_source_commands.py. Those patched `cli.source.SourceValidator` and
`cli.source.VALIDATOR_AVAILABLE`, neither of which exists — the CLI no longer validates
locally or writes SQLite directly; it adds through APIClient.add_source (source.py:115) and
the daemon owns validation. They also hit the live network, which a CI-run gate rules out.

APIClient is the true external boundary here: it is the HTTP hop to a separately deployed
daemon. Patching it at the module boundary follows tests/unit/test_extract_command_unit.py.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from cli.source import app as source_app


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
        (
            "https://reddit.com/r/rust",
            "reddit",
            "https://reddit.com/r/rust",
        ),
        (
            "https://youtube.com/@mkbhd",
            "youtube",
            "https://youtube.com/@mkbhd",
        ),
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
    runner = CliRunner()

    with patch("cli.source.APIClient") as MockClient:
        MockClient.return_value.add_source.return_value = {
            "success": True,
            "message": "Source added successfully",
            "data": {"id": "test-id", "url": expected_url},
        }
        result = runner.invoke(source_app, ["add", input_url])

    assert result.exit_code == 0, f"add {input_url} failed: {result.output}"

    MockClient.return_value.add_source.assert_called_once()
    called_url, called_type = MockClient.return_value.add_source.call_args[0][:2]

    assert called_type == expected_type, (
        f"{input_url} must be detected as {expected_type}, got {called_type}"
    )
    assert called_url == expected_url, (
        f"{input_url} must normalize to {expected_url}, got {called_url}"
    )
