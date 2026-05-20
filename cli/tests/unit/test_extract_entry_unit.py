"""Unit tests for APIClient.extract_entry() HTTP contract.

Protects:
- INV: extract_entry() uses 120s local timeout, not the 30s class default
- INV: extract_entry() raises RuntimeError on HTTP 4xx/5xx responses
- INV: extract_entry() raises RuntimeError wrapping network errors

Tests bypass APIClient.__init__ (avoids config-file dependency) and patch
httpx.Client.post at the HTTP boundary — same pattern as test_api_client_search_params.py.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx

# Add CLI src to path (matches pattern in test_api_client_search_params.py)
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


def test_extract_entry_uses_120s_timeout_not_class_default() -> None:
    """
    INVARIANT: extract_entry() opens httpx.Client with timeout=httpx.Timeout(120.0).
    BREAKS: LLM extractions take 60-90s on large docs; 30s class default aborts them silently.

    The 120s override is a local override per Alternative B-local (P3 reversibility).
    If someone changes it back to self.timeout, real extractions will timeout mid-run.
    """
    captured_timeout = [None]
    original_init = httpx.Client.__init__

    def fake_init(self_client, *args, **kwargs):
        captured_timeout[0] = kwargs.get("timeout")
        # Don't actually connect — stub out the post method
        original_init(self_client, *args, **kwargs)

    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {
        "success": True,
        "data": {"deep_extraction": {"synthesis": "done"}},
    }

    client = _make_client()

    with patch.object(httpx.Client, "__init__", fake_init):
        with patch.object(httpx.Client, "post", return_value=resp):
            client.extract_entry("entry-uuid-123")

    timeout = captured_timeout[0]
    assert timeout is not None, "timeout kwarg must be passed to httpx.Client()"
    assert isinstance(timeout, httpx.Timeout), (
        f"timeout must be httpx.Timeout instance, got {type(timeout)}"
    )
    assert timeout.read == 120.0, (
        f"extract_entry() must use 120s timeout (got {timeout.read}s); "
        "30s class default aborts real LLM extractions"
    )


def test_extract_entry_raises_on_http_error() -> None:
    """
    INVARIANT: extract_entry() raises RuntimeError when API returns 4xx/5xx.
    BREAKS: Errors silently swallowed; per-item loop thinks extraction succeeded.

    Simulates a 503 response (deep_service not configured) — the real error
    path exercised by the build phase demo.
    """
    resp = MagicMock()
    resp.status_code = 503
    resp.json.return_value = {
        "success": False,
        "message": "Deep extraction not configured",
    }

    client = _make_client()

    with patch.object(httpx.Client, "post", return_value=resp):
        try:
            client.extract_entry("entry-uuid-503")
            raised = False
        except RuntimeError as e:
            raised = True
            err_msg = str(e)

    assert raised, "extract_entry() must raise RuntimeError on HTTP 503"
    assert "Deep extraction not configured" in err_msg, (
        f"RuntimeError must carry server message, got: {err_msg!r}"
    )


def test_extract_entry_wraps_network_error_as_runtime_error() -> None:
    """
    INVARIANT: httpx.RequestError is caught and re-raised as RuntimeError("Network error: ...").
    BREAKS: httpx.ConnectError propagates uncaught; CLI loop's `except RuntimeError` misses it,
    aborting the entire batch instead of recording a per-item failure.
    """
    client = _make_client()

    def fake_post(*args, **kwargs):
        raise httpx.ConnectError("Connection refused")

    with patch.object(httpx.Client, "post", fake_post):
        try:
            client.extract_entry("entry-uuid-net")
            raised = False
        except RuntimeError as e:
            raised = True
            err_msg = str(e)
        except Exception:
            raised = False
            err_msg = ""

    assert raised, (
        "extract_entry() must re-raise httpx.RequestError as RuntimeError, "
        "not let it propagate raw (CLI loop only catches RuntimeError)"
    )
    assert "Network error" in err_msg, (
        f"RuntimeError message must start with 'Network error:', got: {err_msg!r}"
    )
