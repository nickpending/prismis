"""Regression: in --json mode a command's error must reach stderr.

Before this change, list, search, get and statistics printed nothing and exited 1
in --json mode when they failed. A pipeline such as
`prismis-cli list ... --json | jq ...` then exited 0 with empty output, which looks
the same as "no results."

The failure is reproduced the way the issue observed it: no config file. The real
APIClient runs and fails on the real missing file, so there are no mocks.
"""

import pytest
import typer
from typer.testing import CliRunner

from cli.get import get as get_command
from cli.list import list as list_command
from cli.search import search as search_command
from cli.statistics import statistics as statistics_command


def _app(command):
    """Wrap one command as a Typer app, as the existing list tests do."""
    app = typer.Typer()
    app.command()(command)
    return app


CASES = [
    pytest.param(list_command, ["--json"], id="list"),
    pytest.param(search_command, ["term", "--json"], id="search"),
    pytest.param(
        get_command, ["00000000-0000-0000-0000-000000000000", "--json"], id="get"
    ),
    pytest.param(statistics_command, ["--json"], id="statistics"),
]


@pytest.mark.parametrize(("command", "args"), CASES)
def test_json_error_reaches_stderr_and_stdout_stays_empty(
    command, args, tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = CliRunner().invoke(_app(command), args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Config file not found" in result.stderr
