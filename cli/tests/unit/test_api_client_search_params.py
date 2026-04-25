"""Unit tests for APIClient.search() params construction — CLI wiring invariant.

Protects:
- Wiring invariant: min_score=0.0 is included in params (not dropped by falsy guard)
- Wiring invariant: min_score=None omits the param (server default applies)
- Wiring invariant: explicit min_score value is sent as-is to the API

The critical line is api_client.py:606 `if min_score is not None:`.
A falsy guard `if min_score:` would silently drop 0.0, breaking the override path.

Tests bypass APIClient.__init__ (avoids config-file dependency) and patch
httpx.Client.get at the HTTP boundary to capture the params dict.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

# Add CLI src to path (matches pattern in test_url_extraction.py)
cli_src = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(cli_src))

from cli.api_client import APIClient  # noqa: E402


def _make_client() -> APIClient:
    """Construct APIClient bypassing __init__ config-file dependency."""
    client = object.__new__(APIClient)
    client.base_url = "http://localhost:8989"
    client.api_key = "test-key"
    client.timeout = httpx.Timeout(30.0)
    return client


def _fake_get_ok(*args, **kwargs) -> MagicMock:
    """Fake httpx.Client.get returning a minimal success response."""
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"success": True, "data": {"items": []}}
    return resp


def test_min_score_none_omits_param_from_request() -> None:
    """
    INVARIANT: min_score=None does NOT add min_score to the HTTP params dict.
    BREAKS: Server default (0.1) would be overridden by an explicit None param.

    When min_score is None, the caller intends to use the server's default.
    The params dict must not include min_score so the server default applies.
    """
    client = _make_client()
    captured: dict = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _fake_get_ok()

    with patch.object(httpx.Client, "get", fake_get):
        client.search("test query", min_score=None)

    params = captured.get("params", {})
    assert "min_score" not in params, (
        f"min_score=None must not add 'min_score' to params, got: {params}"
    )


def test_min_score_zero_included_in_params() -> None:
    """
    INVARIANT: min_score=0.0 IS included in params (0.0 is falsy but valid).
    BREAKS: Users requesting all results (override path) silently get filtered results.

    This is the critical correctness point: `if min_score is not None` must be used,
    not `if min_score`. The value 0.0 is falsy, so a truthiness check would drop it.
    """
    client = _make_client()
    captured: dict = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _fake_get_ok()

    with patch.object(httpx.Client, "get", fake_get):
        client.search("test query", min_score=0.0)

    params = captured.get("params", {})
    assert "min_score" in params, (
        f"min_score=0.0 must be included in params (is not None), got: {params}"
    )
    assert params["min_score"] == 0.0, (
        f"min_score param must be 0.0, got: {params['min_score']}"
    )


def test_explicit_min_score_sent_as_is() -> None:
    """
    INVARIANT: Explicit min_score value is passed through to the HTTP request unchanged.
    BREAKS: Score threshold is silently transformed before reaching the API.
    """
    client = _make_client()
    captured: dict = {}

    def fake_get(*args, **kwargs):
        captured.update(kwargs)
        return _fake_get_ok()

    with patch.object(httpx.Client, "get", fake_get):
        client.search("test query", min_score=0.15)

    params = captured.get("params", {})
    assert params.get("min_score") == 0.15, (
        f"min_score=0.15 must reach API unchanged, got: {params.get('min_score')}"
    )
