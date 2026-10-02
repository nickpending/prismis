"""Unit tests for the storage timestamp invariant (INV-STORAGE-TS-1).

Invariant protected:
  - every datetime column the schema declares holds an ISO 8601 string with an explicit
    offset after any write path runs, and no source or schema text writes the
    CURRENT_TIMESTAMP literal

Success criteria covered: SC-3.

The columns are enumerated from sqlite_master at test time, so a column added to the
schema later is held to the invariant without editing this file. Each write path runs
twice: with the update triggers in place, and with them dropped. The triggers rewrite
updated_at on every UPDATE, so with them in place a storage site that still wrote a
naive updated_at would be masked; dropping them leaves the site's own value standing.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conftest import add_new_content
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage, utc_now_iso

_SRC = Path(__file__).parent.parent.parent / "src" / "prismis_daemon"


def _timestamp_columns(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Every (table, column) typed TIMESTAMP or named *_at, across every table."""
    found = []
    tables = [
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    ]
    for table in tables:
        for _cid, name, col_type, *_rest in conn.execute(
            f"PRAGMA table_info('{table}')"
        ):
            if col_type.upper() == "TIMESTAMP" or name.endswith("_at"):
                found.append((table, name))
    return found


def _item(source_id: str, ext: str, priority: str = "high") -> ContentItem:
    return ContentItem(
        source_id=source_id,
        external_id=ext,
        title=f"Title {ext}",
        url=f"https://example.com/{ext}",
        content="body",
        summary="s",
        analysis={"summary": "s"},
        priority=priority,
        published_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    )


def _assert_all_aware(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """Fail on any timestamp value without an offset; return the columns enumerated."""
    columns = _timestamp_columns(conn)
    for table, column in columns:
        for (value,) in conn.execute(
            f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL"
        ):
            parsed = datetime.fromisoformat(value)
            assert parsed.tzinfo is not None, f"{table}.{column} holds naive {value!r}"
    return columns


def _exercise_every_write_path(storage: Storage) -> None:
    """Run every public write path, checking every timestamp column after each one.

    Checking after each step matters: several paths write the same updated_at cell
    (update_source twice, pause then resume), so a naive value from an earlier path
    would be overwritten by a later one and only a per-step check can see it.
    """
    conn = storage.conn

    def check() -> None:
        _assert_all_aware(conn)

    src = storage.add_source("https://example.com/feed", "rss", "Feed")
    check()
    storage.update_source(src, {"name": "Renamed", "url": "https://example.com/feed2"})
    check()
    storage.update_source(src, {"name": "Renamed again"})
    check()
    storage.update_source(src, {"url": "https://example.com/feed3"})
    check()
    storage.update_source_fetch_status(src, success=True)
    check()
    storage.update_source_fetch_status(src, success=False, error_message="boom")
    check()
    storage.pause_source(src)
    check()
    storage.resume_source(src)
    check()

    first = add_new_content(storage, _item(src, "one"))
    check()
    second = add_new_content(storage, _item(src, "two", priority="low"))
    created, _was_new = storage.create_or_update_content(_item(src, "three"))
    assert created
    check()
    # Second pass over an existing external_id takes the UPDATE branch.
    storage.create_or_update_content(_item(src, "three"))
    check()
    storage.update_analysis(first, {"summary": "new"})
    check()
    storage.mark_content_read(first)
    check()
    storage.update_content_status(second, user_feedback="up")
    check()
    storage.add_embedding(first, [0.1] * 384, "model-x")
    check()

    # Backdate so archive_old_content selects the low-priority item.
    conn.execute(
        "UPDATE content SET fetched_at = '2000-01-01T00:00:00+00:00' WHERE id = ?",
        (second,),
    )
    conn.commit()
    archived = storage.archive_old_content(
        {
            "high_read": None,
            "medium_unread": 1,
            "medium_read": 1,
            "low_unread": 1,
            "low_read": 1,
        }
    )
    assert archived == 1
    check()

    # Direct INSERTs relying on column defaults.
    conn.execute("INSERT INTO categories (id, name) VALUES ('cat-d', 'Default')")
    conn.execute(
        "INSERT INTO source_categories (source_id, category_id) VALUES (?, 'cat-d')",
        (src,),
    )
    conn.execute(
        "INSERT INTO sources (id, url, type) VALUES ('src-d', 'https://d.example', 'rss')"
    )
    conn.execute(
        "INSERT INTO content (id, external_id, title, url)"
        " VALUES ('c-d', 'ext-d', 't', 'https://d.example/x')"
    )
    conn.commit()
    check()


@pytest.mark.parametrize("triggers", [True, False], ids=["with_triggers", "no_triggers"])
def test_every_timestamp_column_is_tz_aware_after_every_write_path(
    test_db: Path, triggers: bool
) -> None:
    """
    INVARIANT: every timestamp column holds a value with tzinfo after each storage write
    BREAKS: a naive row reaches the TUI, whose RFC3339 parse drops it
    """
    storage = Storage(test_db)
    try:
        if not triggers:
            for name in (
                "update_sources_timestamp",
                "update_categories_timestamp",
                "update_content_timestamp",
            ):
                storage.conn.execute(f"DROP TRIGGER {name}")
        _exercise_every_write_path(storage)

        columns = _assert_all_aware(storage.conn)
        assert ("content", "archived_at") in columns, "enumeration must see content"
        assert len(columns) >= 12, f"expected at least the twelve known columns: {columns}"
        for table, column in columns:
            (written,) = storage.conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {column} IS NOT NULL"
            ).fetchone()
            assert written, f"{table}.{column} was never written by the exercised paths"
    finally:
        storage.close()


def test_utc_now_iso_is_aware_utc() -> None:
    """
    INVARIANT: the single storage timestamp source returns UTC with an explicit offset
    BREAKS: a writer binding a naive string
    """
    value = utc_now_iso()
    parsed = datetime.fromisoformat(value)
    assert parsed.utcoffset() is not None
    assert value.endswith("+00:00")


def test_no_current_timestamp_literal_in_sql_or_schema() -> None:
    """
    INVARIANT: no SQL in the daemon source nor schema.sql writes CURRENT_TIMESTAMP
    BREAKS: a write site silently reintroduces naive UTC strings
    """
    offenders = [
        str(path.relative_to(_SRC))
        for path in sorted(_SRC.rglob("*"))
        if path.suffix in {".py", ".sql"}
        and "CURRENT_TIMESTAMP" in path.read_text()
    ]
    assert offenders == []
