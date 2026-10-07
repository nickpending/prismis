"""Tests for the Makefile install targets (python-314, SC-3).

Covers:
- No install recipe names a Python version literally; each takes it from its unit's
  .python-version, so moving the interpreter is a one-file change.
- Each install recipe exports its unit's uv.lock and installs with the result as
  constraints. `uv tool install` ignores uv.lock, so without the constraints a deploy
  resolves fresh and production runs versions the gate never tested.

Why these tests:
  The pre-change Makefile hardcoded `--python 3.13` in three recipes and installed without
  constraints. Both defects are silent: the install succeeds either way, and the drift only
  shows up as a production dependency the tests never saw.
"""

import re
import subprocess
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
_MAKEFILE = _PROJECT_ROOT / "Makefile"

# target -> unit directory whose lock and .python-version it must use
_TARGET_UNIT = {
    "install-daemon": "daemon",
    "install-cli": "cli",
}

_PY_VERSION_RE = re.compile(r"3\.1\d")


def _recipe(target: str) -> str:
    """Raw text of a target's recipe: the lines after `target:` up to the next target."""
    lines = _MAKEFILE.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(f"{target}:"))
    body: list[str] = []
    for line in lines[start + 1 :]:
        if line and not line.startswith(("\t", "#", " ")):
            break
        body.append(line)
    return "\n".join(body)


def _expanded(target: str) -> str:
    """The commands make would run for a target, variables expanded, nothing executed."""
    result = subprocess.run(
        ["make", "-n", target],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def _names_python_version(recipe: str) -> bool:
    return bool(_PY_VERSION_RE.search(recipe))


def _installs_under_lock_constraints(recipe: str) -> bool:
    return "uv export --locked" in recipe and "--constraints" in recipe


# The install-daemon recipe as it stood before python-314: a literal interpreter version
# and no lock export. The checks above must reject it, so they are shown red here on every
# run rather than once by hand.
_PRE_CHANGE_RECIPE = """\
\t@if ! uv python list 2>/dev/null | grep -q "cpython-3\\\\.13"; then \\\\
\t\tuv python install 3.13; \\\\
\tfi
\tcd daemon && uv tool install . --python 3.13 --reinstall
"""


def test_checks_reject_the_pre_change_recipe() -> None:
    assert _names_python_version(_PRE_CHANGE_RECIPE)
    assert not _installs_under_lock_constraints(_PRE_CHANGE_RECIPE)


@pytest.mark.parametrize(
    "target", ["install-daemon", "install-cli", "install-cli-local"]
)
def test_install_recipe_names_no_python_version(target: str) -> None:
    recipe = _recipe(target)
    assert recipe.strip(), f"no recipe found for {target}"
    assert not _names_python_version(recipe), (
        f"{target} names a Python version literally; read it from .python-version"
    )


@pytest.mark.parametrize("target", sorted(_TARGET_UNIT))
def test_install_recipe_installs_under_lock_constraints(target: str) -> None:
    assert _installs_under_lock_constraints(_recipe(target)), (
        f"{target} installs without exporting the unit's lock as constraints"
    )


def test_install_cli_local_installs_through_install_cli() -> None:
    # install-cli-local has no install of its own; it must reach the constrained one.
    assert "install-cli" in _recipe("install-cli-local")


@pytest.mark.parametrize(
    "target,unit",
    [
        ("install-daemon", "daemon"),
        ("install-cli", "cli"),
        ("install-cli-local", "cli"),
    ],
)
def test_install_python_comes_from_unit_python_version(target: str, unit: str) -> None:
    """Enters through `make`: the interpreter the install requests is the unit's file."""
    pinned = (_PROJECT_ROOT / unit / ".python-version").read_text().strip()
    commands = _expanded(target)
    requested = re.findall(r"uv tool install .*--python (\S+)", commands)
    assert requested == [pinned], commands
    assert "--constraints .tool-constraints.txt" in commands
