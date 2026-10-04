"""Unit tests for the title-only marker and reason in `prismis-cli list` and `get`
(title-only-reasons SC-7).

Protects:
- `list` marks a row whose entry is title-only with a lowercase 'title only' (the TUI
  and web marker) and leaves other rows unmarked.
- `get` prints 'Title only: <reason>' for a title-only entry and nothing for others.
- `--json` for both carries `title_only` and `title_only_reason` as the API returned
  them.

Per constitution Principle I, the real `list` and `get` Typer commands run against a
real local HTTP server standing in only for the network boundary, wired through the
sealed config's [remote] section -- same technique as test_list_kind_filter_unit.py.
"""

from __future__ import annotations

import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import typer
from typer.testing import CliRunner

from cli.get import get as get_command
from cli.list import list as list_command

_REASON = "fetch_failed:HTTP 429; content:no_prose"

_TITLE_ONLY_ENTRY: dict[str, Any] = {
    "id": "11111111-aaaa-bbbb-cccc-000000000001",
    "title": "Walled Item",
    "priority": "low",
    "published": "2026-01-01 00:00",
    "source_name": "Feed",
    "url": "https://example.com/walled",
    "title_only": True,
    "title_only_reason": _REASON,
}
_READABLE_ENTRY: dict[str, Any] = {
    "id": "22222222-aaaa-bbbb-cccc-000000000002",
    "title": "Readable Item",
    "priority": "high",
    "published": "2026-01-02 00:00",
    "source_name": "Feed",
    "url": "https://example.com/readable",
    "title_only": False,
    "title_only_reason": None,
}

_ENTRIES = {e["id"]: e for e in (_TITLE_ONLY_ENTRY, _READABLE_ENTRY)}

_list_app = typer.Typer()
_list_app.command()(list_command)
_get_app = typer.Typer()
_get_app.command()(get_command)

runner = CliRunner()


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path.startswith("/api/entries/"):
            entry = _ENTRIES.get(self.path.split("/")[3].split("?")[0])
            payload = {"success": True, "data": entry}
        else:
            payload = {"success": True, "data": {"items": list(_ENTRIES.values())}}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def entries_server(
    isolated_xdg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.setenv("COLUMNS", "200")
    server = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    base_url = f"http://{host}:{port}"
    isolated_xdg_env.joinpath("config.toml").write_text(
        f'[remote]\nurl = "{base_url}"\nkey = "unused"\n'
    )
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_list_marks_only_the_title_only_row(entries_server: str) -> None:
    """
    BREAKS: A list that never reads `title_only` shows no marker; one that marks
    every row (or the wrong one) tells the user nothing about which item failed.
    """
    result = runner.invoke(_list_app, [])

    assert result.exit_code == 0, result.output
    rows = {
        title: line
        for line in result.output.splitlines()
        for title in ("Walled Item", "Readable Item")
        if title in line
    }
    assert "title only" in rows["Walled Item"]
    assert "title only" not in rows["Readable Item"]


def test_get_prints_the_reason_for_a_title_only_entry(entries_server: str) -> None:
    """
    BREAKS: A get that omits the reason leaves the cause of the failure an
    investigation instead of a lookup.
    """
    result = runner.invoke(_get_app, [_TITLE_ONLY_ENTRY["id"]])

    assert result.exit_code == 0, result.output
    assert f"Title only: {_REASON}" in result.output


def test_get_prints_no_title_only_line_for_a_readable_entry(
    entries_server: str,
) -> None:
    """The line appears only for title-only entries; a readable one is not
    labelled. The API answers this entry with `title_only` false, the only way the
    unlabelled outcome can be distinguished from a command that never prints it."""
    result = runner.invoke(_get_app, [_READABLE_ENTRY["id"]])

    assert result.exit_code == 0, result.output
    assert "Readable Item" in result.output
    assert "Title only" not in result.output


def test_json_output_carries_both_fields(entries_server: str) -> None:
    """`--json` passes `title_only` and `title_only_reason` through for list and
    get, as the API returned them."""
    listed = runner.invoke(_list_app, ["--json"])
    fetched = runner.invoke(_get_app, [_TITLE_ONLY_ENTRY["id"], "--json"])

    assert listed.exit_code == 0, listed.output
    assert fetched.exit_code == 0, fetched.output
    by_id = {e["id"]: e for e in json.loads(listed.output)}
    assert by_id[_TITLE_ONLY_ENTRY["id"]]["title_only"] is True
    assert by_id[_TITLE_ONLY_ENTRY["id"]]["title_only_reason"] == _REASON
    got = json.loads(fetched.output)
    assert got["title_only"] is True
    assert got["title_only_reason"] == _REASON
