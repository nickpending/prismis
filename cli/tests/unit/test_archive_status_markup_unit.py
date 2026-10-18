"""`archive status` renders its Enabled/Disabled word as markup, never as literal tags.

Protects:
- INV: the status word is the CLI's own markup, so escaping it would print
  `[green]Enabled[/green]` as visible text instead of colouring the word

Invokes the real `status()` command against the fixture's real daemon.
"""

import re

import typer
from typer.testing import CliRunner

from cli.archive import status

from conftest import LiveDaemon

_app = typer.Typer()
_app.command()(status)

runner = CliRunner()


def test_status_word_is_rendered_not_printed_as_tags(live_daemon: LiveDaemon) -> None:
    """
    INVARIANT: the Enabled/Disabled word prints without its markup tags.
    BREAKS: escaping the template shows '[green]Enabled[/green]' as text and loses the colour.
    """
    result = runner.invoke(_app, [])

    assert result.exit_code == 0, result.output
    assert re.search(r"Archival Status: (Enabled|Disabled)\b", result.output), (
        result.output
    )
    assert "[green]" not in result.output and "[red]" not in result.output
