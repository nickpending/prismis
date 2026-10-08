"""Tests for the Python 3.14 move (python-314, SC-1 and SC-2).

Covers:
- SC-1: both units' .python-version read 3.14, both mypy python_version settings read
  "3.14", and both requires-python stay >=3.13.
- SC-2: both uv.lock files are current with their pyproject.toml, and every registry
  package in them installs from a wheel on 3.14 -- a package that ships interpreter-
  specific wheels must ship a cp314 (or abi3) one, so no PyPI package falls back to a
  source build. The project itself builds from source by
  nature and is not a registry package.

Why these tests:
  The old locks pinned lxml, markupsafe, pydantic-core and pyyaml at versions with no
  3.14 wheel; the failure only appeared when a host tried to install. A later
  `uv lock` that regresses one package reintroduces it silently.
"""

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_UNITS = ["cli", "daemon"]

# cpXYZ-cpXYZ interpreter-specific wheel, or an abi3 wheel that covers 3.14.
_CP_SPECIFIC_RE = re.compile(r"-(cp3\d+)-(cp3\d+t?|abi3)-")
_ABI3_RE = re.compile(r"-abi3-")


def _pyproject(unit: str) -> dict:  # type: ignore[type-arg]
    return tomllib.loads((_PROJECT_ROOT / unit / "pyproject.toml").read_text())


@pytest.mark.parametrize("unit", _UNITS)
def test_python_version_file_is_314(unit: str) -> None:
    assert (_PROJECT_ROOT / unit / ".python-version").read_text().strip() == "3.14"


@pytest.mark.parametrize("unit", _UNITS)
def test_mypy_python_version_is_314(unit: str) -> None:
    assert _pyproject(unit)["tool"]["mypy"]["python_version"] == "3.14"


@pytest.mark.parametrize("unit", _UNITS)
def test_requires_python_floor_stays_313(unit: str) -> None:
    assert _pyproject(unit)["project"]["requires-python"] == ">=3.13"


@pytest.mark.parametrize("unit", _UNITS)
def test_lock_is_current_with_pyproject(unit: str) -> None:
    result = subprocess.run(
        ["uv", "lock", "--check", "--offline"],
        cwd=_PROJECT_ROOT / unit,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("unit", _UNITS)
def test_every_registry_package_has_a_314_wheel(unit: str) -> None:
    lock = tomllib.loads((_PROJECT_ROOT / unit / "uv.lock").read_text())
    lacking: list[str] = []
    for pkg in lock["package"]:
        if "registry" not in pkg["source"]:
            continue
        wheels = [w["url"].rsplit("/", 1)[-1] for w in pkg.get("wheels", [])]
        if not wheels:
            lacking.append(f"{pkg['name']} {pkg['version']}: sdist only")
            continue
        specific = [w for w in wheels if _CP_SPECIFIC_RE.search(w)]
        if not specific:
            continue  # pure-python / py3-none wheels run on any interpreter
        if not any("-cp314-" in w or _ABI3_RE.search(w) for w in specific):
            lacking.append(f"{pkg['name']} {pkg['version']}: no cp314 or abi3 wheel")
    assert not lacking, lacking
