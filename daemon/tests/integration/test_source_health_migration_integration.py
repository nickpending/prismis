"""The source-health migration in init_db (user_version 4).

Invariants protected:
  - opening a user_version 3 database turns back on exactly the sources the five-strike
    rule switched off (active 0 with error_count >= 5), leaves a source the operator
    paused paused, backfills each source's newest_item_at from its stored items, writes
    a backup beside the file and stamps user_version 4, changing no other cell
  - a migrated sources table has the columns and the attention trigger a fresh one has

The database under test is built from tests/fixtures/schema_before_source_health.sql, a
frozen copy of schema.sql as it stood before this change, stamped user_version 3 the
way the previous release left every database it opened.
"""

import sqlite3
from pathlib import Path

import sqlite_vec

from prismis_daemon.database import init_db

OLD_SCHEMA = (
    Path(__file__).parent.parent / "fixtures" / "schema_before_source_health.sql"
).read_text()

_SOURCE_COLUMNS = (
    "id, url, type, name, active, error_count, last_error, last_fetched_at,"
    " created_at, updated_at, naughty"
)

_STRUCK_OFF = "src-a"
_PAUSED = "src-b"
_LIVE = "src-c"
_NEWEST_PUBLISHED = "2026-10-20T08:30:00+00:00"

_OLD_SOURCES = [
    (
        _STRUCK_OFF,
        "https://a.example/feed",
        "rss",
        "Struck off",
        0,
        5,
        "HTTP 404",
        "2026-09-01T00:00:00+00:00",
        "2026-08-01T00:00:00+00:00",
        "2026-09-02T00:00:00.123456+00:00",
        0,
    ),
    (
        _PAUSED,
        "https://b.example/feed",
        "rss",
        "Paused by the operator",
        0,
        0,
        None,
        "2026-10-01T00:00:00+00:00",
        "2026-08-02T00:00:00+00:00",
        "2026-10-02T00:00:00+00:00",
        1,
    ),
    (
        _LIVE,
        "https://c.example/feed",
        "rss",
        "Live",
        1,
        0,
        None,
        "2026-10-21T00:00:00+00:00",
        "2026-08-03T00:00:00+00:00",
        "2026-10-21T00:00:00+00:00",
        0,
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
        f"INSERT INTO sources ({_SOURCE_COLUMNS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",  # nosec B608 -- the column list is this test's own constant
        _OLD_SOURCES,
    )
    for content_id, published in (
        ("c-old", "2026-10-01T12:00:00+00:00"),
        ("c-new", _NEWEST_PUBLISHED),
    ):
        conn.execute(
            "INSERT INTO content (id, source_id, external_id, title, url, published_at,"
            " created_at, updated_at) VALUES (?, ?, ?, 'Item', 'https://c.example/x',"
            " ?, '2026-10-21T00:00:00+00:00', '2026-10-21T00:00:00+00:00')",
            (content_id, _LIVE, f"ext-{content_id}", published),
        )
    conn.execute("PRAGMA user_version = 3")
    conn.commit()
    rows = conn.execute("SELECT * FROM content ORDER BY id").fetchall()
    conn.close()
    return rows


def _rows(path: Path, sql: str) -> list[tuple]:
    conn = _connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


def test_opening_a_previous_release_database_reactivates_only_struck_off_sources(
    tmp_path: Path,
) -> None:
    """
    INVARIANT: init_db on a user_version 3 database sets active 1 on the source with
               active 0 and error_count 5, leaves the operator-paused source at active
               0, backfills newest_item_at from the newest stored item, leaves every
               other cell as it was, writes a backup and stamps user_version 4
    BREAKS: the source the five-strike rule switched off stays off forever with
            nothing saying so, or the upgrade resumes a source the operator paused
    """
    db = tmp_path / "prismis.db"
    content_before = _build_previous_release_database(db)

    init_db(db)

    assert _rows(db, "PRAGMA user_version") == [(4,)]
    assert db.with_name(db.name + ".bak-tz").exists(), "no backup beside the database"
    kept = _rows(db, f"SELECT {_SOURCE_COLUMNS} FROM sources ORDER BY id")  # nosec B608 -- the column list is this test's own constant
    expected = [
        (row[:4] + (1,) + row[5:]) if row[0] == _STRUCK_OFF else row
        for row in _OLD_SOURCES
    ]
    assert kept == expected
    newest = dict(_rows(db, "SELECT id, newest_item_at FROM sources"))
    assert newest == {_STRUCK_OFF: None, _PAUSED: None, _LIVE: _NEWEST_PUBLISHED}
    assert _rows(db, "SELECT * FROM content ORDER BY id") == content_before


def test_a_migrated_sources_table_matches_a_fresh_one(tmp_path: Path) -> None:
    """
    INVARIANT: the sources columns and triggers the migration produces equal those
               schema.sql creates, in order
    BREAKS: upgraded and new installs disagree about what a source row holds, or one of
            them computes attention and the other never does
    """
    fresh = tmp_path / "fresh.db"
    init_db(fresh)
    migrated = tmp_path / "migrated.db"
    _build_previous_release_database(migrated)
    init_db(migrated)

    def shape(path: Path) -> tuple[list[tuple], list[tuple]]:
        return (
            _rows(path, "PRAGMA table_info(sources)"),
            _rows(
                path,
                "SELECT name FROM sqlite_master WHERE type = 'trigger'"
                " AND tbl_name = 'sources' ORDER BY name",
            ),
        )

    assert shape(migrated) == shape(fresh)
    assert ("update_sources_attention",) in shape(fresh)[1]
