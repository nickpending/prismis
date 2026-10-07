"""Integration test for the deployed tools (python-314, SC-4).

`make install-daemon` and `make install-cli-local` run into a scratch UV_TOOL_DIR. Every
package the unit's lock names for this platform must be installed at the locked version,
the tool's interpreter must be the unit's .python-version, and the entry point must run.

Why this test:
  `uv tool install` ignores uv.lock, so an install that drops the lock constraints still
  succeeds -- it just resolves fresh. Only comparing the installed environment to the lock
  shows production runs the versions the gate tested. Needs the network for the apiconf
  git source, which uv fetches even when every wheel is cached.
"""

import os
import pwd
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from packaging.markers import Marker

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

# (make target, unit directory, tool name, uv export extra flags)
_CASES = [
    ("install-daemon", "daemon", "prismis-daemon", []),
    ("install-cli-local", "cli", "prismis-cli", ["--extra", "local"]),
]


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_pins(unit: str, extra: list[str]) -> dict[str, str]:
    """name -> version for every locked package whose marker holds on this platform."""
    out = subprocess.run(
        [
            "uv",
            "export",
            "--locked",
            "--no-emit-project",
            "--no-dev",
            "--no-hashes",
            "--no-header",
            *extra,
        ],
        cwd=_PROJECT_ROOT / unit,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    pins: dict[str, str] = {}
    for line in out.splitlines():
        m = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)(?: ; (.+))?$", line)
        if not m:
            continue
        name, version, marker = m.groups()
        if marker and not Marker(marker).evaluate():
            continue
        pins[_norm(name)] = version
    return pins


def _real_home_env(tmp_path: Path) -> dict[str, str]:
    # conftest seals HOME and XDG_* into a temp dir; uv would find neither its managed
    # Pythons nor its wheel cache there, so hand it the account's real home back.
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("XDG_")},
        "HOME": pwd.getpwuid(os.getuid()).pw_dir,
        "UV_TOOL_DIR": str(tmp_path / "tools"),
        "UV_TOOL_BIN_DIR": str(tmp_path / "bin"),
    }


def test_constraints_override_a_fresh_resolve(tmp_path: Path) -> None:
    """The lock, not a fresh resolve, decides the installed version.

    A scratch copy of the daemon unit has `six` locked one release behind what an
    unconstrained resolve picks. Installing through the Makefile must land the locked
    older version; without `--constraints` it lands the newest.
    """
    root = tmp_path / "repo"
    ignore = shutil.ignore_patterns(
        ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", "tests"
    )
    root.mkdir()
    shutil.copy(_PROJECT_ROOT / "Makefile", root / "Makefile")
    shutil.copytree(_PROJECT_ROOT / "daemon", root / "daemon", ignore=ignore)
    (root / "cli").mkdir()
    shutil.copy(_PROJECT_ROOT / "cli" / ".python-version", root / "cli")

    env = _real_home_env(tmp_path)
    older = "1.16.0"
    locked = subprocess.run(
        ["uv", "lock", "--upgrade-package", f"six=={older}"],
        cwd=root / "daemon",
        env=env,
        capture_output=True,
        text=True,
    )
    assert locked.returncode == 0, locked.stderr
    assert f"six=={older}" in _export_text(root / "daemon", env)

    made = subprocess.run(
        ["make", "install-daemon"], cwd=root, env=env, capture_output=True, text=True
    )
    assert made.returncode == 0, made.stdout[-2000:] + made.stderr[-2000:]
    listed = subprocess.run(
        [
            "uv",
            "pip",
            "list",
            "--format",
            "freeze",
            "--python",
            str(tmp_path / "tools" / "prismis-daemon" / "bin" / "python"),
        ],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    assert f"six=={older}" in listed.splitlines()


def _export_text(unit_dir: Path, env: dict[str, str]) -> str:
    return subprocess.run(
        ["uv", "export", "--locked", "--no-emit-project", "--no-hashes", "--no-header"],
        cwd=unit_dir,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout


@pytest.mark.parametrize("target,unit,tool,extra", _CASES)
def test_make_install_matches_lock(
    target: str, unit: str, tool: str, extra: list[str], tmp_path: Path
) -> None:
    env = _real_home_env(tmp_path)
    made = subprocess.run(
        ["make", target], cwd=_PROJECT_ROOT, env=env, capture_output=True, text=True
    )
    assert made.returncode == 0, made.stdout[-2000:] + made.stderr[-2000:]

    tool_python = tmp_path / "tools" / tool / "bin" / "python"
    wanted = (_PROJECT_ROOT / unit / ".python-version").read_text().strip()
    version = subprocess.run(
        [str(tool_python), "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert version.startswith(wanted + "."), version

    listed = subprocess.run(
        ["uv", "pip", "list", "--python", str(tool_python), "--format", "freeze"],
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout
    installed = {
        _norm(n): v
        for n, v in (ln.split("==") for ln in listed.splitlines() if "==" in ln)
    }
    mismatched = {
        name: (want, installed.get(name))
        for name, want in _locked_pins(unit, extra).items()
        if installed.get(name) != want
    }
    assert not mismatched, mismatched

    helped = subprocess.run(
        [str(tmp_path / "bin" / tool), "--help"], capture_output=True, text=True
    )
    assert helped.returncode == 0, helped.stderr
