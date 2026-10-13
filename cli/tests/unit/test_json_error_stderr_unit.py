"""Regression: in --json mode a command's error must reach stderr.

Before this change, list, search, get and statistics printed nothing and exited 1
in --json mode when they failed. A pipeline such as
`prismis-cli list ... --json | jq ...` then exited 0 with empty output, which looks
the same as "no results."

The failure is reproduced the way the issue observed it: no config file. The real
APIClient runs and fails on the real missing file, so there are no mocks.

Each error is reported once. click's Exit subclasses RuntimeError, so a command
that raises typer.Exit(1) inside a try with `except RuntimeError` (or `except
Exception`) caught its own exit and reported it again as "Error: 1".
"""

import pytest
import typer
from typer.testing import CliRunner

from cli.get import get as get_command
from cli.list import list as list_command
from cli.search import search as search_command
from cli.source import add as source_add_command
from cli.source import list_sources as source_list_command
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
    pytest.param(
        source_add_command, ["https://example.com/feed.xml", "--json"], id="source-add"
    ),
    pytest.param(source_list_command, ["--json"], id="source-list"),
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
    assert result.stderr.count("Error:") == 1


# A config with a key gets each command past APIClient() to its own flag
# validation, which raises typer.Exit(1) inside the command's try block.
VALIDATION_CASES = [
    pytest.param(
        list_command,
        ["--archived", "--include-archived", "--json"],
        "Cannot use --archived and --include-archived together",
        id="list",
    ),
    pytest.param(
        search_command,
        ["term", "--limit", "99", "--json"],
        "Limit must be between 1 and 50",
        id="search",
    ),
    pytest.param(
        get_command,
        ["00000000-0000-0000-0000-000000000000", "--raw", "--json"],
        "Cannot use --raw and --json together",
        id="get",
    ),
]


@pytest.mark.parametrize(("command", "args", "message"), VALIDATION_CASES)
def test_json_validation_error_is_reported_once_on_stderr(
    command, args, message, tmp_path, monkeypatch
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    (tmp_path / "prismis").mkdir()
    (tmp_path / "prismis" / "config.toml").write_text('[api]\nkey = "test-key"\n')

    result = CliRunner().invoke(_app(command), args)

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr == f"Error: {message}\n"
