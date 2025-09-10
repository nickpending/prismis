"""Unit tests for config loading logic."""

import tempfile
from pathlib import Path
import pytest

from config import Config
from defaults import DEFAULT_CONTEXT_MD


def test_config_loading_with_all_files_present() -> None:
    """Test Config.from_file() with valid config.toml and context.md files."""
    # Test TOML content
    test_toml = """[daemon]
fetch_interval = 45
max_items_per_feed = 15

[llm]
provider = "anthropic"
model = "claude-3-haiku"

[notifications]
high_priority_only = false
"""

    # Test context content
    test_context = "# Custom Context\n\nCustom priorities defined here."

    # Create temp directory structure
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"

        # Write test files
        config_path.write_text(test_toml)
        context_path.write_text(test_context)

        # Execute function
        config = Config.from_file(config_path)

        # Verify config sections loaded correctly
        assert config.fetch_interval == 45
        assert config.max_items == 15
        assert config.llm_provider == "anthropic"
        assert config.llm_model == "claude-3-haiku"
        assert config.high_priority_only is False

        # Verify context loaded
        assert config.context == test_context

    finally:
        # Cleanup
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_missing_files_uses_defaults() -> None:
    """Test Config.from_file() falls back to defaults when files don't exist."""
    # Create temp directory without config files
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        # Execute function (no config files exist)
        config = Config.from_file(config_path)

        # Verify defaults are used
        assert config.fetch_interval == 30  # From DEFAULT_CONFIG_TOML
        assert config.max_items == 25  # Our new default
        assert config.llm_provider == "openai"
        assert config.llm_model == "gpt-4.1-mini"

        # Verify default context is used
        assert "High Priority Topics" in config.context
        assert "AI/LLM breakthroughs" in config.context

    finally:
        # Cleanup
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_malformed_toml_uses_defaults() -> None:
    """Test Config.from_file() handles malformed TOML by falling back to defaults."""
    # Malformed TOML content
    malformed_toml = """[daemon
    fetch_interval = "not a number"
    [llm]
    provider =
    """

    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"

        # Write malformed TOML
        config_path.write_text(malformed_toml)
        context_path.write_text("Valid context")

        # Execute function
        config = Config.from_file(config_path)

        # Should fall back to defaults
        assert config.fetch_interval == 30
        assert config.llm_provider == "openai"

        # Context should still load
        assert config.context == "Valid context"

    finally:
        # Cleanup
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_unreadable_context_uses_default() -> None:
    """Test Config.from_file() handles context.md read errors by using default."""
    valid_toml = """[daemon]
fetch_interval = 60

[llm]
provider = "openai"
"""

    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        context_path = Path(temp_dir) / "context.md"

        # Write valid TOML
        config_path.write_text(valid_toml)

        # Create context file but make it unreadable (on Unix systems)
        context_path.write_text("Some content")
        import os

        if os.name != "nt":  # Skip on Windows
            context_path.chmod(0o000)  # Remove all permissions

        # Execute function
        config = Config.from_file(config_path)

        # Config should load normally
        assert config.fetch_interval == 60

        # Should use default context due to read error (or if Windows, will read it)
        if os.name != "nt":
            from defaults import DEFAULT_CONTEXT_MD

            assert config.context == DEFAULT_CONTEXT_MD
            # Restore permissions for cleanup
            context_path.chmod(0o644)

    finally:
        # Cleanup
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_structure_contains_all_expected_fields() -> None:
    """Test Config always has all expected fields."""
    # Create config with defaults
    config = Config()

    # Verify all expected fields present
    assert hasattr(config, "fetch_interval")
    assert hasattr(config, "max_items")
    assert hasattr(config, "max_days_lookback")
    assert hasattr(config, "llm_provider")
    assert hasattr(config, "llm_model")
    assert hasattr(config, "llm_api_key")
    assert hasattr(config, "high_priority_only")
    assert hasattr(config, "notification_command")
    assert hasattr(config, "context")

    # Verify types
    assert isinstance(config.fetch_interval, int)
    assert isinstance(config.max_items, int)
    assert isinstance(config.llm_provider, str)
    assert isinstance(config.context, str)


def test_config_max_items_validation() -> None:
    """Test Config.validate() correctly validates max_items range."""
    # Test valid values at boundaries
    config_min = Config(max_items=1)
    config_min.validate()  # Should not raise

    config_max = Config(max_items=100)
    config_max.validate()  # Should not raise

    config_normal = Config(max_items=25)
    config_normal.validate()  # Should not raise

    # Test invalid values
    with pytest.raises(ValueError, match="max_items must be between 1 and 100, got 0"):
        Config(max_items=0).validate()

    with pytest.raises(
        ValueError, match="max_items must be between 1 and 100, got 101"
    ):
        Config(max_items=101).validate()

    with pytest.raises(ValueError, match="max_items must be between 1 and 100, got -5"):
        Config(max_items=-5).validate()

    with pytest.raises(
        ValueError, match="max_items must be between 1 and 100, got 1000"
    ):
        Config(max_items=1000).validate()


def test_config_fetch_interval_validation() -> None:
    """Test Config.validate() correctly validates fetch_interval."""
    # Test valid values
    config_min = Config(fetch_interval=1)
    config_min.validate()  # Should not raise

    config_normal = Config(fetch_interval=30)
    config_normal.validate()  # Should not raise

    # Test invalid values
    with pytest.raises(
        ValueError, match="fetch_interval must be at least 1 minute, got 0"
    ):
        Config(fetch_interval=0).validate()

    with pytest.raises(
        ValueError, match="fetch_interval must be at least 1 minute, got -1"
    ):
        Config(fetch_interval=-1).validate()


def test_config_default_values() -> None:
    """Test Config dataclass has correct default values."""
    config = Config()

    # Verify daemon defaults
    assert config.max_items == 25
    assert config.fetch_interval == 30
    assert config.max_days_lookback == 30

    # Verify LLM defaults
    assert config.llm_provider == "openai"
    assert config.llm_model == "gpt-4.1-mini"
    assert config.llm_api_key == "env:OPENAI_API_KEY"

    # Verify notification defaults
    assert config.high_priority_only is True
    assert config.notification_command == "terminal-notifier"
