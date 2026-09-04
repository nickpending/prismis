"""Unit tests for config loading logic."""

import tempfile
from pathlib import Path
import pytest

from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD
from conftest import TEST_API_KEY, make_config


def _write_config(dir_path: Path, **daemon_overrides: object) -> Path:
    """Materialize a valid config.toml from the production template.

    Values are edited in the rendered template rather than hand-authored, so the file
    stays complete against Config.from_file's required-field set (config.py:243-283)
    as that set changes.
    """
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    for key, value in daemon_overrides.items():
        lines = []
        for line in text.split("\n"):
            if line.startswith(f"{key} = "):
                line = f"{key} = {value}"
            lines.append(line)
        text = "\n".join(lines)
    path = dir_path / "config.toml"
    path.write_text(text)
    return path


def test_config_loading_with_all_files_present() -> None:
    """Test Config.from_file() with valid config.toml and context.md files."""
    test_context = "# Custom Context\n\nCustom priorities defined here."

    temp_dir = tempfile.mkdtemp()

    try:
        config_path = _write_config(
            Path(temp_dir), fetch_interval=45, max_items_rss=15
        )
        (Path(temp_dir) / "context.md").write_text(test_context)

        config = Config.from_file(config_path)

        # Verify config sections loaded correctly
        assert config.fetch_interval == 45
        assert config.max_items_rss == 15
        assert config.llm_light_service == "prismis-openai"
        assert config.high_priority_only is True
        assert config.api_key == TEST_API_KEY

        # Verify context loaded
        assert config.context == test_context

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_missing_file_raises() -> None:
    """Test Config.from_file() raises when the config file does not exist.

    The config file is required; silent fallback to defaults was removed so a
    misconfigured daemon fails loudly instead of running against invisible defaults
    (config.py:188-192).
    """
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"

        with pytest.raises(FileNotFoundError, match="Config file not found"):
            Config.from_file(config_path)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_malformed_toml_raises() -> None:
    """Test Config.from_file() raises on malformed TOML rather than using defaults.

    A parse error is a configuration bug; masking it with defaults hides it
    (config.py:196-198).
    """
    malformed_toml = """[daemon
    fetch_interval = "not a number"
    [llm]
    provider =
    """

    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        config_path.write_text(malformed_toml)
        (Path(temp_dir) / "context.md").write_text("Valid context")

        with pytest.raises(ValueError, match="Failed to parse config file"):
            Config.from_file(config_path)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_loading_with_unreadable_context_uses_default() -> None:
    """Test Config.from_file() handles context.md read errors by using default."""
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = _write_config(Path(temp_dir), fetch_interval=60)
        context_path = Path(temp_dir) / "context.md"

        # Create context file but make it unreadable (on Unix systems)
        context_path.write_text("Some content")
        import os

        if os.name != "nt":  # Skip on Windows
            context_path.chmod(0o000)  # Remove all permissions

        config = Config.from_file(config_path)

        # Config should load normally
        assert config.fetch_interval == 60

        # Should use default context due to read error (or if Windows, will read it)
        if os.name != "nt":
            assert config.context == DEFAULT_CONTEXT_MD
            # Restore permissions for cleanup
            context_path.chmod(0o644)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_structure_contains_all_expected_fields() -> None:
    """Test Config always has all expected fields."""
    config = make_config()

    # Verify all expected fields present
    for name in (
        "fetch_interval",
        "max_items_rss",
        "max_items_reddit",
        "max_items_youtube",
        "max_items_file",
        "max_days_lookback",
        "llm_light_service",
        "llm_deep_service",
        "api_key",
        "high_priority_only",
        "notification_command",
        "context",
    ):
        assert hasattr(config, name), f"Config is missing {name}"

    # Verify types
    assert isinstance(config.fetch_interval, int)
    assert isinstance(config.max_items_rss, int)
    assert isinstance(config.llm_light_service, str)
    assert isinstance(config.context, str)


def test_config_max_items_validation() -> None:
    """Test Config.validate() correctly validates the per-source max_items range."""
    # Test valid values at boundaries
    make_config(max_items_rss=1).validate()  # Should not raise
    make_config(max_items_rss=100).validate()  # Should not raise
    make_config(max_items_rss=25).validate()  # Should not raise

    # Test invalid values
    for bad in (0, 101, -5, 1000):
        with pytest.raises(
            ValueError, match=f"max_items_rss must be between 1 and 100, got {bad}"
        ):
            make_config(max_items_rss=bad).validate()


def test_config_fetch_interval_validation() -> None:
    """Test Config.validate() correctly validates fetch_interval."""
    # Test valid values
    config_min = make_config(fetch_interval=1)
    config_min.validate()  # Should not raise

    config_normal = make_config(fetch_interval=30)
    config_normal.validate()  # Should not raise

    # Test invalid values
    with pytest.raises(
        ValueError, match="fetch_interval must be at least 1 minute, got 0"
    ):
        make_config(fetch_interval=0).validate()

    with pytest.raises(
        ValueError, match="fetch_interval must be at least 1 minute, got -1"
    ):
        make_config(fetch_interval=-1).validate()


def test_config_default_values() -> None:
    """Test the shipped default config carries the documented values."""
    config = make_config()

    # Verify daemon defaults
    assert config.max_items_rss == 25
    assert config.max_items_reddit == 50
    assert config.max_items_youtube == 10
    assert config.max_items_file == 1
    assert config.fetch_interval == 30
    assert config.max_days_lookback == 30

    # Verify LLM defaults
    assert config.llm_light_service == "prismis-openai"
    assert config.llm_deep_service is None
    assert config.auto_extract == "none"

    # Verify notification defaults
    assert config.high_priority_only is True
    assert config.notification_command == "terminal-notifier"
