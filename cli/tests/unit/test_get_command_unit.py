"""Unit tests for `prismis-cli get --json` returning the full entry (cli-parity SC-3).

Protects:
- `get <id> --json` requests `include=content` and prints the content.
- The formatted mode still requests the summary shape (no `include`).

Per constitution Principle I, the real `get` Typer command runs against a real
local HTTP server standing in only for the network boundary.
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path
from typing import ClassVar

import pytest
import typer
from typer.testing import CliRunner

from cli.get import get as get_command

_ENTRY_ID = "33333333-aaaa-bbbb-cccc-000000000003"
_BASE = {
    "id": _ENTRY_ID,
    "title": "Entry",
    "priority": "high",
    "source_name": "Feed",
    "url": "https://example.com/e",
}

_app = typer.Typer()
_app.command()(get_command)
runner = CliRunner()


class _Handler(http.server.BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        _Handler.requests.append(self.path)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        entry = dict(_BASE)
        if query.get("include") == ["content"]:
            entry["content"] = "the full article body"
        body = json.dumps({"success": True, "data": entry}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def entry_server(isolated_xdg_env: Path) -> Iterator[str]:
    _Handler.requests = []
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


def test_json_mode_requests_and_prints_content(entry_server: str) -> None:
    """
    BREAKS: A get --json that omits `include=content` prints an entry with no
    content, contradicting what the prismis skill tells agents.
    """
    result = runner.invoke(_app, [_ENTRY_ID, "--json"])

    assert result.exit_code == 0, result.output
    assert any("include=content" in r for r in _Handler.requests)
    assert json.loads(result.output)["content"] == "the full article body"


def test_formatted_mode_does_not_request_content(entry_server: str) -> None:
    result = runner.invoke(_app, [_ENTRY_ID])

    assert result.exit_code == 0, result.output
    assert not any("include" in r for r in _Handler.requests)
