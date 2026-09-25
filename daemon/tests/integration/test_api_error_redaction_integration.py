"""An unexpected failure never puts its exception text in an API response (gh #76).

Invariant protected:
  - a 500 carries a generic message; the exception's text goes to the observability
    log, where the operator can read it, and nowhere a client can

Exception text can hold a credential, a filesystem path or a query. /api/context was
proven to return an API key this way; every other handler had the same shape.

The behavior test drives a real failure through a real endpoint: the sources table is
dropped from the sealed test database, so the real query raises sqlite's own error
inside the handler. The structural test covers every handler, since each one
cannot be made to fail for real from a test.
"""

import ast
import json
import os
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.observability import reset_logger

from conftest import TEST_API_KEY

_API = Path(__file__).resolve().parents[2] / "src" / "prismis_daemon" / "api.py"

# A circuit-open message is prismis's own text, built to tell the client when to retry.
_SAFE_EXCEPTION_TYPES = frozenset({"CircuitOpenError"})

# Storage's input checks raise ValueError with prismis's own message about the request's
# own values ("Invalid user_feedback value: ..."), returned as a 422 on purpose.
_SAFE_CONVERSIONS = frozenset({("ValueError", "ValidationError")})


def test_a_failing_endpoint_returns_no_exception_text(test_db: Path) -> None:
    """
    INVARIANT: A real sqlite error inside /api/sources reaches the log, not the response
    BREAKS: The client receives the database's own error text, and with it whatever a
            query, path or credential the exception happened to carry
    """
    with sqlite3.connect(test_db) as conn:
        conn.execute("DROP TABLE sources")
    reset_logger()

    response = TestClient(app).get("/api/sources", headers={"X-API-Key": TEST_API_KEY})

    assert response.status_code == 500
    assert "no such table" not in response.text, response.text
    assert "Failed to get sources" in response.json()["message"]

    log_dir = Path(os.environ["XDG_DATA_HOME"]) / "prismis" / "observability"
    events = [
        json.loads(line)
        for f in log_dir.glob("*.jsonl")
        for line in f.read_text().splitlines()
        if line.strip()
    ]
    errors = [e for e in events if e.get("event") == "api.error"]
    assert any("no such table" in e.get("error", "") for e in errors), (
        "the detail must still reach the operator, in the log"
    )


def _mentions(node: ast.AST, name: str) -> bool:
    return any(isinstance(n, ast.Name) and n.id == name for n in ast.walk(node))


def test_no_handler_puts_its_exception_into_a_response() -> None:
    """
    INVARIANT: In api.py, no except block builds an error or response from its exception
    BREAKS: A new handler writes `ServerError(f"...: {e}")` and the leak is back, in an
            endpoint no test happens to fail for real
    """
    tree = ast.parse(_API.read_text())
    leaks = []
    for handler in ast.walk(tree):
        if not (isinstance(handler, ast.ExceptHandler) and handler.name):
            continue
        caught = ast.unparse(handler.type) if handler.type else ""
        if caught in _SAFE_EXCEPTION_TYPES:
            continue
        for node in ast.walk(handler):
            if not isinstance(node, ast.Call):
                continue
            called = ast.unparse(node.func)
            if not called.endswith(("Error", "Response")):
                continue
            if (caught, called) in _SAFE_CONVERSIONS:
                continue
            if any(
                _mentions(arg, handler.name) for arg in [*node.args, *node.keywords]
            ):
                leaks.append(f"api.py:{node.lineno}: {called}(...{handler.name}...)")

    assert not leaks, "exception text passed into a response:\n" + "\n".join(leaks)
