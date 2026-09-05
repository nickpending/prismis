"""The ambient .env load must never return to module scope.

Both entry-point modules once did `load_dotenv($XDG_CONFIG_HOME/prismis/.env)` at import.
That executed during pytest collection, before any fixture could isolate it, so importing
the CLI injected whatever credentials happened to be on the developer's disk. It was
fixed by moving the load into `_load_ambient_env`, called from the Typer callback.

That fix had no test. It was verified once by hand and the work order recorded the manual
check as though it were a guard — moving the load back to module scope would have turned
nothing red. This is that guard.
"""

import importlib
import os
import subprocess
import sys
from pathlib import Path

PROBE = "PRISMIS_AMBIENT_ENV_PROBE"


def _xdg_with_env_file(tmp_path: Path) -> Path:
    cfg = tmp_path / "prismis"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / ".env").write_text(f"{PROBE}=loaded-from-dotenv\n")
    return tmp_path


def test_importing_daemon_main_does_not_load_the_ambient_env(tmp_path: Path) -> None:
    """Import must be inert. Run in a subprocess so a prior import cannot mask it."""
    script = (
        "import os, sys;"
        f"assert {PROBE!r} not in os.environ;"
        "import prismis_daemon.__main__;"
        f"sys.exit(0 if {PROBE!r} not in os.environ else 1)"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        env={**os.environ, "XDG_CONFIG_HOME": str(_xdg_with_env_file(tmp_path))},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        "importing prismis_daemon.__main__ loaded the ambient .env — the load has returned to "
        f"module scope. stderr: {result.stderr}"
    )


def test_daemon_entry_point_helper_does_load_the_ambient_env(
    tmp_path: Path, monkeypatch
) -> None:
    """The behaviour must survive the move: the helper still loads it."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(_xdg_with_env_file(tmp_path)))
    monkeypatch.delenv(PROBE, raising=False)

    module = importlib.import_module("prismis_daemon.__main__")
    assert PROBE not in os.environ, "import leaked before the helper was called"

    module._load_ambient_env()
    assert os.environ.get(PROBE) == "loaded-from-dotenv", (
        "_load_ambient_env no longer loads $XDG_CONFIG_HOME/prismis/.env"
    )
