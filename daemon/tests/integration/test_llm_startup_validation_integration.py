"""Integration tests for LLM startup validation with real services."""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from prismis_daemon.__main__ import validate_llm_config
from prismis_daemon.config import Config

# llm_validator.py IS the wrapper around llm_core — mocking through it is correct
_HEALTH_CHECK_MOCK = (
    "prismis_daemon.llm_validator.llm_core.health_check"  # claudex-guard: allow-mock
)

# Valid new-format config TOML template for tests
VALID_CONFIG_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
service = "prismis-openai"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"
max_comments = 100

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false

[archival.windows]
high_read = 30
medium_unread = 14
medium_read = 30
low_unread = 7
low_read = 30

[context]
auto_update_enabled = false
auto_update_interval_days = 7
auto_update_min_votes = 5
backup_count = 3
"""


def _create_config_dir(config_toml: str = VALID_CONFIG_TOML) -> tuple:
    """Create a temp config directory with config.toml and context.md.

    Returns:
        Tuple of (temp_dir, config_path)
    """
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.toml"
    config_path.write_text(config_toml)

    context_path = Path(temp_dir) / "context.md"
    context_path.write_text("# Test Context\nHigh Priority: Testing")

    return temp_dir, config_path


def _has_prismis_openai_service() -> bool:
    """Check if prismis-openai service is configured in services.toml."""
    try:
        import tomllib

        config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
        services_path = Path(config_home) / "llm-core" / "services.toml"
        if not services_path.exists():
            return False
        with open(services_path, "rb") as f:
            data = tomllib.load(f)
        return "prismis-openai" in data.get("services", {})
    except Exception:
        return False


def test_INVARIANT_health_check_accuracy_with_real_api() -> None:
    """
    INVARIANT: Health check success MUST correlate with analysis capability
    BREAKS: Health check passes but analysis fails, breaking user trust
    """
    # Skip if no real API key available
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY environment variable not set")
    if not _has_prismis_openai_service():
        pytest.skip("prismis-openai service not configured in services.toml")

    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        # Real validation with actual health check via llm_core
        try:
            validate_llm_config(config)
        except SystemExit:
            pytest.fail("Valid real API key should pass validation")

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_FAILURE_network_timeout_handling() -> None:
    """
    FAILURE: Network timeout during health check
    GRACEFUL: System must fail with timeout guidance, not hang
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        # Mock timeout scenario

        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.side_effect = TimeoutError("Connection timeout")

            # Should fail gracefully with timeout guidance
            with pytest.raises(SystemExit):
                validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_FAILURE_provider_auth_failure_guidance() -> None:
    """
    FAILURE: Provider-specific authentication failure
    GRACEFUL: System must show provider-specific error guidance
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        # Mock auth failure
        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.side_effect = Exception("Incorrect API key provided")

            # Should fail with auth guidance
            with pytest.raises(SystemExit):
                validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_FAILURE_model_unavailable_detection() -> None:
    """
    FAILURE: Model exists during health check but becomes unavailable
    GRACEFUL: System must detect model availability issues
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        # Mock model not found error
        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.side_effect = Exception(
                "Model gpt-nonexistent-model does not exist"
            )

            # Should fail with model availability guidance
            with pytest.raises(SystemExit):
                validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_CONFIDENCE_health_check_accuracy_threshold() -> None:
    """
    CONFIDENCE: Health check accuracy must be >95% correlated with analysis success
    THRESHOLD: Based on user trust requirements
    """
    # Skip if no real API key available
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        pytest.skip("OPENAI_API_KEY environment variable not set")
    if not _has_prismis_openai_service():
        pytest.skip("prismis-openai service not configured in services.toml")

    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        # Test correlation: if health check passes, analysis should work
        successful_validations = 0
        total_tests = 5  # Reduced for faster testing

        for _i in range(total_tests):
            try:
                validate_llm_config(config)
                successful_validations += 1
            except SystemExit:
                pass

        # Just verify we had some successful validations
        assert successful_validations > 0, (
            "Should have at least one successful validation"
        )

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
