"""Unit tests for Task 1.1: dual-service config foundation + migrate-config rename.

Invariants protected:
  INV-003: migrate-config renames service → light_service in [llm]; post-migration
           light_service present and ^service absent.

Success criteria covered:
  SC-9:  migrate-config renames config.toml and appends [services.prismis-openai-deep]
         to services.toml; idempotent re-run produces no changes.
  SC-14: Dual-service health check — deep failure is non-fatal (no sys.exit); light
         failure is fatal.
"""

import re
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# migrate_config() uses os.getenv("XDG_CONFIG_HOME") at call-time — isolate via env.
# Import the function directly; XDG override via monkeypatch.setenv.
from prismis_daemon.__main__ import (
    migrate_config,  # noqa: E402 (post-import is intentional)
)
from prismis_daemon.config import Config
from prismis_daemon.llm_validator import validate_llm_services

# Mock path for llm_core.health_check inside the validator module
_HEALTH_CHECK_MOCK = (
    "prismis_daemon.llm_validator.llm_core.health_check"  # claudex-guard: allow-mock
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

# Pre-llm-core: [llm] section uses provider/model/api_key (pre-task-3.1 format)
_PRE_LLM_CORE_CONFIG = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 1
max_days_lookback = 30

[llm]
provider = "openai"
model = "gpt-4.1-mini"
api_key = "sk-test-key-1234"

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

# Minimal services.toml with prismis-openai but not prismis-openai-deep
_SERVICES_TOML_NO_DEEP = """\
default_service = "prismis-openai"

[services.prismis-openai]
adapter = "openai"
key = "sable-openai"
base_url = "https://api.openai.com/v1"
default_model = "gpt-4.1-mini"
"""

# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_prismis_config_dir(tmp: Path, config_text: str) -> Path:
    """Create ~/.config/prismis/ layout under tmp, return config.toml path."""
    prismis_dir = tmp / "prismis"
    prismis_dir.mkdir(parents=True)
    config_path = prismis_dir / "config.toml"
    config_path.write_text(config_text)
    (prismis_dir / "context.md").write_text("# Test context")
    return config_path


def _make_services_toml(tmp: Path, content: str) -> Path:
    """Create ~/.config/llm-core/services.toml under tmp, return path."""
    llm_core_dir = tmp / "llm-core"
    llm_core_dir.mkdir(parents=True, exist_ok=True)
    services_path = llm_core_dir / "services.toml"
    services_path.write_text(content)
    return services_path


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


# ─── migrate_config: rename service → light_service ──────────────────────────


def test_migrate_config_renames_service_to_light_service(monkeypatch) -> None:
    """
    INVARIANT (INV-003 / SC-9): migrate-config changes `service =` → `light_service =`
    in the [llm] section; the resulting config.toml must be parseable by Config.from_file().
    BREAKS: Post-migration daemon still fails to start — config is corrupt or field absent.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _make_prismis_config_dir(tmp, _PRE_RENAME_CONFIG)
        _make_services_toml(tmp, _SERVICES_TOML_NO_DEEP)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        migrate_config()

        result_text = (tmp / "prismis" / "config.toml").read_text()

        # light_service must be present
        assert "light_service" in result_text, (
            "light_service must appear in config.toml after migrate-config"
        )
        # bare `service =` (start of line) must not remain in the [llm] block
        llm_block_match = re.search(
            r"(\[llm\].*?)(?=\n\[|\Z)", result_text, flags=re.DOTALL
        )
        assert llm_block_match, "Could not find [llm] section in migrated config"
        llm_block = llm_block_match.group(1)
        assert not re.search(r"(?m)^service\s*=", llm_block), (
            "Old `service =` key must be absent from [llm] block after migration"
        )


def test_migrate_config_result_parseable_by_config(monkeypatch) -> None:
    """
    INVARIANT (INV-003): After migrate-config, Config.from_file() must load successfully.
    BREAKS: Migration produces syntactically valid TOML but wrong field names → ValueError.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        config_path = _make_prismis_config_dir(tmp, _PRE_RENAME_CONFIG)
        _make_services_toml(tmp, _SERVICES_TOML_NO_DEEP)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        migrate_config()

        cfg = Config.from_file(config_path)
        assert cfg.llm_light_service == "prismis-openai"


def test_migrate_config_appends_deep_service_to_services_toml(monkeypatch) -> None:
    """
    INVARIANT (SC-9): migrate-config appends [services.prismis-openai-deep] with
    default_model = "gpt-5-mini" to services.toml.
    BREAKS: Deep service configured in config but missing from services.toml → runtime
    "Unknown service" error when deep extraction is attempted.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _make_prismis_config_dir(tmp, _PRE_RENAME_CONFIG)
        services_path = _make_services_toml(tmp, _SERVICES_TOML_NO_DEEP)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        migrate_config()

        services_text = services_path.read_text()
        assert "[services.prismis-openai-deep]" in services_text, (
            "[services.prismis-openai-deep] block must be appended to services.toml"
        )
        assert 'default_model = "gpt-5-mini"' in services_text, (
            "deep service must specify gpt-5-mini as default_model"
        )
        assert 'adapter = "openai"' in services_text
        assert 'key = "sable-openai"' in services_text


def test_migrate_config_idempotent_on_already_migrated_config(monkeypatch) -> None:
    """
    INVARIANT (SC-9): Re-running migrate-config on an already-migrated config must
    be a no-op — config.toml and services.toml unchanged.
    BREAKS: Second migrate-config run duplicates `light_service =` or appends a second
    [services.prismis-openai-deep] block, corrupting both files.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        # Start from already-migrated state (light_service present)
        _make_prismis_config_dir(tmp, _POST_RENAME_CONFIG)
        services_path = _make_services_toml(tmp, _SERVICES_TOML_NO_DEEP)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        config_before = (tmp / "prismis" / "config.toml").read_text()
        services_before = services_path.read_text()

        migrate_config()  # Re-run on already-migrated config

        config_after = (tmp / "prismis" / "config.toml").read_text()
        services_after = services_path.read_text()

        assert config_after == config_before, (
            "config.toml must not be modified on idempotent re-run"
        )
        assert services_after == services_before, (
            "services.toml must not be modified on idempotent re-run"
        )


def test_migrate_config_idempotent_deep_service_already_present(monkeypatch) -> None:
    """
    INVARIANT (SC-9): If [services.prismis-openai-deep] already exists in services.toml,
    migrate-config must not append a second copy.
    BREAKS: TOML parse error — duplicate section key crashes all subsequent llm-core calls.
    """
    deep_already_present = (
        _SERVICES_TOML_NO_DEEP
        + "\n[services.prismis-openai-deep]\n"
        + 'adapter = "openai"\n'
        + 'key = "sable-openai"\n'
        + 'base_url = "https://api.openai.com/v1"\n'
        + 'default_model = "gpt-5-mini"\n'
    )

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _make_prismis_config_dir(tmp, _PRE_RENAME_CONFIG)
        services_path = _make_services_toml(tmp, deep_already_present)

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        migrate_config()

        services_text = services_path.read_text()
        # Exactly one occurrence of the deep service header
        count = services_text.count("[services.prismis-openai-deep]")
        assert count == 1, (
            f"[services.prismis-openai-deep] must appear exactly once; found {count}"
        )


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
            raise Exception("Unknown service: prismis-openai-deep")

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
    with patch(_HEALTH_CHECK_MOCK, side_effect=Exception("Connection refused")):
        with pytest.raises(Exception, match="Connection refused"):
            validate_llm_services("prismis-openai", None)


# ─── Fix 3 reverify: pre-llm-core single-run convergence ────────────────────


def test_migrate_config_pre_llm_core_single_run_convergence(monkeypatch) -> None:
    """
    INVARIANT (Fix 3 / INV-003): A single migrate-config run on a pre-llm-core config
    (provider/model/api_key format) must produce a config.toml that:
      - has light_service = "prismis-openai" in [llm]
      - has no bare `provider =` key
      - loads via Config.from_file() without raising
    AND services.toml must have [services.prismis-openai-deep] after a single run.

    BREAKS: Before Fix 3 the pre-llm-core path wrote `service =` (intermediate invalid
    state) requiring a second migrate-config run. A single run left the config unloadable
    — daemon could not start until the user ran the command twice.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        _make_prismis_config_dir(tmp, _PRE_LLM_CORE_CONFIG)

        # services.toml must exist for the deep-entry append step (the pre-llm-core
        # branch creates it during migration; we let migrate_config do that work)
        # — no pre-existing services.toml needed; migrate_config creates it.

        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp))

        migrate_config()

        config_path = tmp / "prismis" / "config.toml"
        result_text = config_path.read_text()

        # 1. light_service must be present (not the intermediate `service =`)
        assert 'light_service = "prismis-openai"' in result_text, (
            "Pre-llm-core migrate-config must write light_service, not service"
        )

        # 2. provider key must be gone
        assert "provider" not in result_text, (
            "Old provider key must be absent after migration"
        )

        # 3. No bare `service =` in [llm] block (would make config unloadable)
        llm_match = re.search(r"(\[llm\].*?)(?=\n\[|\Z)", result_text, flags=re.DOTALL)
        assert llm_match, "Could not find [llm] section in migrated config"
        assert not re.search(r"(?m)^service\s*=", llm_match.group(1)), (
            "Bare `service =` must not appear in [llm] block after migration"
        )

        # 4. Config.from_file() must load without raising (single run → loadable)
        cfg = Config.from_file(config_path)
        assert cfg.llm_light_service == "prismis-openai"

        # 5. services.toml must have [services.prismis-openai-deep] after single run
        services_path = tmp / "llm-core" / "services.toml"
        assert services_path.exists(), "services.toml must be created by migrate_config"
        services_text = services_path.read_text()
        assert "[services.prismis-openai-deep]" in services_text, (
            "[services.prismis-openai-deep] must be appended in a single migrate-config run"
        )
        assert 'default_model = "gpt-5-mini"' in services_text
