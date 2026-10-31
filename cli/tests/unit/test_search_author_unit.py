"""`prismis-cli search --author` shows only that author's items.

Protects:
- INV: `search <q> --author jane` against a real daemon lists only the item whose
  stored author matches, in the table and in the --json output alike.

Success criteria covered: C5 (the command half; the wire half is in
test_api_client_search_params.py).

Invokes the real `search` command against conftest.py's `live_daemon`: a real daemon,
real Storage, real Embedder for the query. The two seeded items share one embedding,
so only the author filter can tell them apart.
"""

import json
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from cli.search import search as search_command
from prismis_daemon.storage import Storage

from conftest import LiveDaemon, seed_content

_app = typer.Typer()
_app.command()(search_command)

runner = CliRunner()

JANE_TITLE = "Resolver cache notes"
OTHER_TITLE = "Linter configuration notes"


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


def _json_document(stdout: str) -> list[dict[str, Any]]:
    """The JSON array the command printed.

    live_daemon serves in this process, so its request log lines land in the same
    captured stdout ahead of the command's own output; the array starts at the first
    line that is a bare `[`.
    """
    lines = stdout.splitlines()
    return json.loads("\n".join(lines[lines.index("[") :]))


@pytest.fixture
def authored_daemon(
    live_daemon: LiveDaemon, monkeypatch: pytest.MonkeyPatch
) -> LiveDaemon:
    """A Jane Doe item and an Other Person item at the same embedding.

    COLUMNS is widened so Rich's table, which has no terminal under CliRunner, does
    not squeeze the Title column into an ellipsis before an assertion reads it.
    """
    monkeypatch.setenv("COLUMNS", "200")
    storage = Storage(live_daemon.db_path)
    source_id = storage.add_source("http://example.com/feed", "rss", "Example")
    for title, author in ((JANE_TITLE, "Jane Doe"), (OTHER_TITLE, "Other Person")):
        analysis: dict[str, Any] = {"metrics": {"author": author}}
        content_id = seed_content(storage, source_id, title=title, analysis=analysis)
        storage.add_embedding(content_id, _unit_vector(0))
    storage.close()
    return live_daemon


def test_author_flag_narrows_the_table(authored_daemon: LiveDaemon) -> None:
    """
    INVARIANT: the table lists Jane Doe's item and not Other Person's
    BREAKS: a flag that never reaches the request shows both items while the user
            believes the list was narrowed
    """
    result = runner.invoke(_app, ["notes", "--author", "jane", "--min-score", "0.0"])

    assert result.exit_code == 0, result.output
    assert JANE_TITLE in result.output, result.output
    assert OTHER_TITLE not in result.output, result.output


def test_author_flag_narrows_the_json_output(authored_daemon: LiveDaemon) -> None:
    """
    INVARIANT: --json holds Jane Doe's item only
    BREAKS: a script reading --json receives every author's items
    """
    result = runner.invoke(
        _app, ["notes", "--author", "jane", "--min-score", "0.0", "--json"]
    )

    assert result.exit_code == 0, result.output
    titles = [item["title"] for item in _json_document(result.stdout)]
    assert titles == [JANE_TITLE], titles
