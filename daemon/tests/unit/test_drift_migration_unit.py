"""Unit tests for the drift_checks table's migration and schema (C7).

The database "from before this change" is built by taking a fresh database and
removing the one thing this change adds: the table, and the user_version stamp that
says it is there.

Success criteria covered: C7.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

from prismis_daemon.database import _MIGRATIONS, _apply_migrations, init_db


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    return conn


def _columns(path: Path) -> list[tuple]:
    conn = _connect(path)
    try:
        return conn.execute("PRAGMA table_info(drift_checks)").fetchall()
    finally:
        conn.close()


def _previous_database(path: Path) -> list[tuple]:
    """Build a database as it stood before drift_checks; return its content rows."""
    init_db(path)
    conn = _connect(path)
    conn.execute("DROP TABLE drift_checks")
    conn.execute("PRAGMA user_version = 1")
    conn.executemany(
        "INSERT INTO content (id, external_id, title, url, priority, created_at)"
        " VALUES (?, ?, ?, 'https://example.com', ?, '2026-05-01T00:00:00+00:00')",
        [("c1", "e1", "one", None), ("c2", "e2", "two", "high")],
    )
    conn.commit()
    rows = conn.execute("SELECT * FROM content ORDER BY id").fetchall()
    conn.close()
    return rows


def test_starting_an_existing_database_adds_the_table_and_keeps_every_row(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: init_db on a database without drift_checks adds it, stamps the new
               version, and leaves every existing content row byte-for-byte as it was
    BREAKS: upgrading the daemon loses or rewrites a user's items
    """
    db = tmp_path / "prismis.db"
    before = _previous_database(db)

    init_db(db)

    conn = _connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS) == 2
        assert conn.execute("SELECT COUNT(*) FROM drift_checks").fetchone()[0] == 0
        assert conn.execute("SELECT * FROM content ORDER BY id").fetchall() == before
    finally:
        conn.close()


def test_the_migration_itself_creates_the_table(tmp_path: Path) -> None:
    """
    INVARIANT: the numbered migration, run on its own with no schema.sql beforehand,
               creates drift_checks; it does not lean on the schema script having run
    BREAKS: a database whose schema.sql step is skipped or reordered gets the version
            bump with no table
    """
    db = tmp_path / "prismis.db"
    _previous_database(db)
    conn = _connect(db)
    try:
        assert (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'drift_checks'"
            ).fetchone()
            is None
        )

        _apply_migrations(conn, db)

        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM drift_checks").fetchone()[0] == 0
    finally:
        conn.close()


def test_a_fresh_database_has_the_same_table_as_a_migrated_one(tmp_path: Path) -> None:
    """
    INVARIANT: the table schema.sql builds and the one the migration builds have the
               same columns, types, nullability and defaults
    BREAKS: new installs and upgraded installs disagree about what drift_checks holds
    """
    fresh = tmp_path / "fresh.db"
    init_db(fresh)
    migrated = tmp_path / "migrated.db"
    _previous_database(migrated)
    conn = _connect(migrated)
    _apply_migrations(conn, migrated)
    conn.close()

    fresh_columns = _columns(fresh)

    assert fresh_columns
    assert [c[1] for c in fresh_columns] == [
        "id",
        "checked_at",
        "window_items",
        "window_null",
        "share",
        "baseline",
        "baseline_windows",
        "deviation",
        "threshold",
        "flagged",
        "reason",
    ]
    assert _columns(migrated) == fresh_columns


def test_a_fresh_database_is_stamped_at_the_latest_version(tmp_path: Path) -> None:
    """
    INVARIANT: a database built from schema.sql is stamped len(_MIGRATIONS) so the
               migrations never re-run on it
    BREAKS: every new install backs itself up and re-migrates on its first restart
    """
    db = tmp_path / "fresh.db"
    init_db(db)

    conn = _connect(db)
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == len(_MIGRATIONS)
    finally:
        conn.close()
