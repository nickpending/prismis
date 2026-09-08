"""Unit tests for the store link's observability record.

The pipeline stores through the `create_or_update_content` method in
`daemon/src/prismis_daemon/storage.py`, not through the `add_content` method beside it
— `add_content` was already instrumented but nothing in the pipeline calls it, which is
what made the store link look covered when it was dark.

Invariants protected:
  - Creating a row emits db.insert with status "created"
  - Updating an existing row emits db.insert with status "updated" — a different answer
    from a create, not the same one
  - A database error emits db.insert with status "error" and still raises

Success criteria covered:
  SC-3 (the store link reports a real state), Principle III

Real collaborators: real Storage against the real SQLite test database.
"""

import sqlite3
import uuid

import pytest

from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.storage import Storage
from prismis_daemon.verify_chain import read_run_events


@pytest.fixture(autouse=True)
def _fresh_observability():
    """Bind the observability logger to this test's sealed data dir, and unbind after.

    The logger is a module-level singleton that caches the base directory it resolved
    when first constructed, and every test gets a different XDG_DATA_HOME — without the
    reset a test reads a directory an earlier test established (and, where that earlier
    test removed it, one that no longer exists). The run id is a module-level global for
    the same reason it is reset here: pytest runs the suite in one process.
    """
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _store_events(run_id: str) -> list[dict]:
    events = read_run_events(get_logger().base_dir, run_id)
    return [
        e
        for e in events
        if e["event"] == "db.insert"
        and e.get("operation") == "create_or_update_content"
    ]


def _item(source_id: str, external_id: str, summary: str) -> dict:
    return {
        "source_id": source_id,
        "external_id": external_id,
        "title": "A title",
        "url": "https://example.com/a",
        "content": "some content",
        "summary": summary,
        "priority": "medium",
    }


def test_create_and_update_emit_distinguishable_events(test_db) -> None:
    """
    SC-3: a created row and an updated row are two different answers in the record.
    BREAKS: the chain's store link cannot tell a first-time insert from a re-store, and
    the unattended daemon leaves no trace of either.
    """
    storage = Storage()
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test")
    external_id = f"ext-{uuid.uuid4()}"

    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    content_id, is_new = storage.create_or_update_content(
        _item(source_id, external_id, "first")
    )
    same_id, is_new_again = storage.create_or_update_content(
        _item(source_id, external_id, "second")
    )

    assert is_new is True
    assert is_new_again is False
    assert same_id == content_id

    events = _store_events(run_id)
    assert [e["status"] for e in events] == ["created", "updated"]
    for event in events:
        assert event["table"] == "content"
        assert event["row_count"] == 1
        assert isinstance(event["duration_ms"], int)


def test_database_error_emits_error_event_and_still_raises(test_db) -> None:
    """
    Principle II: a store that failed is a third answer, distinct from created and
    updated. The source id below violates the content table's foreign key, which the
    connection enforces (PRAGMA foreign_keys=ON), so this is a real sqlite3 error.

    BREAKS: a failed write looks exactly like a write that never happened.
    """
    storage = Storage()
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    with pytest.raises(sqlite3.Error) as exc_info:
        storage.create_or_update_content(
            _item("no-such-source-id", f"ext-{uuid.uuid4()}", "orphan")
        )

    assert "Failed to create or update content" in str(exc_info.value)

    events = _store_events(run_id)
    assert len(events) == 1
    assert events[0]["status"] == "error"
    assert "FOREIGN KEY" in events[0]["error"].upper()
    assert isinstance(events[0]["duration_ms"], int)
