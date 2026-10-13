"""Regression: a command's own typer.Exit or typer.Abort is not reported a second time.

click's Exit and Abort both subclass RuntimeError. A command that raised
typer.Exit(1) inside a try with `except RuntimeError` caught its own exit and
printed "✗ Error: 1" under the real message; `source remove` turned Ctrl-C at its
confirm prompt into "❌ Failed to remove source:". The --json commands are covered
in test_json_error_stderr_unit.py.

The behavior tests cover the sites found on 2026-10-13. The structural test keeps the
class closed: no try block under cli/src may raise typer.Exit, or call a typer
prompt that raises typer.Abort, inside a handler broad enough to catch it unless an
earlier handler re-raises it.
"""

import ast
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from cli.prune import count as prune_count_command
from cli.prune import delete as prune_delete_command
from cli.report import generate as report_generate_command
from cli.source import remove as source_remove_command
from prismis_daemon.storage import Storage


def _app(command):
    app = typer.Typer()
    app.command()(command)
    return app


@pytest.mark.parametrize(
    ("command", "args", "message"),
    [
        pytest.param(
            prune_count_command, ["soon"], "Invalid age format", id="prune-count"
        ),
        pytest.param(
            prune_delete_command, ["soon"], "Invalid age format", id="prune-delete"
        ),
        pytest.param(report_generate_command, ["soon"], "Invalid period", id="report"),
    ],
)
def test_bad_argument_is_reported_once(command, args, message, isolated_xdg_env):
    # prune builds its APIClient before parsing the age, so it needs a key to get there.
    (isolated_xdg_env / "config.toml").write_text('[api]\nkey = "test-key"\n')

    result = CliRunner().invoke(_app(command), args)

    assert result.exit_code == 1
    assert message in result.stdout
    assert result.stdout.count("✗") == 1


def test_source_remove_abort_at_prompt_is_not_reported_as_a_failure(live_daemon):
    source_id = Storage(live_daemon.db_path).add_source(
        "http://example.com/feed", "rss", "Example"
    )

    # No input: the confirm prompt reads EOF and click raises Abort.
    result = CliRunner().invoke(_app(source_remove_command), [source_id], input="")

    assert result.exit_code == 1
    assert "Aborted" in result.output
    assert "Failed to remove source" not in result.output


_SRC = Path(__file__).resolve().parents[2] / "src" / "cli"
_CATCHES_CLICK_CONTROL_FLOW = frozenset({"RuntimeError", "Exception", "BaseException"})


def _dotted(node: ast.AST | None) -> str:
    return ast.unparse(node) if node is not None else ""


def _control_flow_raised(try_node: ast.Try) -> set[str]:
    """typer.Exit raised, or typer.Abort possible from a prompt, inside the try body."""
    raised: set[str] = set()
    for node in (n for stmt in try_node.body for n in ast.walk(stmt)):
        if isinstance(node, ast.Raise) and node.exc is not None:
            target = node.exc.func if isinstance(node.exc, ast.Call) else node.exc
            if _dotted(target) == "typer.Exit":
                raised.add("typer.Exit")
        if isinstance(node, ast.Call) and _dotted(node.func) in (
            "typer.confirm",
            "typer.prompt",
        ):
            raised.add("typer.Abort")
    return raised


def find_swallowed_control_flow(root: Path) -> list[str]:
    """Every `file:line` of a handler that catches its own try block's Exit or Abort."""
    found: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for try_node in (n for n in ast.walk(tree) if isinstance(n, ast.Try)):
            pending = _control_flow_raised(try_node)
            for handler in try_node.handlers:
                if not pending:
                    break
                caught = handler.type
                names = (
                    {_dotted(e) for e in caught.elts}
                    if isinstance(caught, ast.Tuple)
                    else {_dotted(caught)}
                )
                if caught is None or names & _CATCHES_CLICK_CONTROL_FLOW:
                    found.append(f"{path.name}:{handler.lineno}")
                    break
                pending -= names
    return found


def test_no_cli_handler_catches_its_own_exit_or_abort():
    assert find_swallowed_control_flow(_SRC) == []
