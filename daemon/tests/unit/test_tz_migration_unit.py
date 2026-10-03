"""Unit tests for the tz-aware timestamp migration in init_db (user_version 1).

Invariant protected:
  - INV-STORAGE-TS-1: after init_db, every datetime column of an existing database
    holds an ISO 8601 string with an explicit offset, every row's updated_at is its
    own converted original (never the migration time), and a database the rewrite
    cannot fully convert is left untouched at user_version 0 with startup failing.

Success criteria covered: SC-1, SC-2.

The database under test is built from tests/fixtures/schema_before_tz.sql, a frozen
copy of the schema.sql text as it stood before this change. schema.sql itself now emits
the new shape, so reading it here would test a fresh database, not the upgrade.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
import sqlite_vec

from prismis_daemon.database import init_db

OLD_SCHEMA = (
    Path(__file__).parent.parent / "fixtures" / "schema_before_tz.sql"
).read_text()

TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
    "content": (
        "published_at",
        "fetched_at",
        "created_at",
        "updated_at",
        "archived_at",
    ),
    "sources": ("created_at", "updated_at", "last_fetched_at"),
    "categories": ("created_at", "updated_at"),
    "source_categories": ("created_at",),
    "embeddings": ("created_at",),
}

NAIVE_SPACE = "2026-03-04 05:06:07"
NAIVE_T_FRAC = "2026-03-04T05:06:07.123456"
AWARE = "2026-03-04T05:06:07.123456+00:00"
AWARE_OFFSET = "2026-03-04T10:06:07+05:00"
# Aware but space-separated: the shape 20,070 published_at and 193 fetched_at cells held
# on cerebro on 2026-10-02, which the first migration neither rewrote nor accepted.
AWARE_SPACE = "2026-04-30 02:38:14+00:00"
AWARE_SPACE_FRAC_OFFSET = "2026-03-04 10:06:07.5+05:00"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    return conn


def _build_old_database(
    path: Path, *, extra_content_published: str | None = None
) -> None:
    """Create a pre-change database seeded with naive and aware values in every
    timestamp column, a known distinct updated_at per content row."""
    conn = _connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.execute(
        "INSERT INTO categories (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("cat-1", "Cat One", NAIVE_SPACE, NAIVE_T_FRAC),
    )
    conn.execute(
        "INSERT INTO categories (id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("cat-2", "Cat Two", AWARE, AWARE),
    )
    conn.execute(
        "INSERT INTO sources (id, url, type, created_at, updated_at, last_fetched_at)"
        " VALUES (?, ?, 'rss', ?, ?, ?)",
        ("src-1", "https://a.example/feed", NAIVE_SPACE, NAIVE_T_FRAC, NAIVE_SPACE),
    )
    conn.execute(
        "INSERT INTO sources (id, url, type, created_at, updated_at, last_fetched_at)"
        " VALUES (?, ?, 'rss', ?, ?, ?)",
        ("src-2", "https://b.example/feed", AWARE, AWARE_OFFSET, None),
    )
    conn.execute(
        "INSERT INTO source_categories (source_id, category_id, created_at) VALUES (?, ?, ?)",
        ("src-1", "cat-1", NAIVE_T_FRAC),
    )
    conn.execute(
        "INSERT INTO source_categories (source_id, category_id, created_at) VALUES (?, ?, ?)",
        ("src-2", "cat-2", AWARE),
    )
    rows = [
        # id, published, fetched, created, updated, archived
        ("c1", NAIVE_SPACE, NAIVE_T_FRAC, NAIVE_SPACE, "2026-01-01 00:00:01", None),
        (
            "c2",
            NAIVE_T_FRAC,
            NAIVE_SPACE,
            NAIVE_T_FRAC,
            "2026-01-02T00:00:02.000002",
            NAIVE_SPACE,
        ),
        ("c3", AWARE, AWARE, AWARE, AWARE_OFFSET, AWARE),
        (
            "c4",
            extra_content_published,
            NAIVE_SPACE,
            NAIVE_SPACE,
            "2026-01-04 00:00:04",
            None,
        ),
        (
            "c5",
            AWARE_SPACE,
            AWARE_SPACE_FRAC_OFFSET,
            NAIVE_SPACE,
            "2026-01-05 00:00:05",
            AWARE_SPACE,
        ),
    ]
    for cid, pub, fet, cre, upd, arc in rows:
        conn.execute(
            "INSERT INTO content (id, source_id, external_id, title, url, published_at,"
            " fetched_at, created_at, updated_at, archived_at)"
            " VALUES (?, 'src-1', ?, 't', 'https://x.example', ?, ?, ?, ?, ?)",
            (cid, f"ext-{cid}", pub, fet, cre, upd, arc),
        )
    for cid in ("c1", "c3"):
        conn.execute(
            "INSERT INTO embeddings (content_id, embedding, model, created_at)"
            " VALUES (?, x'00', 'm', ?)",
            (cid, NAIVE_SPACE if cid == "c1" else AWARE),
        )
    conn.commit()
    conn.close()


def _dump(path: Path) -> dict[str, list[tuple]]:
    conn = _connect(path)
    try:
        return {
            table: conn.execute(f"SELECT * FROM {table} ORDER BY 1, 2").fetchall()
            for table in TIMESTAMP_COLUMNS
        }
    finally:
        conn.close()


def _all_values(path: Path) -> list[tuple[str, str, str]]:
    conn = _connect(path)
    try:
        out = []
        for table, cols in TIMESTAMP_COLUMNS.items():
            for col in cols:
                for (v,) in conn.execute(
                    f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"
                ):
                    out.append((table, col, v))
        return out
    finally:
        conn.close()


def _user_version(path: Path) -> int:
    conn = _connect(path)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()


def _backup(path: Path) -> Path:
    return path.with_name(path.name + ".bak-tz")


def test_migration_converts_every_naive_timestamp_and_keeps_updated_at(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: init_db leaves no timestamp without an offset, rewrites each naive value
               to its own aware form, and never replaces updated_at with the migration time
    BREAKS: the TUI's RFC3339 parse drops every naive row, or every row reads as just updated
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db)

    init_db(db)

    values = _all_values(db)
    assert values, "seed must produce timestamp values"
    for table, col, v in values:
        parsed = datetime.fromisoformat(v)
        assert parsed.tzinfo is not None, f"{table}.{col} left naive: {v!r}"

    conn = _connect(db)
    updated = dict(conn.execute("SELECT id, updated_at FROM content"))
    published = dict(conn.execute("SELECT id, published_at FROM content"))
    fetched = dict(conn.execute("SELECT id, fetched_at FROM content"))
    conn.close()
    # Each updated_at is its own converted original, not the migration time.
    assert updated["c1"] == "2026-01-01T00:00:01+00:00"
    assert updated["c2"] == "2026-01-02T00:00:02.000002+00:00"
    assert updated["c4"] == "2026-01-04T00:00:04+00:00"
    # Both historical shapes become RFC3339, fractional seconds kept.
    assert published["c1"] == "2026-03-04T05:06:07+00:00"
    assert published["c2"] == "2026-03-04T05:06:07.123456+00:00"
    assert fetched["c1"] == "2026-03-04T05:06:07.123456+00:00"
    assert published["c5"] == "2026-04-30T02:38:14+00:00"
    assert fetched["c5"] == "2026-03-04T10:06:07.5+05:00"
    # Already-aware values are byte-identical, including a non-UTC offset.
    assert updated["c3"] == AWARE_OFFSET
    assert published["c3"] == AWARE
    assert published["c4"] is None

    assert _user_version(db) == 1


def test_migration_writes_backup_with_pre_migration_content(tmp_path: Path) -> None:
    """
    INVARIANT: the pre-migration database is preserved beside the database before any rewrite
    BREAKS: a bad migration has no way back
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db)

    init_db(db)

    backup = _backup(db)
    assert backup.exists()
    conn = _connect(backup)
    try:
        assert conn.execute(
            "SELECT published_at FROM content WHERE id = 'c1'"
        ).fetchone() == (NAIVE_SPACE,)
    finally:
        conn.close()


def test_migration_recreated_triggers_write_offset_updated_at(tmp_path: Path) -> None:
    """
    INVARIANT: after the migration a plain UPDATE stamps updated_at with an explicit offset
    BREAKS: the TUI's direct read/feedback updates would write naive updated_at again
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db)
    init_db(db)

    conn = _connect(db)
    try:
        conn.execute("UPDATE content SET read = 1 WHERE id = 'c1'")
        conn.execute("UPDATE sources SET name = 'n' WHERE id = 'src-1'")
        conn.execute("UPDATE categories SET description = 'd' WHERE id = 'cat-1'")
        conn.commit()
        for table, key in (
            ("content", "c1"),
            ("sources", "src-1"),
            ("categories", "cat-1"),
        ):
            (value,) = conn.execute(
                f"SELECT updated_at FROM {table} WHERE id = ?", (key,)
            ).fetchone()
            assert value.endswith("+00:00"), f"{table} trigger wrote {value!r}"
            assert datetime.fromisoformat(value).tzinfo is not None
    finally:
        conn.close()


def test_migration_second_run_changes_nothing(tmp_path: Path) -> None:
    """
    INVARIANT: user_version makes the migration run once
    BREAKS: every restart re-backs-up and re-walks 130k cells
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db)
    init_db(db)
    after_first = _dump(db)
    _backup(db).unlink()

    init_db(db)

    assert _user_version(db) == 1
    assert not _backup(db).exists(), "a second run must not take a second backup"
    assert _dump(db) == after_first


def test_migration_fresh_database_is_stamped_without_backup(tmp_path: Path) -> None:
    """
    INVARIANT: a database created by this schema starts at user_version 1, nothing to back up
    BREAKS: every new install leaves an empty .bak-tz file behind
    """
    db = tmp_path / "prismis.db"

    init_db(db)

    assert _user_version(db) == 1
    assert not _backup(db).exists()


def test_migration_unconvertible_cell_fails_postcondition_and_rolls_back(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: a cell the rewrite cannot convert fails init_db naming table, column and
               count, and the whole transaction rolls back
    BREAKS: the daemon serves a half-converted database, or silently skips a bad cell
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db, extra_content_published="not-a-date")
    before = _dump(db)

    with pytest.raises(sqlite3.Error) as excinfo:
        init_db(db)

    message = str(excinfo.value)
    assert "content.published_at" in message and "1 " in message, message
    assert _user_version(db) == 0
    assert _dump(db) == before, "rollback must leave every column unchanged"
    assert _backup(db).exists()
    conn = _connect(db)
    try:
        trigger_sql = [
            sql
            for (sql,) in conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='trigger'"
            )
        ]
    finally:
        conn.close()
    assert len(trigger_sql) == 3, "rolled-back migration must keep the three triggers"
    assert all("CURRENT_TIMESTAMP" in sql for sql in trigger_sql), (
        "the DROP/CREATE TRIGGER steps must roll back with the backfill"
    )


def test_migration_stale_backup_is_kept_and_a_fresh_one_taken(tmp_path: Path) -> None:
    """
    INVARIANT: the backup always holds the state the migration is about to rewrite; a
               backup left by an earlier failed attempt is preserved, never reused
    BREAKS: the operator repairs data after a failed start, and the retry rewrites the
            database against a backup that no longer holds its pre-migration state
    """
    db = tmp_path / "prismis.db"
    _build_old_database(db, extra_content_published="not-a-date")
    with pytest.raises(sqlite3.Error):
        init_db(db)
    stale = _backup(db)
    assert stale.exists()

    # The operator repairs the bad cell, then restarts.
    conn = _connect(db)
    conn.execute("UPDATE content SET published_at = NULL WHERE id = 'c4'")
    conn.commit()
    conn.close()
    init_db(db)

    assert _user_version(db) == 1
    conn = _connect(_backup(db))
    try:
        assert conn.execute(
            "SELECT published_at FROM content WHERE id = 'c4'"
        ).fetchone() == (None,), (
            "fresh backup must hold the repaired pre-migration state"
        )
    finally:
        conn.close()
    kept = [p for p in tmp_path.glob("prismis.db.bak-tz.*")]
    assert len(kept) == 1, "the earlier backup must be kept under a timestamped name"
    conn = _connect(kept[0])
    try:
        assert conn.execute(
            "SELECT published_at FROM content WHERE id = 'c4'"
        ).fetchone() == ("not-a-date",)
    finally:
        conn.close()


def test_runner_reports_the_migration_error_when_sqlite_already_ended_the_transaction(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: when a migration fails after SQLite has already ended the transaction
               (a full disk does this), init_db raises that failure, not a rollback error
    BREAKS: the operator reads "cannot rollback - no transaction is active" and never
            learns the disk was full (cerebro, 2026-10-03)
    """
    import sqlite3

    from prismis_daemon.database import _apply_migrations

    db = tmp_path / "runner.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.commit()

    def ends_its_own_transaction_then_fails(c: sqlite3.Connection) -> None:
        c.execute("INSERT INTO t VALUES (1)")
        c.execute("ROLLBACK")  # what SQLite does itself on a full disk
        raise sqlite3.OperationalError("database or disk is full")

    with pytest.raises(sqlite3.OperationalError, match="disk is full"):
        _apply_migrations(conn, db, migrations=[ends_its_own_transaction_then_fails])

    assert conn.execute("SELECT count(*) FROM t").fetchone() == (0,)
    assert conn.execute("PRAGMA user_version").fetchone() == (0,)
    conn.close()
