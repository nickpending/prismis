"""Unit tests for config.py migration detection and llm_light_service field — Task 1.1."""

import tempfile
from pathlib import Path

import pytest

from prismis_daemon.config import Config

# Minimal valid config using the new [llm] light_service= dual-service format.
# All required sections must be present; missing any mandatory key raises ValueError.
_NEW_FORMAT_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30

[llm]
light_service = "prismis-openai"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"
max_comments = 5

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false
[archival.windows]
high_read = 999
medium_unread = 30
medium_read = 14
low_unread = 14
low_read = 7

[context]
auto_update_enabled = false
auto_update_interval_days = 30
auto_update_min_votes = 5
backup_count = 10
"""


def _write_config(tmpdir: str, content: str, write_context: bool = True) -> Path:
    """Write config.toml (and optional context.md) to tmpdir, return config path."""
    config_path = Path(tmpdir) / "config.toml"
    config_path.write_text(content)
    if write_context:
        (Path(tmpdir) / "context.md").write_text("# Test context")
    return config_path


def test_new_config_format_loads_llm_light_service_field() -> None:
    """
    INVARIANT: New config format sets llm_light_service correctly.
    BREAKS: Daemon silently uses wrong service name for all LLM calls.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, _NEW_FORMAT_TOML)
        config = Config.from_file(config_path)

        assert config.llm_light_service == "prismis-openai"


def test_new_config_format_has_no_old_llm_fields() -> None:
    """
    INVARIANT: Removed fields must not be present on Config after migration.
    BREAKS: Code silently falls back to deleted field, masking migration failures.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, _NEW_FORMAT_TOML)
        config = Config.from_file(config_path)

        assert not hasattr(config, "llm_provider"), "llm_provider must be removed"
        assert not hasattr(config, "llm_model"), "llm_model must be removed"
        assert not hasattr(config, "llm_api_key"), "llm_api_key must be removed"
        assert not hasattr(config, "llm_api_base"), "llm_api_base must be removed"
        assert not hasattr(config, "llm_max_retries"), "llm_max_retries must be removed"
        assert not hasattr(config, "llm_retry_backoff_base"), (
            "llm_retry_backoff_base must be removed"
        )


def test_old_config_format_raises_value_error_with_migration_message() -> None:
    """
    INVARIANT: Old [llm] provider= config must fail clearly, not silently.
    BREAKS: Daemon runs with uninitialised LLM service, producing cryptic errors at analysis time.
    """
    old_format_toml = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "sk-test"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"
max_comments = 5

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false
[archival.windows]
high_read = 999
medium_unread = 30
medium_read = 14
low_unread = 14
low_read = 7

[context]
auto_update_enabled = false
auto_update_interval_days = 30
auto_update_min_votes = 5
backup_count = 10
"""
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, old_format_toml)

        with pytest.raises(ValueError, match="migrate-config"):
            Config.from_file(config_path)


def test_missing_llm_light_service_key_raises_value_error() -> None:
    """
    INVARIANT: Empty [llm] section (no light_service key) must fail with actionable error.
    BREAKS: Config loads with llm_light_service=None, causing silent NoneType errors at runtime.
    """
    empty_llm_toml = _NEW_FORMAT_TOML.replace(
        'light_service = "prismis-openai"',
        "# light_service field intentionally omitted",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, empty_llm_toml)

        with pytest.raises(ValueError, match="migrate-config"):
            Config.from_file(config_path)
