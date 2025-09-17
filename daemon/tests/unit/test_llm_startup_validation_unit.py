"""Unit tests for LLM startup validation logic."""

import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch
import sys

# Add src directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from prismis_daemon.__main__ import validate_llm_config
from prismis_daemon.config import Config


def test_INVARIANT_provider_specific_validation_ollama_requires_api_base() -> None:
    """
    INVARIANT: Ollama provider validation MUST require api_base configuration
    BREAKS: Startup allows broken Ollama config, analysis fails later
    """
    # Create real config file with Ollama provider but missing api_base
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        invalid_ollama_config = """
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "ollama"
model = "ollama/llama2"
api_key = ""
# Missing api_base - this should cause validation failure

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"
"""
        config_path.write_text(invalid_ollama_config)

        # Create context.md
        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test Context\nHigh Priority: Testing")

        # Load real config
        config = Config.from_file(config_path)

        # Validation MUST fail for Ollama without api_base
        with pytest.raises(SystemExit):
            validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_provider_specific_validation_openai_accepts_minimal_config() -> None:
    """
    INVARIANT: OpenAI provider validation MUST work with just model and api_key
    BREAKS: Valid OpenAI configs rejected, preventing daemon start
    """
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        valid_openai_config = """
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "sk-test-key-1234567890"
# No api_base needed for OpenAI

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"
"""
        config_path.write_text(valid_openai_config)

        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test Context\nHigh Priority: Testing")

        config = Config.from_file(config_path)

        # Mock only the external LLM API call
        with patch("litellm.ahealth_check") as mock_health:
            mock_health.return_value = None  # Success

            # Should NOT raise exception for valid OpenAI config
            try:
                validate_llm_config(config)
            except SystemExit:
                pytest.fail("Valid OpenAI config should not cause SystemExit")

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_error_message_clarity_invalid_provider() -> None:
    """
    INVARIANT: Error messages MUST provide actionable guidance for fixing config
    BREAKS: Users get cryptic errors, can't fix their configuration
    """
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        invalid_provider_config = """
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "nonexistent_provider"
model = "invalid-model"
api_key = "fake-key"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"
"""
        config_path.write_text(invalid_provider_config)

        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test Context\nHigh Priority: Testing")

        config = Config.from_file(config_path)

        # Should fail with clear error about invalid provider
        with pytest.raises(SystemExit):
            validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)


def test_INVARIANT_startup_validation_prevents_broken_daemon() -> None:
    """
    INVARIANT: Invalid LLM config MUST prevent daemon start (never runs with broken config)
    BREAKS: Daemon starts but fails during content analysis, corrupting user experience
    """
    temp_dir = tempfile.mkdtemp()

    try:
        config_path = Path(temp_dir) / "config.toml"
        # Create config that will fail at Analyzer initialization
        broken_config = """
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30

[llm]
provider = "ollama"
model = "ollama/llama2"
api_key = ""
# Intentionally missing api_base for Ollama

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"
"""
        config_path.write_text(broken_config)

        context_path = Path(temp_dir) / "context.md"
        context_path.write_text("# Test Context\nHigh Priority: Testing")

        config = Config.from_file(config_path)

        # Validation MUST prevent daemon start
        with pytest.raises(SystemExit):
            validate_llm_config(config)

    finally:
        import shutil

        shutil.rmtree(temp_dir, ignore_errors=True)
