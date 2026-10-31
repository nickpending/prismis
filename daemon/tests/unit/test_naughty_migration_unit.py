"""Unit tests for the sources.naughty migration in init_db (user_version 3).

Invariant protected:
  - an existing database gains sources.naughty with every source row's fields intact
    and the flag reading 0, a backup beside it, and user_version 3; a database created
    from schema.sql already carries the column and runs no migration

Success criteria covered: C3.

The database under test is built from tests/fixtures/schema_before_naughty.sql, a frozen
copy of schema.sql as it stood before this change, stamped user_version 2 the way the
previous release left every database it opened. Storage's source reads select the
column, so they are the reads that fail on a database the migration skipped.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

from prismis_daemon.database import init_db
from prismis_daemon.storage import Storage

OLD_SCHEMA = (
    Path(__file__).parent.parent / "fixtures" / "schema_before_naughty.sql"
).read_text()

_SOURCE_FIELDS = (
    "id, url, type, name, active, error_count, last_error,"
    " last_fetched_at, created_at, updated_at"
)

_OLD_SOURCES = [
    (
        "src-1",
        "https://a.example/feed",
        "rss",
        "Feed A",
        1,
        0,
        None,
        "2026-10-01T00:00:00+00:00",
        "2026-09-01T00:00:00+00:00",
        "2026-09-02T00:00:00+00:00",
    ),
    (
        "src-2",
        "https://www.reddit.com/r/python",
        "reddit",
        "r/python",
        0,
        5,
        "HTTP 403",
        None,
        "2026-08-01T00:00:00+00:00",
        "2026-08-05T00:00:00.123456+00:00",
    ),
]


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    return conn


def _build_previous_release_database(path: Path) -> list[tuple]:
    """A database as the previous release left it; returns its content rows."""
    conn = _connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.executemany(
        f"INSERT INTO sources ({_SOURCE_FIELDS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # nosec B608 -- the column list is this test's own constant
        _OLD_SOURCES,
    )
    conn.execute(
        "INSERT INTO content (id, source_id, external_id, title, url, created_at,"
        " updated_at) VALUES ('c1', 'src-1', 'e1', 'One', 'https://a.example/1',"
        " '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')"
    )
    conn.execute("PRAGMA user_version = 2")
    conn.commit()
    rows = conn.execute("SELECT * FROM content ORDER BY id").fetchall()
    conn.close()
    return rows


def _scalar(path: Path, sql: str) -> object:
    conn = _connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_opening_a_previous_release_database_adds_naughty_and_keeps_every_source(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: init_db on a user_version 2 database adds sources.naughty, reads 0 for
               every existing source, keeps every other field and content row, writes a
               backup beside the file, and stamps user_version 3
    BREAKS: the upgraded daemon's first source read raises on the missing column and
            no source is fetched, or the upgrade rewrites a user's sources
    """
    db = tmp_path / "prismis.db"
    content_before = _build_previous_release_database(db)

    init_db(db)

    storage = Storage(db)
    try:
        sources = {s["id"]: s for s in storage.get_all_sources()}
    finally:
        storage.close()
    assert sources["src-1"]["naughty"] is False
    assert sources["src-2"]["naughty"] is False
    assert sources["src-2"]["last_error"] == "HTTP 403"
    assert sources["src-2"]["error_count"] == 5

    assert _scalar(db, "PRAGMA user_version") == 3
    assert db.with_name(db.name + ".bak-tz").exists(), "no backup beside the database"
    conn = _connect(db)
    try:
        kept = conn.execute(
            f"SELECT {_SOURCE_FIELDS} FROM sources ORDER BY id"  # nosec B608 -- the column list is this test's own constant
        ).fetchall()
        flags = conn.execute("SELECT naughty FROM sources ORDER BY id").fetchall()
        content_after = conn.execute("SELECT * FROM content ORDER BY id").fetchall()
    finally:
        conn.close()
    assert kept == _OLD_SOURCES
    assert flags == [(0,), (0,)]
    assert content_after == content_before


def test_a_fresh_database_has_the_column_and_runs_no_migration(tmp_path: Path) -> None:
    """
    INVARIANT: a database created from schema.sql carries sources.naughty NOT NULL
               DEFAULT 0, is stamped user_version 3, and leaves no backup
    BREAKS: every new install backs itself up and migrates on its first start, or a
            fresh database lacks the column the source reads select
    """
    db = tmp_path / "prismis.db"

    init_db(db)

    assert _scalar(db, "PRAGMA user_version") == 3
    assert not db.with_name(db.name + ".bak-tz").exists()
    conn = _connect(db)
    try:
        columns = {
            row[1]: row for row in conn.execute("PRAGMA table_info(sources)").fetchall()
        }
    finally:
        conn.close()
    _cid, _name, col_type, not_null, default, _pk = columns["naughty"]
    assert (col_type, not_null, default) == ("INTEGER", 1, "0")


def test_a_migrated_sources_table_matches_a_fresh_one(tmp_path: Path) -> None:
    """
    INVARIANT: the sources columns the migration produces equal schema.sql's, in order
    BREAKS: upgraded and new installs disagree about what a source row holds
    """
    fresh = tmp_path / "fresh.db"
    init_db(fresh)
    migrated = tmp_path / "migrated.db"
    _build_previous_release_database(migrated)
    init_db(migrated)

    def columns(path: Path) -> list[tuple]:
        conn = _connect(path)
        try:
            return conn.execute("PRAGMA table_info(sources)").fetchall()
        finally:
            conn.close()

    assert columns(migrated) == columns(fresh)
