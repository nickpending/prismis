"""Integration tests for config loading with real filesystem."""

import tempfile
from pathlib import Path
import shutil

from config import Config
from defaults import ensure_config, DEFAULT_CONTEXT_MD


def test_complete_config_workflow_with_real_files() -> None:
    """Test complete config workflow: ensure_config() -> load_config() with real files."""
    # Create temporary home directory
    temp_home = tempfile.mkdtemp()

    try:
        # Mock Path.home() to use our temp directory
        import config
        import defaults

        original_home = Path.home

        def mock_home() -> Path:
            return Path(temp_home)

        # Patch both modules
        config.Path.home = mock_home
        defaults.Path.home = mock_home

        # Step 1: Ensure config files are created
        ensure_config()

        # Verify files were created
        config_dir = Path(temp_home) / ".config" / "prismis"
        assert config_dir.exists()
        assert (config_dir / "config.toml").exists()
        assert (config_dir / "context.md").exists()

        # Step 2: Load configuration
        config_obj = Config.from_file(config_dir / "config.toml")

        # Verify daemon settings from defaults
        assert config_obj.fetch_interval == 30
        assert config_obj.max_items == 25  # Default value
        assert config_obj.max_days_lookback == 30

        # Verify LLM settings from defaults
        assert config_obj.llm_provider == "openai"
        assert config_obj.llm_model == "gpt-4.1-mini"
        assert "env:OPENAI_API_KEY" in config_obj.llm_api_key

        # Verify notifications settings from defaults
        assert config_obj.high_priority_only is True
        assert config_obj.notification_command == "terminal-notifier"

        # Verify context contains expected content
        assert "High Priority Topics" in config_obj.context
        assert "Medium Priority Topics" in config_obj.context
        assert "Low Priority Topics" in config_obj.context
        assert "Not Interested" in config_obj.context
        assert "AI/LLM breakthroughs" in config_obj.context

        # Restore original home function
        config.Path.home = original_home
        defaults.Path.home = original_home

    finally:
        # Cleanup
        shutil.rmtree(temp_home, ignore_errors=True)


def test_config_loading_with_custom_user_modifications() -> None:
    """Test config loading works when user modifies config files."""
    # Create temporary home directory
    temp_home = tempfile.mkdtemp()

    try:
        import config
        import defaults

        original_home = Path.home

        def mock_home() -> Path:
            return Path(temp_home)

        config.Path.home = mock_home
        defaults.Path.home = mock_home

        # Create initial config
        ensure_config()

        # Modify config.toml with custom settings
        config_dir = Path(temp_home) / ".config" / "prismis"
        custom_config = """[daemon]
fetch_interval = 60  
max_items_per_feed = 20
max_days_lookback = 60

[llm]
provider = "anthropic"
model = "claude-3-sonnet"
api_key = "env:ANTHROPIC_API_KEY"

[notifications]
high_priority_only = false
command = "notify-send"
"""
        (config_dir / "config.toml").write_text(custom_config)

        # Modify context.md with custom content
        custom_context = """# My Personal Context

## High Priority Topics
- Rust programming
- Database performance
- AI research papers

## Medium Priority Topics  
- Python updates
- Web development

## Low Priority Topics
- General tech news

## Not Interested
- Social media updates
- Celebrity news
"""
        (config_dir / "context.md").write_text(custom_context)

        # Load configuration
        result = load_config()

        # Verify custom config loaded
        assert result["daemon"]["fetch_interval"] == 60
        assert result["daemon"]["max_items_per_feed"] == 20
        assert result["daemon"]["max_days_lookback"] == 60

        assert result["llm"]["provider"] == "anthropic"
        assert result["llm"]["model"] == "claude-3-sonnet"
        assert result["llm"]["api_key"] == "env:ANTHROPIC_API_KEY"

        assert result["notifications"]["high_priority_only"] is False
        assert result["notifications"]["command"] == "notify-send"

        # Verify custom context loaded
        assert "My Personal Context" in result["context"]
        assert "Rust programming" in result["context"]
        assert "Database performance" in result["context"]
        assert "Social media updates" in result["context"]

        # Restore original home function
        config.Path.home = original_home
        defaults.Path.home = original_home

    finally:
        # Cleanup
        shutil.rmtree(temp_home, ignore_errors=True)


def test_config_integration_with_partially_missing_files() -> None:
    """Test config loading when only some files exist."""
    # Create temporary home directory
    temp_home = tempfile.mkdtemp()

    try:
        import config

        original_home = Path.home

        def mock_home() -> Path:
            return Path(temp_home)

        config.Path.home = mock_home

        # Create config directory and only config.toml (no context.md)
        config_dir = Path(temp_home) / ".config" / "prismis"
        config_dir.mkdir(parents=True)

        partial_config = """[daemon]
fetch_interval = 15

[llm]
provider = "ollama"
model = "llama2"
"""
        (config_dir / "config.toml").write_text(partial_config)
        # Note: context.md intentionally not created

        # Load configuration
        config_obj = Config.from_file(config_path)

        # Verify config.toml loaded
        assert config_obj.fetch_interval == 15
        assert config_obj.llm_provider == "ollama"
        assert config_obj.llm_model == "llama2"

        # Verify default context used (since context.md missing)
        assert "High Priority Topics" in config_obj.context
        assert "AI/LLM breakthroughs" in config_obj.context
        assert config_obj.context == DEFAULT_CONTEXT_MD

        # Restore original home function
        config.Path.home = original_home

    finally:
        # Cleanup
        shutil.rmtree(temp_home, ignore_errors=True)


def test_config_from_file_with_max_items() -> None:
    """Test Config.from_file() correctly loads max_items from TOML file."""
    # Create a temporary config file
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.toml"

    try:
        # Write test config with max_items
        test_toml = """[daemon]
fetch_interval = 30
max_items_per_feed = 50
max_days_lookback = 7

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "test-key"

[notifications]
high_priority_only = false
command = "test-notifier"
"""
        config_path.write_text(test_toml)

        # Load config using from_file()
        config = Config.from_file(config_path)

        # Verify max_items loaded correctly
        assert config.max_items == 50
        assert config.fetch_interval == 30
        assert config.max_days_lookback == 7

        # Verify other fields loaded
        assert config.llm_provider == "openai"
        assert config.llm_model == "gpt-4o-mini"
        assert config.high_priority_only is False

        # Test validation is called during load
        config.validate()  # Should not raise

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_from_file_uses_defaults_when_max_items_missing() -> None:
    """Test Config.from_file() uses default max_items when not in TOML."""
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.toml"

    try:
        # Write config without max_items_per_feed
        test_toml = """[daemon]
fetch_interval = 45

[llm]
provider = "anthropic"
"""
        config_path.write_text(test_toml)

        # Load config
        config = Config.from_file(config_path)

        # Should use default value
        assert config.max_items == 25
        assert config.fetch_interval == 45
        assert config.llm_provider == "anthropic"

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
