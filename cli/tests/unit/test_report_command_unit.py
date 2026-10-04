"""Unit tests for `prismis-cli report` rendering from GET /api/entries list data
(cli-parity SC-1, SC-2).

Protects:
- `report generate <period>` makes one request to /api/entries carrying
  `since_hours` and `view=list` and never touches /api/reports.
- The output holds every high item, medium capped at 10, low capped at 15, each
  with title, source, age, link and summary, with 'and N more' lines and no
  unprioritized item.
- A period that is malformed or beyond 720 hours exits non-zero with a message
  and sends no request.

Per constitution Principle I, the real Typer app runs against a real local HTTP
server standing in only for the network boundary, wired through the sealed
config's [remote] section -- same technique as test_title_only_display_unit.py.
"""

from __future__ import annotations

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, ClassVar

import pytest
from typer.testing import CliRunner

from cli.report import app as report_app

runner = CliRunner()


def _item(n: int, priority: str | None) -> dict[str, Any]:
    published = datetime.now(timezone.utc) - timedelta(hours=3)
    return {
        "id": f"id-{priority}-{n}",
        "title": f"Title {priority} {n}",
        "url": f"https://example.com/{priority}/{n}",
        "priority": priority,
        "source_name": f"Source {priority} {n}",
        "summary": f"Summary {priority} {n}",
        "published_at": published.isoformat(),
    }


_ITEMS = (
    [_item(n, "high") for n in range(3)]
    + [_item(n, "medium") for n in range(13)]
    + [_item(n, "low") for n in range(18)]
    + [_item(n, None) for n in range(2)]
)


class _Handler(http.server.BaseHTTPRequestHandler):
    requests: ClassVar[list[str]] = []

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        _Handler.requests.append(self.path)
        if self.path.startswith("/api/entries"):
            body = json.dumps({"success": True, "data": {"items": _ITEMS}}).encode()
            status = 200
        else:
            body = json.dumps({"message": "not found"}).encode()
            status = 404
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def entries_server(
    isolated_xdg_env: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.setenv("COLUMNS", "200")
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


def test_report_renders_sections_from_list_data(entries_server: str) -> None:
    """
    BREAKS: A report that still calls /api/reports gets a 404; one that skips the
    caps floods the terminal; one that includes unprioritized items misreports.
    """
    result = runner.invoke(report_app, ["generate", "48h"])

    assert result.exit_code == 0, result.output
    assert len(_Handler.requests) == 1
    parsed = urllib.parse.urlparse(_Handler.requests[0])
    assert parsed.path == "/api/entries"
    query = urllib.parse.parse_qs(parsed.query)
    assert query["since_hours"] == ["48"]
    assert query["view"] == ["list"]

    out = result.output
    for n in range(3):
        assert f"Title high {n}" in out
    for n in range(10):
        assert f"Title medium {n}" in out
    for n in range(10, 13):
        assert f"Title medium {n}" not in out
    assert "... and 3 more items" in out
    for n in range(15):
        assert f"Title low {n}" in out
    for n in range(15, 18):
        assert f"Title low {n}" not in out
    assert out.count("... and 3 more items") == 2
    assert "Title None" not in out
    # Per-item fields, checked on one item per section
    assert "Source high 0" in out
    assert "3 hours ago" in out
    assert "https://example.com/high/0" in out
    assert "Summary high 0" in out
    assert "https://example.com/low/0" in out
    assert "Summary low 0" in out


@pytest.mark.parametrize(
    ("period", "expected"),
    [("31d", "720 hours"), ("721h", "720 hours"), ("bogus", "<n>h or <n>d")],
)
def test_report_refuses_bad_period_before_any_request(
    entries_server: str, period: str, expected: str
) -> None:
    """
    BREAKS: A period beyond the API's 720-hour bound would reach the daemon and
    come back as a validation error; a malformed one would be sent verbatim.
    """
    result = runner.invoke(report_app, ["generate", period])

    assert result.exit_code != 0
    assert _Handler.requests == []
    assert expected in result.output
