"""Unit tests for Task 1.1: dual-service config foundation + migrate-config rename.

Invariants protected:
  INV-003: migrate-config renames service → light_service in [llm]; post-migration
           light_service present and ^service absent.

Success criteria covered:
  SC-14: Dual-service health check — deep failure is non-fatal (no sys.exit); light
         failure is fatal.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import openai
import pytest

from prismis_daemon import llm_client
from prismis_daemon.config import Config
from prismis_daemon.llm_validator import validate_llm_services

from conftest import unreachable_service_error

# Mock path for llm_client.health_check inside the validator module
_HEALTH_CHECK_MOCK = (
    "prismis_daemon.llm_validator.llm_client.health_check"  # claudex-guard: allow-mock
)

# ─── TOML fixtures ────────────────────────────────────────────────────────────

# Pre-migration: [llm] section uses old `service =` key (post-llm-core, pre-task-1.1)
_PRE_RENAME_CONFIG = """\
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

# Post-migration: [llm] section uses new `light_service =` key
_POST_RENAME_CONFIG = _PRE_RENAME_CONFIG.replace(
    "service = ",
    "light_service = ",
    1,  # replace first occurrence only
)

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_prismis_config_dir(tmp: Path, config_text: str) -> Path:
    """Create ~/.config/prismis/ layout under tmp, return config.toml path."""
    prismis_dir = tmp / "prismis"
    prismis_dir.mkdir(parents=True)
    config_path = prismis_dir / "config.toml"
    config_path.write_text(config_text)
    (prismis_dir / "context.md").write_text("# Test context")
    return config_path



# ─── Config dataclass: new fields present ────────────────────────────────────


def test_config_loads_llm_light_service() -> None:
    """
    INVARIANT (INV-003): Config.from_file() maps light_service → llm_light_service.
    BREAKS: Daemon boots with None service name, all LLM calls silently fail.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _make_prismis_config_dir(Path(tmpdir), _POST_RENAME_CONFIG)
        cfg = Config.from_file(config_path)
        assert cfg.llm_light_service == "prismis-openai"


def test_config_deep_service_defaults_none() -> None:
    """
    INVARIANT (SC-14): When deep_service absent from config, llm_deep_service is None.
    BREAKS: Validator attempts health_check(service=None), crashing on startup.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _make_prismis_config_dir(Path(tmpdir), _POST_RENAME_CONFIG)
        cfg = Config.from_file(config_path)
        assert cfg.llm_deep_service is None


def test_config_auto_extract_defaults_none() -> None:
    """
    INVARIANT (SC-9 context): auto_extract defaults to "none" — no unintended deep calls.
    BREAKS: All content silently sent to gpt-5-mini tier before operator opts in.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _make_prismis_config_dir(Path(tmpdir), _POST_RENAME_CONFIG)
        cfg = Config.from_file(config_path)
        assert cfg.auto_extract == "none"


def test_config_old_service_key_rejected() -> None:
    """
    INVARIANT (INV-003): Config with old `service =` key in [llm] must raise with
    migrate-config hint — not silently load the wrong field.
    BREAKS: Daemon boots thinking llm_light_service is set but it isn't; AttributeError
    on first LLM call.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = _make_prismis_config_dir(Path(tmpdir), _PRE_RENAME_CONFIG)
        with pytest.raises(ValueError, match="migrate-config"):
            Config.from_file(config_path)


# migrate-config is covered by test_migrate_config_unit.py.


# ─── validate_llm_services: light fatal, deep non-fatal ──────────────────────


def test_validate_llm_services_light_ok_deep_none() -> None:
    """
    INVARIANT (SC-14): When deep_service is None, validate_llm_services returns
    {'light': 'ok', 'deep': 'not_configured'} — no health_check called for deep.
    BREAKS: Validator calls health_check(service=None) → crash on every startup where
    deep extraction is disabled.
    """
    with patch(_HEALTH_CHECK_MOCK) as mock_hc:
        mock_hc.return_value = None
        result = validate_llm_services("prismis-openai", None)

    assert result["light"] == "ok"
    assert result["deep"] == "not_configured"
    mock_hc.assert_called_once_with(service="prismis-openai")


def test_validate_llm_services_light_ok_deep_ok() -> None:
    """
    INVARIANT (SC-14): Both services reachable → {'light': 'ok', 'deep': 'ok'}.
    BREAKS: Return dict missing 'deep' key — callers crash with KeyError when reading
    dual-service status for startup output.
    """
    with patch(_HEALTH_CHECK_MOCK) as mock_hc:
        mock_hc.return_value = None
        result = validate_llm_services("prismis-openai", "prismis-openai-deep")

    assert result["light"] == "ok"
    assert result["deep"] == "ok"


def test_validate_llm_services_deep_failure_is_non_fatal() -> None:
    """
    INVARIANT (SC-14): Deep service health_check failure must NOT raise — returns
    {'deep': 'unreachable'} instead.
    BREAKS: Daemon cannot start when gpt-5-mini tier is unreachable; violates the
    graceful-degradation contract ("deep extraction will be disabled at runtime").
    """
    calls = []

    def _side_effect(service: str) -> None:
        calls.append(service)
        if service == "prismis-openai-deep":
            raise llm_client.ConfigError("Unknown service: prismis-openai-deep")

    with patch(_HEALTH_CHECK_MOCK, side_effect=_side_effect):
        result = validate_llm_services("prismis-openai", "prismis-openai-deep")

    assert result["light"] == "ok", "light must be ok when it succeeds"
    assert result["deep"] == "unreachable", (
        "deep failure must yield 'unreachable', not propagate an exception"
    )
    assert calls == ["prismis-openai", "prismis-openai-deep"], (
        "health_check must be called for both services in order"
    )


def test_validate_llm_services_light_failure_raises() -> None:
    """
    INVARIANT (SC-14): Light service health_check failure MUST propagate — caller
    (validate_llm_config in __main__.py) catches it and calls sys.exit(1).
    BREAKS: Daemon starts with a broken light service and silently fails on every
    summarization / evaluation call.
    """
    with patch(_HEALTH_CHECK_MOCK, side_effect=unreachable_service_error("Connection refused")):
        with pytest.raises(openai.APIConnectionError, match="Connection refused"):
            validate_llm_services("prismis-openai", None)
