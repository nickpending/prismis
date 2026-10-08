"""A new user's path: first run, one environment variable, verify.

Invariant protected:
  - From an empty config home, the daemon's first run writes a config whose default
    service reaches an LLM once OPENROUTER_API_KEY is set, with only base_url pointed
    at the stub -- and nothing is read from the retired shared-config locations.

Success criteria covered:
  SC-1: the first-run walk, driven through `verify`'s real checks against the local
        LLM stub.
  SC-6 (code side): the first-run next-steps text names the variable and file the code
        reads.

The pre-change code fails this walk on the missing services file; that comparison was
made once by hand against the old tree and is not a standing test.

Real collaborators throughout: the real `ensure_config`, `Config.from_file`, the real
`verify` command function, real Storage. The LLM endpoint is the one collaborator the
constitution permits standing in for, and it is a real HTTP server.
"""

import os
import shutil
from pathlib import Path

import pytest

from prismis_daemon.__main__ import verify
from prismis_daemon.defaults import ensure_config
from prismis_daemon.storage import Storage

from conftest import strip_ansi

OPENROUTER_URL = "https://openrouter.ai/api/v1"


def _plant_retired_locations(*roots: Path) -> list[Path]:
    """Create the retired shared-config files unreadable (mode 000) under each root.

    Any code path that opens one raises PermissionError, so a read cannot hide behind
    a parse that happens to succeed or an error that happens to be swallowed.
    """
    planted = []
    for root in roots:
        for directory, name in (
            ("llm-core", "services.toml"),
            ("api" + "conf", "config.toml"),
        ):
            path = root / directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("[tripwire]\n")
            path.chmod(0)
            planted.append(path)
    return planted


def _seed_source() -> None:
    storage = Storage()
    storage.conn.execute(
        "INSERT INTO sources (url, type, name, active) VALUES (?, ?, ?, 1)",
        ("https://example.com/feed.xml", "rss", "Test Feed"),
    )
    storage.conn.commit()


def test_first_run_then_key_then_verify_reaches_the_llm(
    local_pipeline_stub: str,
    test_db: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """
    SC-1: empty config home -> first run writes the default config -> base_url pointed at
    the stub, OPENROUTER_API_KEY set -> verify's light service check passes.
    BREAKS: a fresh install following the first-run text never reaches an LLM: the
    default service is undefined, names a variable the code never reads, or the code
    reaches for a shared file nothing creates.
    """
    config_home = Path(os.environ["XDG_CONFIG_HOME"])
    home = Path(os.environ["HOME"])
    cfg_dir = config_home / "prismis"
    shutil.rmtree(cfg_dir)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    planted = _plant_retired_locations(config_home, home / ".config")

    try:
        assert ensure_config() is False, (
            "a first run reports that it created the config"
        )
        first_run_text = strip_ansi(capsys.readouterr().out)
        assert "OPENROUTER_API_KEY" in first_run_text
        assert "~/.config/prismis/.env" in first_run_text

        config_path = cfg_dir / "config.toml"
        written = config_path.read_text()
        assert f'base_url = "{OPENROUTER_URL}"' in written
        config_path.write_text(
            written.replace(OPENROUTER_URL, f"{local_pipeline_stub}/v1", 1)
        )
        _seed_source()

        # No key yet: verify fails, and the failure names the service and the variable
        # rather than pointing at a file that does not exist.
        with pytest.raises(SystemExit) as without_key:
            verify()
        assert without_key.value.code == 1
        failure = " ".join(strip_ansi(capsys.readouterr().out).split())
        assert "openrouter" in failure
        assert "OPENROUTER_API_KEY" in failure

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-first-run-test")
        with pytest.raises(SystemExit) as with_key:
            verify()
        out = " ".join(strip_ansi(capsys.readouterr().out).split())
        assert "light service reachable (openrouter)" in out, out
        assert with_key.value.code == 0, out
        assert "verify: PASS" in out
    finally:
        for path in planted:
            path.chmod(0o600)
