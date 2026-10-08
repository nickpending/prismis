"""The three places a newcomer is told how to supply the LLM key agree with the code.

Success criteria covered:
  SC-6: the README Quick Start, the .env template `make install-config` writes and the
        first-run next-steps text each name the key variable the default config reads
        and the file the daemon loads it from.

The variable is derived from defaults.py (the default service's `api_key`), not typed
into this test, so changing the default without changing the docs fails here. The file
is checked against what the daemon really loads: `_load_ambient_env` is run on a planted
.env and the variable must arrive in the process environment.
"""

import os
import re
import tomllib
from pathlib import Path

import pytest

from prismis_daemon.__main__ import _load_ambient_env
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, ensure_config

_REPO = Path(__file__).parent.parent.parent.parent
_ENV_FILE = "~/.config/prismis/.env"


def _default_key_variable() -> str:
    parsed = tomllib.loads(DEFAULT_CONFIG_TOML.format(api_key="k"))
    light = parsed["services"][parsed["llm"]["light_service"]]
    assert light["api_key"].startswith("env:"), light
    return light["api_key"][len("env:") :]


def test_default_config_variable_is_the_one_the_docs_promise() -> None:
    """
    SC-6 anchor: the default light service is keyed by OPENROUTER_API_KEY.
    BREAKS: the docs below are checked against a variable nobody agreed on.
    """
    assert _default_key_variable() == "OPENROUTER_API_KEY"


def test_readme_quick_start_names_the_variable_and_file() -> None:
    """
    SC-6: the Quick Start section tells the reader to set the default's variable in the
    file the daemon loads.
    BREAKS: a newcomer following the README sets a variable the code never reads.
    """
    readme = (_REPO / "README.md").read_text()
    start = readme.index("## 🎬 Quick Start")
    section = readme[start : readme.index("\n## ", start + 1)]
    assert _default_key_variable() in section
    assert _ENV_FILE in section
    assert "OPENAI_API_KEY" not in section


def test_make_install_config_env_template_assigns_the_variable() -> None:
    """
    SC-6: the .env template assigns the default's variable on a line of its own, and
    its directory is the one the daemon loads.
    BREAKS: `make install` writes a template that fills in the wrong variable.
    """
    makefile = (_REPO / "Makefile").read_text()
    assert re.search(r"^XDG_CONFIG_HOME \?= \$\(HOME\)/\.config$", makefile, re.M)
    assert re.search(r"^CONFIG_DIR := \$\(XDG_CONFIG_HOME\)/prismis$", makefile, re.M)
    recipe_start = makefile.index("install-config:")
    recipe = makefile[recipe_start : makefile.index("\n.PHONY", recipe_start)]
    assert f"'{_default_key_variable()}=" in recipe
    assert "OPENAI_API_KEY=" not in recipe


def test_first_run_next_steps_name_the_variable_and_file(
    isolated_xdg_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    SC-6: the text a first run prints names the variable and the file.
    BREAKS: the first thing a new user reads sends them to a file or variable the code
    does not read.
    """
    for child in isolated_xdg_env.iterdir():
        child.unlink()

    assert ensure_config() is False

    text = capsys.readouterr().out
    assert _default_key_variable() in text
    assert _ENV_FILE in text
    assert "services.toml" not in text


_BOOTSTRAP_COMMAND = "prismis-cli context bootstrap"
_CONTEXT_FILE = "~/.config/prismis/context.md"


def test_first_run_next_steps_point_at_context_bootstrap(
    isolated_xdg_env: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """
    SC-5 (context-bootstrap): the first-run text names the bootstrap command and where
    to save its result, and no longer says to hand-customize the sample.
    BREAKS: a new user is told to hand-write a context.md instead of generating one.
    """
    for child in isolated_xdg_env.iterdir():
        child.unlink()

    assert ensure_config() is False

    text = capsys.readouterr().out
    assert _BOOTSTRAP_COMMAND in text
    assert _CONTEXT_FILE in text
    assert "Optionally customize context.md" not in text


def test_readme_quick_start_points_at_context_bootstrap() -> None:
    """
    SC-5 (context-bootstrap): the Quick Start runs the bootstrap command, names the
    save path, and no longer carries a hand-written context.md heredoc.
    BREAKS: the README sends a new user back to writing the sample by hand.
    """
    readme = (_REPO / "README.md").read_text()

    assert _BOOTSTRAP_COMMAND in readme
    assert _CONTEXT_FILE in readme
    assert "cat > ~/.config/prismis/context.md" not in readme


def test_the_daemon_loads_the_named_variable_from_the_named_file(
    isolated_xdg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    SC-6: the file the docs name is the file the daemon loads at startup, and a variable
    already in the environment wins over it.
    BREAKS: the docs and the code agree on a name but the daemon never reads the file.
    """
    var = _default_key_variable()
    monkeypatch.delenv(var, raising=False)
    (isolated_xdg_env / ".env").write_text(f"{var}=from-dotenv\n")

    _load_ambient_env()
    assert os.environ[var] == "from-dotenv"

    monkeypatch.setenv(var, "from-environment")
    _load_ambient_env()
    assert os.environ[var] == "from-environment"
