"""Unit tests for Task 4.1: prismis-daemon verify subcommand.

Invariants protected:
  - Config failure causes immediate sys.exit(1) -- no further checks run
  - Light service unreachable -> FAIL rollup (failures > 0, exit 1)
  - Deep service None -> info only, NOT a failure (exit 0 with other checks passing)
  - Deep service configured but unreachable -> FAIL rollup (exit 1)
  - Zero active sources -> FAIL rollup (exit 1)
  - All checks pass -> exit 0

Success criteria covered:
  SC-15: verify command
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from prismis_daemon.__main__ import verify
from prismis_daemon.storage import Storage

# External API boundary -- llm_core.health_check is a third-party network call
_HEALTH_CHECK_MOCK = "llm_core.health_check"  # claudex-guard: allow-mock

# --- TOML fixtures ------------------------------------------------------------

_LIGHT_ONLY_CONFIG = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
light_service = "prismis-openai"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"
max_comments = 100

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false

[archival.windows]
high_read = 30
medium_unread = 14
medium_read = 30
low_unread = 7
low_read = 30

[context]
auto_update_enabled = false
auto_update_interval_days = 7
auto_update_min_votes = 5
backup_count = 3
"""

_DUAL_SERVICE_CONFIG = """\
[daemon]
fetch_interval = 30
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_items_file = 5
max_days_lookback = 30

[llm]
light_service = "prismis-openai"
deep_service = "prismis-openai-deep"

[reddit]
client_id = "env:REDDIT_CLIENT_ID"
client_secret = "env:REDDIT_CLIENT_SECRET"
user_agent = "test"
max_comments = 100

[notifications]
high_priority_only = true
command = "echo"

[api]
key = "test-api-key"

[archival]
enabled = false

[archival.windows]
high_read = 30
medium_unread = 14
medium_read = 30
low_unread = 7
low_read = 30

[context]
auto_update_enabled = false
auto_update_interval_days = 7
auto_update_min_votes = 5
backup_count = 3
"""

# --- Helpers ------------------------------------------------------------------


def _make_config_dir(config_text: str) -> tuple[Path, Path]:
    """Create a temp prismis config directory. Returns (temp_dir, config_path)."""
    temp_dir = Path(tempfile.mkdtemp())
    prismis_dir = temp_dir / "prismis"
    prismis_dir.mkdir(parents=True)
    config_path = prismis_dir / "config.toml"
    config_path.write_text(config_text)
    (prismis_dir / "context.md").write_text("# Test context")
    return temp_dir, config_path


def _seed_source(test_db: Path) -> None:
    """Add one active source to the isolated test database."""
    storage = Storage()
    storage.conn.execute(
        "INSERT INTO sources (url, type, name, active) VALUES (?, ?, ?, 1)",
        ("https://example.com/feed.xml", "rss", "Test Feed"),
    )
    storage.conn.commit()


# --- Tests --------------------------------------------------------------------


def test_verify_config_failure_exits_1_immediately(monkeypatch, test_db) -> None:
    """
    INVARIANT: Missing/invalid config.toml causes immediate sys.exit(1).
    verify() must not proceed to service checks when config fails.
    BREAKS: Confusing NoneType errors on health_check(service=None) instead of
    clear config-missing message.
    """
    # Point XDG_CONFIG_HOME at empty dir -- no config.toml exists
    tmpdir = Path(tempfile.mkdtemp())
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))

    # verify() must sys.exit(1) immediately on config failure
    with pytest.raises(SystemExit) as exc_info:
        verify()

    assert exc_info.value.code == 1


def test_verify_happy_path_light_only_exits_0(monkeypatch, test_db) -> None:
    """
    SC-15: All checks pass (light-only config, sources present) -> exit 0.
    BREAKS: Operator gets false FAIL on a healthy install, loses confidence in verify.
    """
    tmpdir, _ = _make_config_dir(_LIGHT_ONLY_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    _seed_source(test_db)

    try:
        with patch(_HEALTH_CHECK_MOCK) as mock_hc:
            mock_hc.return_value = None  # light service reachable
            with pytest.raises(SystemExit) as exc_info:
                verify()

        assert exc_info.value.code == 0, (
            "verify must exit 0 when config valid, light service reachable, "
            "deep=None (info), and sources present"
        )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_light_service_unreachable_exits_1(monkeypatch, test_db) -> None:
    """
    INVARIANT: Light service unreachable -> FAIL rollup -> exit 1.
    BREAKS: Operator sees PASS on a broken install; first fetch cycle silently fails.
    """
    tmpdir, _ = _make_config_dir(_LIGHT_ONLY_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    _seed_source(test_db)

    try:
        with patch(_HEALTH_CHECK_MOCK) as mock_hc:
            mock_hc.side_effect = Exception("Connection refused")
            with pytest.raises(SystemExit) as exc_info:
                verify()

        assert exc_info.value.code == 1
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_deep_service_none_is_not_a_failure(monkeypatch, test_db) -> None:
    """
    INVARIANT: Deep service None -> info message only, NOT counted as failure.
    verify exits 0 when config valid, light reachable, deep=None, sources present.
    BREAKS: Operators without deep extraction get spurious FAIL on every verify run.
    """
    tmpdir, _ = _make_config_dir(_LIGHT_ONLY_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    _seed_source(test_db)

    try:
        with patch(_HEALTH_CHECK_MOCK) as mock_hc:
            mock_hc.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                verify()

        # deep=None must NOT contribute to failures -- exit 0
        assert exc_info.value.code == 0
        # health_check called exactly once (light only, not deep)
        assert mock_hc.call_count == 1
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_deep_service_configured_but_unreachable_exits_1(
    monkeypatch, test_db
) -> None:
    """
    INVARIANT: Deep service configured but unreachable -> FAIL rollup -> exit 1.
    (verify has stronger semantics than startup: deep failure IS a FAIL here.)
    BREAKS: Misconfigured deep service goes undetected; deep extraction silently fails
    when triggered.
    """
    tmpdir, _ = _make_config_dir(_DUAL_SERVICE_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    _seed_source(test_db)

    try:
        calls = []

        def _side_effect(service: str) -> None:
            calls.append(service)
            if service == "prismis-openai-deep":
                raise Exception("Unknown service: prismis-openai-deep")

        with patch(_HEALTH_CHECK_MOCK, side_effect=_side_effect):
            with pytest.raises(SystemExit) as exc_info:
                verify()

        assert exc_info.value.code == 1, (
            "Deep service failure must cause FAIL rollup (exit 1) in verify -- "
            "stricter than startup graceful-degradation"
        )
        assert "prismis-openai" in calls
        assert "prismis-openai-deep" in calls
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_zero_active_sources_exits_1(monkeypatch, test_db) -> None:
    """
    INVARIANT: No active sources after fresh install -> FAIL rollup -> exit 1.
    BREAKS: Daemon appears healthy but will never fetch content -- silent no-op.
    """
    tmpdir, _ = _make_config_dir(_LIGHT_ONLY_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    # test_db is empty -- no sources seeded

    try:
        with patch(_HEALTH_CHECK_MOCK) as mock_hc:
            mock_hc.return_value = None
            with pytest.raises(SystemExit) as exc_info:
                verify()

        assert exc_info.value.code == 1, (
            "Zero active sources must cause FAIL rollup -- operator must add sources "
            "before daemon is considered ready"
        )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

def test_verify_continues_all_checks_after_light_failure(monkeypatch, test_db) -> None:
    """
    INVARIANT (discovered, NOT in task file): Light service failure must NOT
    short-circuit -- verify() must still run the sources check so operators
    see all problems in one pass.
    BREAKS: If light failure caused an early sys.exit(1), the sources-missing problem
    would be invisible. Operator fixes light service, re-runs verify, THEN discovers
    the sources problem -- two separate debug cycles for a problem visible in one.
    This invariant is not mentioned in Task 4.1 Test Considerations or the build report.
    """
    tmpdir, _ = _make_config_dir(_LIGHT_ONLY_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    # Empty database -- zero sources -- so sources check will ALSO fail
    # If the function short-circuited on light failure, sources check would not run

    sources_check_ran = [False]
    real_get_active_sources = Storage.get_active_sources

    def _patched_get_active_sources(self):
        sources_check_ran[0] = True
        return real_get_active_sources(self)

    try:
        with patch(_HEALTH_CHECK_MOCK, side_effect=Exception("Connection refused")):
            with patch.object(Storage, "get_active_sources", _patched_get_active_sources):
                with pytest.raises(SystemExit):
                    verify()

        assert sources_check_ran[0], (
            "Sources check must run even when light service fails -- "
            "verify must not short-circuit on light failure"
        )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)


def test_verify_deep_check_uses_deep_service_name_not_light(monkeypatch, test_db) -> None:
    """
    INVARIANT (discovered, NOT in task file): When deep service is configured,
    health_check must be called with config.llm_deep_service, NOT config.llm_light_service.
    BREAKS: Copy-paste bug would call health_check(service=config.llm_light_service)
    for the deep check -- the deep service stays misconfigured silently, verify passes
    because the light service is up, and deep extraction fails at runtime.
    This invariant is not mentioned in Task 4.1 Test Considerations or the build report.
    """
    tmpdir, _ = _make_config_dir(_DUAL_SERVICE_CONFIG)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmpdir))
    _seed_source(test_db)

    calls = []

    def _capture(service: str) -> None:
        calls.append(service)

    try:
        with patch(_HEALTH_CHECK_MOCK, side_effect=_capture):
            with pytest.raises(SystemExit):
                verify()

        assert len(calls) == 2, f"Expected 2 health_check calls, got {calls}"
        assert calls[0] == "prismis-openai", (
            f"First call must use light service name; got {calls[0]}"
        )
        assert calls[1] == "prismis-openai-deep", (
            f"Second call must use deep service name, not light; got {calls[1]}"
        )
    finally:
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)
