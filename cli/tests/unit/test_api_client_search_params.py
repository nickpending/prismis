"""Unit tests for APIClient.search() params construction — CLI wiring invariant.

Protects:
- INV: min_score=0.0 is included in params (not dropped by falsy guard)
- INV: min_score=None omits the param (server default applies)
- INV: explicit min_score value is sent as-is to the API

The critical line is api_client.py:606 `if min_score is not None:`.
A falsy guard `if min_score:` would silently drop 0.0, breaking the override path.

Tests bypass APIClient.__init__ (avoids config-file dependency) and drive the real
APIClient.search() over a real loopback socket against `param_probe`: a real local
HTTP server (below), not a mock — no httpx or cli internals are patched. The probe
reports the query string it actually received back to the caller inside the normal
`items` shape, which `APIClient.search()` returns unmodified (api_client.py's search
does `data.get("data", {}).get("items", [])` with no per-item transformation), so
what the test asserts on is exactly what crossed the real HTTP boundary.

A real `prismis_daemon` daemon (see conftest.py's `live_daemon`) would answer this
too, but only by ranking a seeded item's real embedding against `min_score` — a
similarity score this suite cannot pin to a side of the 0.0/0.1 boundary without
depending on the embedding model's actual output. This wiring invariant is about
what the CLI puts on the wire, not about semantic ranking, so the probe narrows the
proof to what can be pinned deterministically.
"""

import http.server
import json
import threading
import urllib.parse
from collections.abc import Iterator

import httpx
import pytest

from cli.api_client import APIClient


class _ParamProbeHandler(http.server.BaseHTTPRequestHandler):
    """Reports the query params a GET actually carried, inside a normal items list."""

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
                        "has_min_score": "min_score" in params,
                        "min_score_value": params.get("min_score", [None])[0],
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
def param_probe() -> Iterator[str]:
    """A real local HTTP server that reports back what query params it received."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _ParamProbeHandler)
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


def _make_client(base_url: str) -> APIClient:
    """Construct APIClient bypassing __init__ config-file dependency."""
    client = object.__new__(APIClient)
    client.base_url = base_url
    client.api_key = "test-key"
    client.timeout = httpx.Timeout(30.0)
    return client


def test_min_score_none_omits_param_from_request(param_probe: str) -> None:
    """
    INVARIANT: min_score=None does NOT add min_score to the HTTP params dict.
    BREAKS: Server default (0.1) would be overridden by an explicit None param.

    When min_score is None, the caller intends to use the server's default.
    The params dict must not include min_score so the server default applies.
    """
    client = _make_client(param_probe)

    results = client.search("test query", min_score=None)

    assert results[0]["has_min_score"] is False, (
        f"min_score=None must not add 'min_score' to params, got: {results[0]}"
    )


def test_min_score_zero_included_in_params(param_probe: str) -> None:
    """
    INVARIANT: min_score=0.0 IS included in params (0.0 is falsy but valid).
    BREAKS: Users requesting all results (override path) silently get filtered results.

    This is the critical correctness point: `if min_score is not None` must be used,
    not `if min_score`. The value 0.0 is falsy, so a truthiness check would drop it.
    """
    client = _make_client(param_probe)

    results = client.search("test query", min_score=0.0)

    assert results[0]["has_min_score"] is True, (
        f"min_score=0.0 must be included in params (is not None), got: {results[0]}"
    )
    assert results[0]["min_score_value"] == "0.0", (
        f"min_score param must carry 0.0, got: {results[0]['min_score_value']}"
    )


def test_explicit_min_score_sent_as_is(param_probe: str) -> None:
    """
    INVARIANT: Explicit min_score value is passed through to the HTTP request unchanged.
    BREAKS: Score threshold is silently transformed before reaching the API.
    """
    client = _make_client(param_probe)

    results = client.search("test query", min_score=0.15)

    assert results[0]["min_score_value"] == "0.15", (
        f"min_score=0.15 must reach API unchanged, got: {results[0]['min_score_value']}"
    )
