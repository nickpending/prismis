"""Integration test for Config → RSSFetcher flow."""

import tempfile
from pathlib import Path
import shutil

import pytest

from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML
from prismis_daemon.fetchers.rss import RSSFetcher
from conftest import TEST_API_KEY, make_config


def test_config_max_items_flows_to_fetcher() -> None:
    """Test that max_items from Config properly flows to RSSFetcher."""
    # Create a config file with custom max_items
    temp_dir = tempfile.mkdtemp()
    config_path = Path(temp_dir) / "config.toml"

    try:
        # Write config with max_items_rss = 75, rendered from the production template
        # so it stays complete against Config.from_file's required fields.
        test_toml = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY).replace(
            "max_items_rss = 25", "max_items_rss = 75"
        )
        config_path.write_text(test_toml)

        # Load config
        config = Config.from_file(config_path)

        # Verify config loaded the value
        assert config.max_items_rss == 75

        # Create RSSFetcher with config value
        fetcher = RSSFetcher(max_items=config.max_items_rss)

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
    config = make_config(max_items_rss=150)  # Too high

    with pytest.raises(ValueError, match="max_items_rss must be between 1 and 100"):
        config.validate()

    # Can't create fetcher with invalid value
    # (in real code, config.validate() is called in from_file())
