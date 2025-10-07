"""Unit tests for host configuration security - protecting defaults and validation."""

import tempfile
from pathlib import Path
import pytest

from prismis_daemon.config import Config


def test_host_binding_security_default() -> None:
    """
    INVARIANT: Security by Default - api_host defaults to localhost-only
    BREAKS: Accidental LAN exposure if users don't explicitly opt-in
    """
    # Test with minimal config (no host specified)
    test_toml = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
"""

    temp_dir = tempfile.mkdtemp()
    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"

        config_path.write_text(test_toml)
        context_path.write_text("# Test context")

        # Execute config loading
        config = Config.from_file(config_path)

        # CRITICAL: Must default to localhost for security
        assert config.api_host == "127.0.0.1", (
            f"Default host should be localhost, got {config.api_host}"
        )

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_host_config_explicit_values() -> None:
    """
    INVARIANT: Host Binding Correct - config.api_host properly loads explicit values
    BREAKS: LAN access doesn't work when user configures it
    """
    # Test with explicit LAN binding
    test_toml_lan = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
host = "0.0.0.0"
"""

    # Test with explicit localhost
    test_toml_localhost = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
host = "192.168.1.100"
"""

    temp_dir = tempfile.mkdtemp()
    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test context")

        # Test LAN binding
        config_path.write_text(test_toml_lan)
        config = Config.from_file(config_path)
        assert config.api_host == "0.0.0.0", (
            f"Should load 0.0.0.0, got {config.api_host}"
        )

        # Test specific IP
        config_path.write_text(test_toml_localhost)
        config = Config.from_file(config_path)
        assert config.api_host == "192.168.1.100", (
            f"Should load specific IP, got {config.api_host}"
        )

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_malformed_host_config_handling() -> None:
    """
    FAILURE: Malformed config.toml with host field issues
    GRACEFUL: System must handle gracefully, not crash
    """
    # Test with missing api section (should use default)
    test_toml_missing = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"
"""

    # Test with malformed host value (should handle gracefully)
    test_toml_malformed = """[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"

[notifications]
high_priority_only = true
command = "test-command"

[api]
key = "test-api-key"
# This section has syntax errors that should be handled
host =
"""

    temp_dir = tempfile.mkdtemp()
    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test context")

        # Test missing API section - should not crash, should get defaults
        config_path.write_text(test_toml_missing)
        with pytest.raises(ValueError, match="API key not configured"):
            # This should fail because api.key is required
            Config.from_file(config_path)

        # Test malformed TOML - should not crash
        config_path.write_text(test_toml_malformed)
        with pytest.raises(ValueError, match="Failed to parse config file"):
            # Should fail parsing but with clear error, not crash
            Config.from_file(config_path)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
