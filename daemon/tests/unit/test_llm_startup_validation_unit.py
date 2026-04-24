"""Unit tests for LLM startup validation logic — updated for dual-service (Task 1.1)."""

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

# Valid dual-service config TOML — uses light_service= (task 1.1 format)
VALID_CONFIG_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
light_service = "prismis-openai"

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

# Dual-service config — both light and deep configured
DUAL_SERVICE_CONFIG_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
light_service = "prismis-openai"
deep_service = "prismis-openai-deep"

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


def test_INVARIANT_valid_service_config_passes_validation() -> None:
    """
    INVARIANT: Valid light_service config MUST pass validation when health check succeeds.
    BREAKS: Valid configs rejected, preventing daemon start.
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.return_value = None  # Success

            # Should NOT raise exception for valid config
            try:
                validate_llm_config(config)
            except SystemExit:
                pytest.fail("Valid config should not cause SystemExit")

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_health_check_failure_prevents_daemon_start() -> None:
    """
    INVARIANT: Failed light service health check MUST prevent daemon start.
    BREAKS: Daemon starts but fails during content analysis, corrupting user experience.
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.side_effect = Exception("Connection refused")

            # Validation MUST prevent daemon start on light service failure
            with pytest.raises(SystemExit):
                validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_old_config_format_rejected() -> None:
    """
    INVARIANT: Old config format with provider/model/api_key MUST be rejected.
    BREAKS: Old configs silently accepted, causing runtime errors.
    """
    old_format_config = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "sk-test-key-1234567890"

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
    temp_dir, config_path = _create_config_dir(old_format_config)

    try:
        # Config.from_file should raise ValueError for old format (no light_service)
        with pytest.raises(ValueError, match="migrate-config"):
            Config.from_file(config_path)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_startup_validation_calls_health_check_with_light_service() -> None:
    """
    INVARIANT: Startup validation MUST call llm_core.health_check with the light service name.
    BREAKS: Health check called with wrong arguments, silently passing with incorrect config.
    """
    temp_dir, config_path = _create_config_dir()

    try:
        config = Config.from_file(config_path)

        with patch(_HEALTH_CHECK_MOCK) as mock_health:
            mock_health.return_value = None

            validate_llm_config(config)

            # Light service health_check called with correct service name
            mock_health.assert_any_call(service="prismis-openai")

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_deep_service_failure_is_non_fatal() -> None:
    """
    INVARIANT: Deep service health check failure MUST NOT cause sys.exit(1).
    BREAKS: Daemon cannot start when gpt-5-mini tier is unreachable — violates graceful degradation.
    """
    temp_dir, config_path = _create_config_dir(DUAL_SERVICE_CONFIG_TOML)

    try:
        config = Config.from_file(config_path)

        # Light succeeds, deep fails
        call_count = [0]

        def mock_health_check(service: str) -> None:
            call_count[0] += 1
            if service == "prismis-openai-deep":
                raise Exception("Service unreachable")
            # light service succeeds (returns None)

        with patch(_HEALTH_CHECK_MOCK, side_effect=mock_health_check):
            # Deep failure must NOT cause SystemExit
            try:
                validate_llm_config(config)
            except SystemExit:
                pytest.fail(
                    "Deep service failure must not cause SystemExit — violates graceful degradation"
                )

        assert call_count[0] == 2, "Both light and deep health checks must be called"

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_deep_service_not_configured_skipped() -> None:
    """
    INVARIANT: When deep_service is None (not configured), only light is checked.
    BREAKS: Validator attempts health check on None service, crashes at startup.
    """
    temp_dir, config_path = _create_config_dir(VALID_CONFIG_TOML)

    try:
        config = Config.from_file(config_path)

        assert config.llm_deep_service is None, "deep_service must default to None"

        call_count = [0]

        def mock_health_check(service: str) -> None:
            call_count[0] += 1

        with patch(_HEALTH_CHECK_MOCK, side_effect=mock_health_check):
            validate_llm_config(config)

        assert call_count[0] == 1, (
            "Only light service health check must fire when deep_service is None"
        )

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
