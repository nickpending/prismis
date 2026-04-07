"""Unit tests for host configuration security - protecting defaults and validation."""

import tempfile
from pathlib import Path

import pytest

from prismis_daemon.config import Config

# Minimal valid new-format TOML (service= instead of provider=/model=/api_key=).
# All mandatory sections present; missing any key raises ValueError.
_BASE_TOML = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30

[llm]
service = "prismis-openai"

[reddit]
client_id = "test-id"
client_secret = "test-secret"
user_agent = "test-agent"
max_comments = 5

[notifications]
high_priority_only = true
command = "test-command"

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


def _write_config(tmpdir: str, content: str) -> Path:
    config_path = Path(tmpdir) / "config.toml"
    config_path.write_text(content)
    (Path(tmpdir) / "context.md").write_text("# Test context")
    return config_path


def test_host_binding_security_default() -> None:
    """
    INVARIANT: Security by Default - api_host defaults to localhost-only
    BREAKS: Accidental LAN exposure if users don't explicitly opt-in
    """
    # No host= in [api] section — must default to 127.0.0.1
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, _BASE_TOML)
        config = Config.from_file(config_path)

        assert config.api_host == "127.0.0.1", (
            f"Default host should be localhost, got {config.api_host}"
        )


def test_host_config_explicit_values() -> None:
    """
    INVARIANT: Host Binding Correct - config.api_host properly loads explicit values
    BREAKS: LAN access doesn't work when user configures it
    """
    # Test two different explicit host values to confirm round-trip loading
    toml_lan_ip = _BASE_TOML.replace(
        'key = "test-api-key"', 'key = "test-api-key"\nhost = "10.0.0.1"'
    )
    toml_specific_ip = _BASE_TOML.replace(
        'key = "test-api-key"', 'key = "test-api-key"\nhost = "192.168.1.100"'
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, toml_lan_ip)
        config = Config.from_file(config_path)
        assert config.api_host == "10.0.0.1", (
            f"Should load 10.0.0.1, got {config.api_host}"
        )

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, toml_specific_ip)
        config = Config.from_file(config_path)
        assert config.api_host == "192.168.1.100", (
            f"Should load specific IP, got {config.api_host}"
        )


def test_malformed_host_config_handling() -> None:
    """
    FAILURE: Malformed config.toml with host field issues
    GRACEFUL: System must handle gracefully, not crash
    """
    # Missing [api] section entirely — api.key is required, must raise ValueError
    toml_missing_api = "\n".join(
        line
        for line in _BASE_TOML.splitlines()
        if not line.startswith("[api]") and not line.startswith("key =")
    )

    # Malformed TOML (host = with no value) — tomllib will reject at parse time
    toml_malformed = _BASE_TOML + "\n[extra]\nbad_key =\n"

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, toml_missing_api)
        with pytest.raises(ValueError, match="API key not configured"):
            Config.from_file(config_path)

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _write_config(tmpdir, toml_malformed)
        with pytest.raises(ValueError, match="Failed to parse config file"):
            Config.from_file(config_path)
