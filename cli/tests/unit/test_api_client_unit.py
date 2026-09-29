"""Unit tests for APIClient's consolidated `_send`/`_send_json` HTTP helper.

Protects the httpx-level round-trip triage's cluster 3 found untested: `add_source`,
`remove_source`, `pause_source`, `resume_source`, `count_unprioritized`,
`prune_unprioritized`, `get_report`, `edit_source`, `get_entry`, `get_entry_raw`,
`get_archive_status`, `get_statistics`, `get_sources` — 13 of `APIClient`'s methods
that had no direct httpx-level test before this file (docs/work/dedup-triage.md's
cluster 3 "Gap"). `search`'s `min_score` param (test_api_client_search_params.py),
`get_content`'s `kind` param (test_list_kind_filter_unit.py), and `extract_entry`
(test_extract_entry_unit.py) already had coverage and are not repeated here.

Also protects the two behavior risks the triage named for this consolidation:
- INV: `get_entry_raw` uses `_send` only, never `_send_json` — it does not parse
  the response as JSON, does not check the `success` flag, and raises a distinct
  "Entry not found or API error: {status}" message on failure.
- INV: `count_unprioritized` and `prune_unprioritized` never checked `data.get(
  "success")` before consolidation; `_send_json`'s `check_success=False` must
  keep that — a 200 response with `"success": false` must not raise for these
  two callers, unlike every other method routed through `_send_json`.

Per constitution.md's "Internal code is never mocked" rule (same reading applied
by test_list_kind_filter_unit.py), every test here drives the real `APIClient`
against a real local HTTP server standing in for the daemon — no `unittest.mock`,
no monkeypatched APIClient method.
"""

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator
from typing import ClassVar

import httpx
import pytest

from cli.api_client import APIClient


def _make_client(base_url: str) -> APIClient:
    """Construct APIClient bypassing __init__'s config-file dependency."""
    client = object.__new__(APIClient)
    client.base_url = base_url
    client.api_key = "test-key"
    client.timeout = httpx.Timeout(30.0)
    return client


class _RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Answers every verb with a canned response and records what it received.

    Class attributes configure the next response; each test's fixture resets
    them before yielding. `received_*` attributes are read back by the test
    after the client call — there is only ever one request in flight per test.
    """

    response_status: int = 200
    response_json: ClassVar[dict | None] = {"success": True, "data": {}}
    response_text: str | None = None

    received_method: str = ""
    received_path: str = ""
    received_query: str = ""
    received_api_key: str | None = None
    received_body: dict | None = None

    def log_message(self, *_args: object) -> None:  # keep pytest output clean
        return

    def _handle(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        type(self).received_method = self.command
        type(self).received_path = parsed.path
        type(self).received_query = parsed.query
        type(self).received_api_key = self.headers.get("X-API-Key")

        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            raw = self.rfile.read(length)
            type(self).received_body = json.loads(raw) if raw else None
        else:
            type(self).received_body = None

        if self.response_json is not None:
            body = json.dumps(self.response_json).encode()
            content_type = "application/json"
        else:
            body = (self.response_text or "").encode()
            content_type = "text/plain"

        self.send_response(self.response_status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _handle
    do_POST = _handle
    do_PATCH = _handle
    do_DELETE = _handle


@pytest.fixture
def recording_server() -> Iterator[str]:
    """A real local HTTP server that records requests and answers a canned response."""
    _RecordingHandler.response_status = 200
    _RecordingHandler.response_json = {"success": True, "data": {}}
    _RecordingHandler.response_text = None
    _RecordingHandler.received_method = ""
    _RecordingHandler.received_path = ""
    _RecordingHandler.received_query = ""
    _RecordingHandler.received_api_key = None
    _RecordingHandler.received_body = None

    server = http.server.HTTPServer(("127.0.0.1", 0), _RecordingHandler)
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


# ---------------------------------------------------------------------------
# Each of the 13 previously-untested methods: one real HTTP round trip
# ---------------------------------------------------------------------------


def test_add_source_posts_body_and_returns_data(recording_server: str) -> None:
    _RecordingHandler.response_json = {"success": True, "data": {"id": "src-1"}}
    client = _make_client(recording_server)

    result = client.add_source("http://x.com/feed", "rss", name="X Feed")

    assert result == {"id": "src-1"}
    assert _RecordingHandler.received_method == "POST"
    assert _RecordingHandler.received_path == "/api/sources"
    assert _RecordingHandler.received_api_key == "test-key"
    assert _RecordingHandler.received_body == {
        "url": "http://x.com/feed",
        "type": "rss",
        "name": "X Feed",
    }


def test_remove_source_deletes_and_returns_true(recording_server: str) -> None:
    client = _make_client(recording_server)

    result = client.remove_source("src-1")

    assert result is True
    assert _RecordingHandler.received_method == "DELETE"
    assert _RecordingHandler.received_path == "/api/sources/src-1"


def test_pause_source_patches_and_returns_true(recording_server: str) -> None:
    client = _make_client(recording_server)

    result = client.pause_source("src-1")

    assert result is True
    assert _RecordingHandler.received_method == "PATCH"
    assert _RecordingHandler.received_path == "/api/sources/src-1/pause"


def test_resume_source_patches_and_returns_true(recording_server: str) -> None:
    client = _make_client(recording_server)

    result = client.resume_source("src-1")

    assert result is True
    assert _RecordingHandler.received_method == "PATCH"
    assert _RecordingHandler.received_path == "/api/sources/src-1/resume"


def test_count_unprioritized_returns_count(recording_server: str) -> None:
    _RecordingHandler.response_json = {"success": True, "data": {"count": 7}}
    client = _make_client(recording_server)

    result = client.count_unprioritized(days=3)

    assert result == 7
    assert _RecordingHandler.received_method == "GET"
    assert _RecordingHandler.received_path == "/api/prune/count"
    assert urllib.parse.parse_qs(_RecordingHandler.received_query).get("days") == ["3"]


def test_count_unprioritized_does_not_raise_when_success_false(
    recording_server: str,
) -> None:
    """
    INVARIANT (behavior risk): count_unprioritized never checked `success`
    before consolidation. A 200 response with `"success": false` must still
    return the count, not raise — unlike every other _send_json caller.
    """
    _RecordingHandler.response_json = {"success": False, "data": {"count": 0}}
    client = _make_client(recording_server)

    result = client.count_unprioritized()

    assert result == 0


def test_prune_unprioritized_returns_full_body(recording_server: str) -> None:
    _RecordingHandler.response_json = {"success": True, "deleted": 5}
    client = _make_client(recording_server)

    result = client.prune_unprioritized(days=10)

    assert result == {"success": True, "deleted": 5}
    assert _RecordingHandler.received_method == "POST"
    assert _RecordingHandler.received_path == "/api/prune"
    assert urllib.parse.parse_qs(_RecordingHandler.received_query).get("days") == ["10"]


def test_prune_unprioritized_does_not_raise_when_success_false(
    recording_server: str,
) -> None:
    """
    INVARIANT (behavior risk): prune_unprioritized never checked `success`
    before consolidation either. A 200 response with `"success": false` must
    still return the body, not raise.
    """
    _RecordingHandler.response_json = {"success": False, "deleted": 0}
    client = _make_client(recording_server)

    result = client.prune_unprioritized()

    assert result == {"success": False, "deleted": 0}


def test_get_report_returns_markdown(recording_server: str) -> None:
    _RecordingHandler.response_json = {
        "success": True,
        "data": {"markdown": "# Report"},
    }
    client = _make_client(recording_server)

    result = client.get_report(period="7d")

    assert result == "# Report"
    assert _RecordingHandler.received_path == "/api/reports"
    assert urllib.parse.parse_qs(_RecordingHandler.received_query).get("period") == [
        "7d"
    ]


def test_edit_source_patches_name_and_returns_true(recording_server: str) -> None:
    client = _make_client(recording_server)

    result = client.edit_source("src-1", "New Name")

    assert result is True
    assert _RecordingHandler.received_method == "PATCH"
    assert _RecordingHandler.received_path == "/api/sources/src-1"
    assert _RecordingHandler.received_body == {"name": "New Name"}


def test_get_entry_returns_data(recording_server: str) -> None:
    _RecordingHandler.response_json = {
        "success": True,
        "data": {"id": "e-1", "title": "An Entry"},
    }
    client = _make_client(recording_server)

    result = client.get_entry("e-1")

    assert result == {"id": "e-1", "title": "An Entry"}
    assert _RecordingHandler.received_path == "/api/entries/e-1"


def test_get_archive_status_returns_data(recording_server: str) -> None:
    _RecordingHandler.response_json = {
        "success": True,
        "data": {"enabled": True, "count": 3},
    }
    client = _make_client(recording_server)

    result = client.get_archive_status()

    assert result == {"enabled": True, "count": 3}
    assert _RecordingHandler.received_path == "/api/archive/status"


def test_get_statistics_returns_data(recording_server: str) -> None:
    _RecordingHandler.response_json = {
        "success": True,
        "data": {"total_content": 42},
    }
    client = _make_client(recording_server)

    result = client.get_statistics()

    assert result == {"total_content": 42}
    assert _RecordingHandler.received_path == "/api/statistics"


def test_get_sources_returns_sources_list(recording_server: str) -> None:
    _RecordingHandler.response_json = {
        "success": True,
        "data": {"sources": [{"id": "src-1", "name": "X Feed"}]},
    }
    client = _make_client(recording_server)

    result = client.get_sources()

    assert result == [{"id": "src-1", "name": "X Feed"}]
    assert _RecordingHandler.received_path == "/api/sources"


# ---------------------------------------------------------------------------
# get_entry_raw — the outlier: _send only, never _send_json
# ---------------------------------------------------------------------------


def test_get_entry_raw_returns_plain_text_without_json_parsing(
    recording_server: str,
) -> None:
    """
    INVARIANT (behavior risk): get_entry_raw does not call response.json() and
    does not check `success` — it returns response.text verbatim, even when
    that text is not valid JSON at all.
    """
    _RecordingHandler.response_json = None
    _RecordingHandler.response_text = "not json at all, just raw article text"
    client = _make_client(recording_server)

    result = client.get_entry_raw("e-1")

    assert result == "not json at all, just raw article text"
    assert _RecordingHandler.received_path == "/api/entries/e-1/raw"


def test_get_entry_raw_raises_distinct_message_on_http_error(
    recording_server: str,
) -> None:
    """
    INVARIANT (behavior risk): get_entry_raw's error message is "Entry not
    found or API error: {status}" — distinct from _send_json's "API error:
    {status}" / body-message wording every other method raises.
    """
    _RecordingHandler.response_status = 404
    _RecordingHandler.response_json = None
    _RecordingHandler.response_text = "Not Found"
    client = _make_client(recording_server)

    with pytest.raises(RuntimeError) as exc_info:
        client.get_entry_raw("missing-entry")

    assert "Entry not found or API error: 404" in str(exc_info.value), (
        f"expected the distinct raw-endpoint message, got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# _send_json's standard status/success handling, proven once at this level
# (per-method coverage above exercises the happy path; this proves the shared
# failure handling itself, through a caller that uses the default check_success=True)
# ---------------------------------------------------------------------------


def test_send_json_raises_on_http_error_status(recording_server: str) -> None:
    _RecordingHandler.response_status = 500
    _RecordingHandler.response_json = {"message": "boom"}
    client = _make_client(recording_server)

    with pytest.raises(RuntimeError, match="boom"):
        client.get_sources()


def test_send_json_raises_when_success_false(recording_server: str) -> None:
    _RecordingHandler.response_json = {"success": False, "message": "nope"}
    client = _make_client(recording_server)

    with pytest.raises(RuntimeError, match="nope"):
        client.get_sources()
