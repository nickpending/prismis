"""Unit tests for APIClient.extract_entry() HTTP contract.

Protects:
- INV: extract_entry() opens its own httpx.Client with a 120s local override,
  ignoring self.timeout (the 30s class default)
- INV: extract_entry() raises RuntimeError on HTTP 4xx/5xx responses
- INV: extract_entry() raises RuntimeError wrapping network errors

Tests bypass APIClient.__init__ (avoids config-file dependency) — same pattern as
test_api_client_search_params.py. The HTTP boundary is real throughout: a real
`live_daemon` (conftest.py) for the 503 and success cases, and a real closed port /
real slow peer for the two tests that are specifically about the client's own
httpx wiring rather than daemon behavior.
"""

import http.server
import json
import socket
import threading
import time

import httpx
import pytest

from cli.api_client import APIClient

from conftest import LiveDaemon, seed_content
from prismis_daemon.storage import Storage


def _make_client(base_url: str, api_key: str = "test-key") -> APIClient:
    """Construct APIClient bypassing __init__ config-file dependency."""
    client = object.__new__(APIClient)
    client.base_url = base_url
    client.api_key = api_key
    client.timeout = httpx.Timeout(30.0)
    return client


def test_extract_entry_uses_120s_timeout_not_class_default() -> None:
    """
    INVARIANT: extract_entry() opens httpx.Client with a hardcoded 120s local
    override (api_client.py:728), not `self.timeout`.
    BREAKS: LLM extractions take 60-90s on large docs; the 30s class default
    aborts them silently if the override regresses to using self.timeout.

    A real local peer delays its response by 1s. `self.timeout` is set here to
    50ms — well under that delay — so this only succeeds if extract_entry()
    truly ignores self.timeout and uses a much larger local override: reverting
    the hardcoded 120s back to `self.timeout` makes this test raise RuntimeError
    (a read timeout at 50ms, wrapped) instead of returning.
    """

    class _SlowHandler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            time.sleep(1.0)
            body = json.dumps(
                {"success": True, "data": {"deep_extraction": {"synthesis": "done"}}}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.HTTPServer(("127.0.0.1", 0), _SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address[0], server.server_address[1]
        assert isinstance(host, str), "loopback bind always yields a str host"
        client = _make_client(f"http://{host}:{port}")
        client.timeout = httpx.Timeout(0.05)

        result = client.extract_entry("entry-uuid-123")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)

    assert result == {"deep_extraction": {"synthesis": "done"}}, (
        f"expected the real slow peer's response to come through, got: {result}"
    )


def test_extract_entry_raises_on_http_error(live_daemon: LiveDaemon) -> None:
    """
    INVARIANT: extract_entry() raises RuntimeError when API returns 4xx/5xx.
    BREAKS: Errors silently swallowed; per-item loop thinks extraction succeeded.

    `live_daemon` has no deep-extraction service configured, so a real entry
    with no existing analysis hits the real 503 "not configured" path
    (api.py:1229-1233) — the real error path exercised by the build phase demo.
    """
    storage = Storage(live_daemon.db_path)
    source_id = storage.add_source("http://example.com/feed", "rss", "Example")
    content_id = seed_content(storage, source_id, title="Pending entry")
    storage.close()

    client = _make_client(live_daemon.base_url, live_daemon.api_key)

    with pytest.raises(RuntimeError) as exc_info:
        client.extract_entry(content_id)

    assert "not configured" in str(exc_info.value), (
        f"RuntimeError must carry the server's message, got: {exc_info.value!r}"
    )


def test_extract_entry_wraps_network_error_as_runtime_error() -> None:
    """
    INVARIANT: httpx.RequestError is caught and re-raised as RuntimeError("Network error: ...").
    BREAKS: httpx.ConnectError propagates uncaught; CLI loop's `except RuntimeError` misses it,
    aborting the entire batch instead of recording a per-item failure.

    Points at a real closed loopback port (bound, then closed, so nothing answers):
    a real httpx.ConnectError, not an injected one.
    """
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    probe.bind(("127.0.0.1", 0))
    host, port = probe.getsockname()
    probe.close()

    client = _make_client(f"http://{host}:{port}")

    with pytest.raises(RuntimeError) as exc_info:
        client.extract_entry("entry-uuid-net")

    assert "Network error" in str(exc_info.value), (
        f"RuntimeError message must start with 'Network error:', got: {exc_info.value!r}"
    )
