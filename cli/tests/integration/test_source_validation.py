"""Integration tests for source validation - protecting invariants."""

import sys
from pathlib import Path
from unittest.mock import patch
from typer.testing import CliRunner

# Add CLI src to path
cli_src = Path(__file__).parent.parent.parent / "src"
daemon_src = Path(__file__).parent.parent.parent.parent / "daemon" / "src"
sys.path.insert(0, str(cli_src))
sys.path.insert(0, str(daemon_src))

from cli.__main__ import app  # noqa: E402
from prismis_daemon.storage import Storage  # noqa: E402


def test_invalid_source_not_added_when_validator_rejects(
    mock_home_dir: Path, cli_runner: CliRunner
) -> None:
    """
    INVARIANT: When validator returns False, source MUST NOT be added to database
    BREAKS: Invalid sources corrupt content stream if they enter database
    """
    # Try to add an invalid RSS feed (validator will reject)
    result = cli_runner.invoke(
        app, ["source", "add", "https://invalid.example.com/not-a-feed"]
    )

    # Command should fail
    assert result.exit_code == 1, "Command should exit with error code"
    assert "Validation failed" in result.stdout, "Should show validation failure"

    # CRITICAL: Verify source was NOT added to database
    storage = Storage(mock_home_dir)
    sources = storage.get_all_sources()
    assert len(sources) == 0, f"No sources should be added, but found {len(sources)}"

    # Try with invalid Reddit source
    result = cli_runner.invoke(
        app, ["source", "add", "https://reddit.com/r/thiswillfail999999"]
    )

    assert result.exit_code == 1
    sources = storage.get_all_sources()
    assert len(sources) == 0, "Invalid Reddit source should not be added"


def test_validation_error_message_shown_to_user(
    mock_home_dir: Path, cli_runner: CliRunner
) -> None:
    """
    INVARIANT: When validation fails, user MUST see the actual error reason
    BREAKS: Users can't fix problems without knowing what's wrong
    """
    # Test with network error (unreachable host)
    result = cli_runner.invoke(
        app, ["source", "add", "https://this-host-does-not-exist-999.com/feed"]
    )

    assert result.exit_code == 1
    # User must see WHY it failed, not just that it failed
    assert "Validation failed:" in result.stdout, "Must show validation failed"
    # Should contain actual error information (network error, 404, etc)
    assert any(
        [
            "Network error" in result.stdout,
            "not known" in result.stdout,
            "404" in result.stdout,
            "resolve" in result.stdout,
        ]
    ), f"Error message missing details. Got: {result.stdout}"

    # Test with invalid YouTube URL (wrong format)
    result = cli_runner.invoke(
        app, ["source", "add", "https://youtube.com/watch?v=123"]
    )

    assert result.exit_code == 1
    assert "not supported" in result.stdout or "channel URL" in result.stdout, (
        "Should explain why YouTube URL is invalid"
    )


def test_source_type_detection_for_validation(
    mock_home_dir: Path, cli_runner: CliRunner
) -> None:
    """
    INVARIANT: Source type MUST be correctly identified for proper validation
    BREAKS: Wrong validator = wrong validation = bad sources enter system
    """
    # Mock validator to track which validator method was called
    with patch("cli.source.SourceValidator") as MockValidator:
        mock_instance = MockValidator.return_value
        mock_instance.validate_source.return_value = (True, None)

        # Test Reddit URL detection
        cli_runner.invoke(app, ["source", "add", "https://reddit.com/r/python"])

        # Verify Reddit was detected and passed to validator
        mock_instance.validate_source.assert_called()
        call_args = mock_instance.validate_source.call_args[0]
        assert call_args[1] == "reddit", f"Expected 'reddit' type, got {call_args[1]}"

        # Reset mock
        mock_instance.reset_mock()

        # Test reddit:// protocol detection
        cli_runner.invoke(app, ["source", "add", "reddit://rust"])

        call_args = mock_instance.validate_source.call_args[0]
        assert call_args[1] == "reddit", "reddit:// should detect as reddit type"

        # Reset mock
        mock_instance.reset_mock()

        # Test YouTube detection
        cli_runner.invoke(app, ["source", "add", "https://youtube.com/@channel"])

        call_args = mock_instance.validate_source.call_args[0]
        assert call_args[1] == "youtube", "YouTube URL should detect as youtube type"

        # Reset mock
        mock_instance.reset_mock()

        # Test default RSS detection
        cli_runner.invoke(app, ["source", "add", "https://example.com/feed.xml"])

        call_args = mock_instance.validate_source.call_args[0]
        assert call_args[1] == "rss", "Unknown URLs should default to rss type"


### CHECKPOINT 7: Implement Failure Mode Tests


def test_validator_unavailable_warning(
    mock_home_dir: Path, cli_runner: CliRunner
) -> None:
    """
    FAILURE: Validator import fails (module not available)
    GRACEFUL: User is warned, source still added (with clear warning)
    """
    # Patch the VALIDATOR_AVAILABLE flag to simulate import failure
    with patch("cli.source.VALIDATOR_AVAILABLE", False):
        result = cli_runner.invoke(
            app, ["source", "add", "https://example.com/feed.xml", "--name", "Test"]
        )

        # Command should succeed but with warning
        assert result.exit_code == 0, "Should still add source without validator"

        # User MUST be informed validation was skipped
        assert (
            "Skipping validation" in result.stdout
            or "validator not available" in result.stdout
        ), "User must know validation was skipped"

        # Source should be added (fallback behavior)
        storage = Storage(mock_home_dir)
        sources = storage.get_all_sources()
        assert len(sources) == 1, "Source should be added when validator unavailable"
        assert sources[0]["name"] == "Test"

        # This is a trade-off: accept sources without validation vs blocking all adds
        # User is informed so they can manually verify
