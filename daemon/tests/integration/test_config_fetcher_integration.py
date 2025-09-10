"""Integration test for Config → RSSFetcher flow."""

import tempfile
from pathlib import Path
import shutil

from config import Config
from fetchers.rss import RSSFetcher


def test_config_max_items_flows_to_fetcher() -> None:
    """Test that max_items from Config properly flows to RSSFetcher."""
    # Create a config file with custom max_items
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.toml"

    try:
        # Write config with max_items = 75
        test_toml = """[daemon]
fetch_interval = 30
max_items_per_feed = 75
max_days_lookback = 7

[llm]
provider = "openai"
model = "gpt-4o-mini"
"""
        config_path.write_text(test_toml)

        # Load config
        config = Config.from_file(config_path)

        # Verify config loaded the value
        assert config.max_items == 75

        # Create RSSFetcher with config value
        fetcher = RSSFetcher(max_items=config.max_items)

        # Verify fetcher has the right value
        assert fetcher.max_items == 75

        # The real integration: fetcher will only fetch up to max_items
        # This is already tested in test_fetcher_integration.py

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)


def test_config_validation_prevents_invalid_fetcher() -> None:
    """Test that Config validation prevents creating fetcher with invalid max_items."""
    # Try to create config with invalid max_items
    config = Config(max_items=150)  # Too high

    try:
        config.validate()
        # Should not get here
        assert False, "Validation should have failed"
    except ValueError as e:
        # This is expected
        assert "max_items must be between 1 and 100" in str(e)

    # Can't create fetcher with invalid value
    # (in real code, config.validate() is called in from_file())
