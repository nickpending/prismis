"""Unit tests for SourceValidator - protecting invariants."""

from prismis_daemon.validator import SourceValidator


def test_timeout_configuration() -> None:
    """
    INVARIANT: Network requests have 5-second timeout
    BREAKS: CLI hangs indefinitely if timeout not set
    """
    validator = SourceValidator()

    # Verify timeout is configured correctly
    assert validator.timeout == 5.0, (
        f"Timeout must be 5 seconds, got {validator.timeout}"
    )

    # This prevents the CLI from hanging when sources are slow/unreachable


def test_tuple_return_contract() -> None:
    """
    INVARIANT: All validation methods return (bool, Optional[str]) tuple
    BREAKS: CLI crashes if return type is wrong
    """
    validator = SourceValidator()

    # Test main validate_source method with unknown type
    result = validator.validate_source("http://example.com", "unknown")
    assert isinstance(result, tuple), "Must return tuple"
    assert len(result) == 2, "Must return 2-element tuple"
    assert isinstance(result[0], bool), "First element must be bool"
    assert result[1] is None or isinstance(result[1], str), (
        "Second element must be None or str"
    )

    # Verify the unknown type is handled correctly
    assert result[0] is False, "Unknown type should return False"
    assert "Unknown source type" in result[1], "Should explain unknown type"

    # Test with invalid URL for each type to ensure tuple contract holds
    test_cases = [
        ("not-a-url", "rss"),
        ("not-a-url", "reddit"),
        ("not-a-url", "youtube"),
    ]

    for url, source_type in test_cases:
        result = validator.validate_source(url, source_type)
        assert isinstance(result, tuple), f"{source_type} must return tuple"
        assert len(result) == 2, f"{source_type} must return 2-element tuple"
        assert isinstance(result[0], bool), f"{source_type} first element must be bool"
        assert result[1] is None or isinstance(result[1], str), (
            f"{source_type} second element must be None or str"
        )


def test_user_agent_configuration() -> None:
    """
    INVARIANT: User-Agent header is set for all requests
    BREAKS: Reddit API rejects requests without User-Agent
    """
    validator = SourceValidator()

    # Verify User-Agent is configured
    assert validator.user_agent, "User-Agent must be set"
    assert "Prismis" in validator.user_agent, "User-Agent should identify as Prismis"

    # This prevents Reddit from blocking our requests
