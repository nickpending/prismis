"""Unit tests for the decidable logic in `source list` and `source remove`.

Both commands were left with zero coverage when the SQLite-era integration tests were
deleted. The parts that hold a real decision are called directly here — no mocks, per
the constitution's tests-prove-logic article ("Internal code ... never faked") and llm-providers-only-external-boundary (LLM providers are
the only permitted mock). The `APIClient` calls that remain in those commands are
one-line pass-throughs and are recorded as glue in the plan's Constitution Check table.

The health tests at the end drive the real `source keep` and `source list` commands
against a real daemon (`live_daemon`, conftest.py) whose sources a real daemon cycle
classified, fetching feeds from a local HTTP server.
"""

import http.server
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import pytest
from prismis_daemon.config import Config
from prismis_daemon.storage import Storage
from prismis_daemon.verify_chain import build_orchestrator
from rich.console import Console
from typer.testing import CliRunner

from cli.api_client import APIClient
from cli.source import app as source_app
from cli.source import find_source_by_id, format_source_row

from conftest import LiveDaemon


# ---------------------------------------------------------------------------
# remove: the find_source_by_id helper in cli/src/cli/source.py
#
# INVARIANT: removal only ever targets a source that exists.
# BREAKS: `remove` is destructive and cascades to all content from the source. A lookup
# that returned the wrong row, or a falsy row treated as "not found", deletes the wrong
# thing or refuses to delete the right one.
# ---------------------------------------------------------------------------


def test_find_source_by_id_returns_the_matching_source() -> None:
    sources = [
        {"id": "a", "name": "First"},
        {"id": "b", "name": "Second"},
        {"id": "c", "name": "Third"},
    ]
    assert find_source_by_id(sources, "b") == {"id": "b", "name": "Second"}


def test_find_source_by_id_returns_none_when_absent() -> None:
    """The not-found path: `remove` turns this into exit 1 without deleting anything."""
    sources = [{"id": "a", "name": "First"}]
    assert find_source_by_id(sources, "definitely-not-there") is None


def test_find_source_by_id_on_empty_list() -> None:
    assert find_source_by_id([], "a") is None


def test_find_source_by_id_returns_the_first_match_only() -> None:
    """Ids are UUIDs and unique; if the API ever returns duplicates, take the first."""
    sources = [{"id": "a", "name": "First"}, {"id": "a", "name": "Shadow"}]
    found = find_source_by_id(sources, "a")
    assert found is not None
    assert found["name"] == "First"


def test_find_source_by_id_does_not_match_on_substring() -> None:
    """A prefix of a real id must not resolve — that would delete the wrong source."""
    sources = [{"id": "abc123", "name": "Real"}]
    assert find_source_by_id(sources, "abc") is None


# ---------------------------------------------------------------------------
# list: the format_source_row helper in cli/src/cli/source.py — truncation and columns
# ---------------------------------------------------------------------------


def test_format_source_row_truncates_a_long_name() -> None:
    """Names over 25 chars are cut to 22 plus an ellipsis, so the column stays aligned."""
    source = {"id": "1", "type": "rss", "name": "x" * 40, "active": True}
    _, _, name, _, _, _, _, _ = format_source_row(source)
    assert name == "x" * 22 + "..."
    assert len(name) == 25


def test_format_source_row_leaves_a_boundary_name_intact() -> None:
    """Exactly 25 is not 'too long' — the boundary must not truncate."""
    source = {"id": "1", "type": "rss", "name": "y" * 25, "active": True}
    assert format_source_row(source)[2] == "y" * 25


def test_format_source_row_renders_an_unnamed_source() -> None:
    source = {"id": "1", "type": "rss", "name": None, "active": True}
    assert format_source_row(source)[2] == "Unnamed"


@pytest.mark.parametrize(
    ("active", "expected"), [(True, "✅ Yes"), (False, "❌ No"), (None, "❌ No")]
)
def test_format_source_row_renders_active_state(active, expected: str) -> None:
    source = {"id": "1", "type": "rss", "name": "n", "active": active}
    assert format_source_row(source)[3] == expected


def test_format_source_row_shows_a_dash_for_zero_errors() -> None:
    """Zero errors reads as '—', not '0' — an em dash is the empty-success rendering."""
    source = {"id": "1", "type": "rss", "name": "n", "active": True, "error_count": 0}
    assert format_source_row(source)[4] == "—"


def test_format_source_row_shows_the_error_count_when_nonzero() -> None:
    source = {"id": "1", "type": "rss", "name": "n", "active": True, "error_count": 3}
    assert format_source_row(source)[4] == "3"


def test_format_source_row_truncates_the_timestamp_to_seconds() -> None:
    source = {
        "id": "1",
        "type": "rss",
        "name": "n",
        "active": True,
        "last_fetched": "2026-09-04T09:30:01.123456+00:00",
    }
    assert format_source_row(source)[5] == "2026-09-04T09:30:01"


def test_format_source_row_renders_a_never_fetched_source() -> None:
    source = {
        "id": "1",
        "type": "rss",
        "name": "n",
        "active": True,
        "last_fetched": None,
    }
    assert format_source_row(source)[5] == "Never"


@pytest.mark.parametrize(
    ("source_fields", "expected"),
    [
        pytest.param({"naughty": True}, "browser", id="marked"),
        pytest.param({"naughty": False}, "prismis", id="unmarked"),
        pytest.param({}, "prismis", id="daemon-without-the-field"),
    ],
)
def test_format_source_row_names_the_user_agent_the_source_sends(
    source_fields: dict, expected: str
) -> None:
    """The seventh column names which user-agent the daemon sends for the source; a
    daemon older than the flag sends no `naughty`, and its sources send prismis's."""
    source = {"id": "1", "type": "rss", "name": "n", "active": True, **source_fields}
    assert format_source_row(source)[6] == expected


@pytest.mark.parametrize(
    ("source_fields", "expected"),
    [
        pytest.param({"health": "broken"}, "broken", id="flagged"),
        pytest.param(
            {"health": "dormant", "kept_health": "dormant"}, "dormant (kept)", id="kept"
        ),
        pytest.param(
            {"health": "empty", "kept_health": "dormant"}, "empty", id="stale-keep"
        ),
        pytest.param({"health": None}, "—", id="unclassified"),
        pytest.param({}, "—", id="daemon-without-the-field"),
    ],
)
def test_format_source_row_shows_the_stored_health(
    source_fields: dict, expected: str
) -> None:
    """The eighth column is the daemon's stored health, marked kept only when the keep
    answers that same state; a daemon older than health sends none."""
    source = {"id": "1", "type": "rss", "name": "n", "active": True, **source_fields}
    assert format_source_row(source)[7] == expected


# ---------------------------------------------------------------------------
# keep and list against a real daemon whose cycle classified the sources
# ---------------------------------------------------------------------------

_runner = CliRunner()


def _feed(name: str, dates: list[datetime]) -> bytes:
    items = "".join(
        f"<item><title>{name} {i}</title><link>https://example.com/{name}/{i}</link>"
        f'<guid isPermaLink="false">{name}-{i}</guid>'
        f"<pubDate>{format_datetime(date)}</pubDate></item>"
        for i, date in enumerate(dates)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{name}</title><link>https://example.com/</link>"
        f"<description>local</description>{items}</channel></rss>"
    ).encode()


@pytest.fixture
def feeds() -> Iterator[tuple[str, dict[str, bytes]]]:
    """A loopback server answering GET /<name>.xml with routes[name] (404 when absent)."""
    routes: dict[str, bytes] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            return

        def do_GET(self) -> None:
            body = routes.get(self.path.removeprefix("/").removesuffix(".xml"))
            if body is None:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[0], server.server_address[1]
    try:
        yield f"http://{host!s}:{port}", routes
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _cycle(live_daemon: LiveDaemon) -> None:
    """One real daemon cycle over the live daemon's database."""
    storage = Storage(live_daemon.db_path)
    try:
        build_orchestrator(Config.from_file(), storage, Console(quiet=True)).run_once()
    finally:
        storage.close()


def _add(live_daemon: LiveDaemon, base_url: str, name: str) -> str:
    storage = Storage(live_daemon.db_path)
    try:
        return storage.add_source(f"{base_url}/{name}.xml", "rss", name)
    finally:
        storage.close()


def _invoke(*args: str) -> str:
    result = _runner.invoke(source_app, list(args), env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.stdout


def _listed(source_id: str) -> dict:
    (source,) = [s for s in APIClient().get_sources() if s["id"] == source_id]
    return source


# Older than the default 30-day lookback, newer than 90 days: served, nothing stored.
_ALIVE = [datetime.now(UTC) - timedelta(days=d) for d in (40, 50)]


def test_source_keep_silences_an_empty_source_until_its_health_changes(
    live_daemon: LiveDaemon, feeds: tuple[str, dict[str, bytes]]
) -> None:
    """
    INVARIANT: `source keep <id>` on a source a cycle classified empty exits 0, the API
               then lists it kept_health empty with attention false, and a cycle in
               which the feed still serves nothing leaves attention false
    BREAKS: the operator cannot answer a warning from the CLI, or the next cycle
            raises the warning they just answered
    """
    base_url, routes = feeds
    routes["hollow"] = _feed("hollow", [])
    source_id = _add(live_daemon, base_url, "hollow")
    _cycle(live_daemon)
    flagged = _listed(source_id)
    assert (flagged.get("health"), flagged.get("attention")) == ("empty", True)

    _invoke("keep", source_id)

    kept = _listed(source_id)
    assert (kept.get("kept_health"), kept.get("attention")) == ("empty", False)
    _cycle(live_daemon)
    assert _listed(source_id).get("attention") is False


def test_source_list_has_a_health_column(
    live_daemon: LiveDaemon, feeds: tuple[str, dict[str, bytes]]
) -> None:
    """
    INVARIANT: `source list` shows a Health column reading healthy, empty and
               empty (kept) for sources a cycle classified healthy, empty, and empty
               then kept
    BREAKS: the CLI shows only an error count, and a quiet feed reads as fine
    """
    base_url, routes = feeds
    routes["alive"] = _feed("alive", _ALIVE)
    routes["hollow"] = _feed("hollow", [])
    routes["hushed"] = _feed("hushed", [])
    ids = {name: _add(live_daemon, base_url, name) for name in routes}
    _cycle(live_daemon)
    _invoke("keep", ids["hushed"])

    table = _invoke("list")

    assert "Health" in table
    cells = {}
    for name, source_id in ids.items():
        (row,) = [line for line in table.splitlines() if source_id in line]
        cells[name] = row.split("│")[-2].strip()
    assert cells == {"alive": "healthy", "hollow": "empty", "hushed": "empty (kept)"}
