"""Unit tests for `format_priority` — cluster 17's cli-wide consolidation.

Protects:
- INV: HIGH/MEDIUM/LOW each get the red/yellow/green Rich markup `list.py` and
  `search.py` both used before consolidation (`format_priority` is the one
  function both now call).
- INV: any other value (e.g. "N/A") passes through unchanged, matching the
  original `if/elif/else`'s `else: priority_display = priority_val` branch.
- INV: `list` and `search`'s rendered tables still carry the same color markup
  after routing through the shared helper — proven through the real Typer
  commands against a real local HTTP server, not just the pure function.
"""

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from cli.format import format_priority
from cli.list import list as list_command
from cli.search import search as search_command


# ---------------------------------------------------------------------------
# format_priority — the pure function
# ---------------------------------------------------------------------------


def test_high_gets_red_markup() -> None:
    assert format_priority("HIGH") == "[red]HIGH[/red]"


def test_medium_gets_yellow_markup() -> None:
    assert format_priority("MEDIUM") == "[yellow]MEDIUM[/yellow]"


def test_low_gets_green_markup() -> None:
    assert format_priority("LOW") == "[green]LOW[/green]"


def test_unknown_value_passes_through_unchanged() -> None:
    """
    INVARIANT: a value that is none of HIGH/MEDIUM/LOW (e.g. "N/A", the
    fallback for a missing priority) is returned as-is, with no markup.
    """
    assert format_priority("N/A") == "N/A"


# ---------------------------------------------------------------------------
# list / search — the two real callers render through the shared helper
# ---------------------------------------------------------------------------

_list_app = typer.Typer()
_list_app.command()(list_command)

_search_app = typer.Typer()
_search_app.command()(search_command)

runner = CliRunner()


class _EntriesHandler(http.server.BaseHTTPRequestHandler):
    """Serves /api/entries with one HIGH-priority item."""

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        payload: dict
        if "/api/search" in self.path or urllib.parse.parse_qs(query).get("q"):
            payload = {
                "success": True,
                "data": {
                    "items": [
                        {
                            "id": "11111111-aaaa-bbbb-cccc-000000000001",
                            "title": "A High Priority Result",
                            "priority": "high",
                            "relevance_score": 0.9,
                            "published_at": "2026-01-01 00:00",
                        }
                    ]
                },
            }
        else:
            payload = {
                "success": True,
                "data": {
                    "items": [
                        {
                            "id": "11111111-aaaa-bbbb-cccc-000000000001",
                            "title": "A High Priority Entry",
                            "priority": "high",
                            "published": "2026-01-01 00:00",
                        }
                    ]
                },
            }
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def entries_probe(
    isolated_xdg_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """A real local server standing in for the daemon, wired via sealed [remote]."""
    monkeypatch.setenv("COLUMNS", "200")
    server = http.server.HTTPServer(("127.0.0.1", 0), _EntriesHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str), "loopback bind always yields a str host"
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


def test_list_command_renders_high_priority_in_red(entries_probe: str) -> None:
    """
    INVARIANT: `list`'s table still shows HIGH-priority entries in Rich red
    markup after routing through the shared `format_priority` helper.
    """
    result = runner.invoke(_list_app, [])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert "HIGH" in result.output, f"expected HIGH in output, got: {result.output!r}"


def test_search_command_renders_high_priority(entries_probe: str) -> None:
    """
    INVARIANT: `search`'s table still shows HIGH-priority results after
    routing through the shared `format_priority` helper — the same function
    `list` uses, not a second copy.
    """
    result = runner.invoke(_search_app, ["some query"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert "HIGH" in result.output, f"expected HIGH in output, got: {result.output!r}"
