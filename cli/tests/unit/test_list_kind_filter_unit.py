"""Unit tests for `prismis-cli list --kind` — SC-6.

Protects:
- INV: APIClient.get_content(kind=...) puts a `kind` param on the request only when a
  value is given (mirrors the existing `priority`/`source` falsy-guard convention in
  api_client.py:496-507), and passes a comma-separated value through unchanged.
- INV: `list --kind` forwards the flag's value onto APIClient.get_content(kind=...)
  unmodified — single value and comma-separated list alike.
- INV: the rendered table shows each returned item's `kind` field, and shows a dash
  for an item that carries no kind (unclassified), never crashing on the missing key.

Per constitution.md:42-43 ("Internal code is never mocked... a test against a mock
proves the mock, not the system"), every test here drives the real APIClient / the
real `list` Typer command against a real local HTTP server standing in only for the
network boundary — no `unittest.mock`, no monkeypatched APIClient method. This is the
same technique test_api_client_search_params.py's `param_probe` and
test_extract_command_unit.py's closed-loopback-port tests use.

The daemon's own `/api/entries` kind support (SC-5, a separate job with no dependency
edge to this one) does not exist in this tree yet, so these tests stand up their own
probe server that answers the shape the daemon will produce, rather than routing
through `live_daemon` — proving only what this job owns: what the CLI puts on the
wire and how it renders what comes back.
"""

import http.server
import json
import re
import threading
import urllib.parse
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import typer
from typer.testing import CliRunner

from cli.api_client import APIClient
from cli.list import list as list_command


def _make_client(base_url: str) -> APIClient:
    """Construct APIClient bypassing __init__'s config-file dependency."""
    client = object.__new__(APIClient)
    client.base_url = base_url
    client.api_key = "test-key"
    client.timeout = httpx.Timeout(30.0)
    return client


# ---------------------------------------------------------------------------
# APIClient.get_content(kind=...) — the request-building invariant
# ---------------------------------------------------------------------------


class _KindParamProbeHandler(http.server.BaseHTTPRequestHandler):
    """Reports the `kind` query param a GET actually carried."""

    def log_message(self, *_args: object) -> None:  # keep pytest output clean
        return

    def do_GET(self) -> None:
        query = urllib.parse.urlparse(self.path).query
        params = urllib.parse.parse_qs(query)
        payload = {
            "success": True,
            "data": {
                "items": [
                    {
                        "has_kind_param": "kind" in params,
                        "kind_param_value": params.get("kind", [None])[0],
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
def kind_param_probe() -> Iterator[str]:
    """A real local HTTP server reporting back the `kind` param it received."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _KindParamProbeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str), "loopback bind always yields a str host"
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_get_content_kind_none_omits_param(kind_param_probe: str) -> None:
    """
    INVARIANT: kind=None does NOT add `kind` to the HTTP params dict.
    BREAKS: An unwanted server-side filter narrows results the caller never asked to narrow.
    """
    client = _make_client(kind_param_probe)

    results = client.get_content(kind=None)

    assert results[0]["has_kind_param"] is False, (
        f"kind=None must not add 'kind' to params, got: {results[0]}"
    )


def test_get_content_kind_single_value_included(kind_param_probe: str) -> None:
    """
    INVARIANT: kind='release' is sent as the `kind` query param unchanged.
    """
    client = _make_client(kind_param_probe)

    results = client.get_content(kind="release")

    assert results[0]["has_kind_param"] is True
    assert results[0]["kind_param_value"] == "release", (
        f"expected 'release', got: {results[0]['kind_param_value']}"
    )


def test_get_content_kind_comma_separated_passed_as_is(kind_param_probe: str) -> None:
    """
    INVARIANT: A comma-separated kind list is passed through unmodified, matching
    the API's documented single-or-comma-separated `kind` param (SC-5), the same
    convention `priority` already uses (api.py's Query description at line 787).
    """
    client = _make_client(kind_param_probe)

    results = client.get_content(kind="release,question,incident")

    assert results[0]["kind_param_value"] == "release,question,incident", (
        f"expected the raw comma-separated value untouched, got: "
        f"{results[0]['kind_param_value']}"
    )


# ---------------------------------------------------------------------------
# `list --kind` — the command wiring and rendering invariants
# ---------------------------------------------------------------------------

_app = typer.Typer()
_app.command()(list_command)

runner = CliRunner()


class _ListEntriesHandler(http.server.BaseHTTPRequestHandler):
    """Serves /api/entries: records the received query, returns fixed items.

    One item carries a `kind`; one carries none (the unclassified case). Class
    attribute `received_query` is read by the test after invoking the command —
    there is only ever one request in flight per test here.
    """

    received_query: str = ""

    def log_message(self, *_args: object) -> None:  # keep pytest output clean
        return

    def do_GET(self) -> None:
        type(self).received_query = urllib.parse.urlparse(self.path).query
        payload = {
            "success": True,
            "data": {
                "items": [
                    {
                        "id": "11111111-aaaa-bbbb-cccc-000000000001",
                        "title": "A Release Item",
                        "priority": "high",
                        "kind": "release",
                        "published": "2026-01-01 00:00",
                    },
                    {
                        "id": "22222222-aaaa-bbbb-cccc-000000000002",
                        "title": "An Unclassified Item",
                        "priority": "low",
                        "published": "2026-01-02 00:00",
                    },
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
def list_entries_probe(
    isolated_xdg_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[str]:
    """A real local /api/entries stand-in, wired as the sealed config's [remote].

    `list()` builds its own `APIClient()` in-body (no injection point), so the
    only way to redirect it onto this fixture's ephemeral port is the same
    sealed-config technique `test_extract_command_unit.py`'s
    `test_get_content_failure_exits_one_not_silent_noop` uses: write [remote]
    directly into the isolated XDG config.toml.

    CliRunner has no controlling terminal, so Rich's Console falls back to
    `shutil.get_terminal_size()`'s COLUMNS-env-var lookup (re-read on every
    print, not cached at Console() construction — unlike its color system,
    per conftest.py's FORCE_COLOR note above). Widening it here keeps the
    table's five columns (ID, Title, Priority, Kind, Published) from being
    squeezed to single-character, ellipsized headers that swallow their cell
    content before an `in result.output` assertion ever sees it.
    """
    monkeypatch.setenv("COLUMNS", "200")
    _ListEntriesHandler.received_query = ""
    server = http.server.HTTPServer(("127.0.0.1", 0), _ListEntriesHandler)
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


def test_list_command_carries_kind_flag_onto_request(
    list_entries_probe: str,
) -> None:
    """
    INVARIANT: `list --kind release,question` puts `kind=release%2Cquestion` (or
    the unescaped equivalent) on the real HTTP request the CLI sends.
    BREAKS: A flag that is parsed but never reaches APIClient.get_content() silently
    returns unfiltered results while claiming to have filtered them.
    """
    result = runner.invoke(_app, ["--kind", "release,question"])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    received = urllib.parse.parse_qs(_ListEntriesHandler.received_query)
    assert received.get("kind") == ["release,question"], (
        f"expected kind=release,question on the wire, got query: "
        f"{_ListEntriesHandler.received_query!r}"
    )


def test_list_command_omits_kind_when_flag_not_given(
    list_entries_probe: str,
) -> None:
    """
    INVARIANT: Without --kind, no `kind` param reaches the request.
    BREAKS: A default value or stray param would silently narrow every plain
    `prismis-cli list` invocation.
    """
    result = runner.invoke(_app, [])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    received = urllib.parse.parse_qs(_ListEntriesHandler.received_query)
    assert "kind" not in received, (
        f"expected no kind param without --kind, got query: "
        f"{_ListEntriesHandler.received_query!r}"
    )


def test_list_command_displays_item_kind(list_entries_probe: str) -> None:
    """
    INVARIANT: The rendered table shows the classified item's kind.
    BREAKS: A user filtering `--kind release` has no way to confirm what came back.
    """
    result = runner.invoke(_app, [])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert "release" in result.output, (
        f"expected the classified item's kind 'release' in the table, got: "
        f"{result.output!r}"
    )


def test_list_command_shows_dash_for_unclassified_item(
    list_entries_probe: str,
) -> None:
    """
    INVARIANT: An item with no `kind` key renders a dash, not a crash or 'None'.
    BREAKS: entry.get("kind") returning None must not propagate "None" into the
    table (a literal Rich cell of the string "None" reads as a real kind to a user).
    """
    result = runner.invoke(_app, [])

    assert result.exit_code == 0, (
        f"Expected exit code 0, got {result.exit_code}\nOutput: {result.output}"
    )
    assert "None" not in result.output, (
        f"unclassified item must not render the literal string 'None', got: "
        f"{result.output!r}"
    )
    assert "An Unclassified Item" in result.output, (
        f"expected the unclassified item's title in output, got: {result.output!r}"
    )
    # A standalone dash (not digit-adjacent) distinguishes the Kind cell's dash
    # from the ASCII hyphens inside the published dates ("2026-01-02"), which
    # are always digit-adjacent and would otherwise make this assertion pass
    # even when the Kind column renders nothing at all.
    assert re.search(r"(?<!\d)-(?!\d)", result.output), (
        f"expected a standalone dash for the unclassified item's Kind cell, got: "
        f"{result.output!r}"
    )
