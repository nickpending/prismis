"""The daemon's entry point upgrades its own database before it fetches or serves.

Invariant protected:
  - INV-STORAGE-TS-1 at startup: running the daemon (`--once`) against a pre-change
    database with naive rows leaves user_version 1 and zero naive cells, and the
    conversion happened before the first fetch wrote anything.
  - A database the migration cannot convert stops startup before any fetch.

Success criteria covered: SC-4.

Driven through the real typer app in `prismis_daemon.__main__`, not by calling init_db.
The only stand-in is the LLM/feed stub server (`local_pipeline_stub`), the one
collaborator the constitution permits faking. Ordering is observed from the backup:
`init_db` writes it with VACUUM INTO before it rewrites anything, so if the backup holds
none of the rows the fetch produced while the final database does, the migration ran
first.
"""

import os
import sqlite3
from pathlib import Path
from unittest.mock import patch

import sqlite_vec
from typer.testing import CliRunner

from conftest import configure_local_services
from prismis_daemon.__main__ import app

# The provider boundary: the stub serves chat completions and the feed but not the
# model list that llm_client.health_check reads, so the startup health check is answered
# here, the same way the other startup tests do.
_HEALTH_CHECK_MOCK = (
    "prismis_daemon.llm_validator.llm_client.health_check"  # claudex-guard: allow-mock
)

_OLD_SCHEMA = (
    Path(__file__).parent.parent / "fixtures" / "schema_before_tz.sql"
).read_text()

_TIMESTAMP_COLUMNS = {
    "content": ("published_at", "fetched_at", "created_at", "updated_at", "archived_at"),
    "sources": ("created_at", "updated_at", "last_fetched_at"),
    "categories": ("created_at", "updated_at"),
    "source_categories": ("created_at",),
    "embeddings": ("created_at",),
}


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    conn.enable_load_extension(False)
    return conn


def _seed_old_database(feed_url: str, *, poison: bool = False) -> Path:
    """A pre-change database under XDG_DATA_HOME with a source and a content row, both naive."""
    db = Path(os.environ["XDG_DATA_HOME"]) / "prismis" / "prismis.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = _connect(db)
    conn.executescript(_OLD_SCHEMA)
    conn.execute(
        "INSERT INTO sources (id, url, type, created_at, updated_at, last_fetched_at)"
        " VALUES ('src-old', ?, 'rss', '2026-01-01 00:00:00', '2026-01-01 00:00:00',"
        " '2026-01-01 00:00:00')",
        (feed_url,),
    )
    conn.execute(
        "INSERT INTO content (id, source_id, external_id, title, url, published_at,"
        " fetched_at, created_at, updated_at)"
        " VALUES ('old-1', 'src-old', 'old-ext', 'Old', 'https://old.example',"
        " ?, '2026-01-01 00:00:00', '2026-01-01 00:00:00', '2026-01-01 00:00:00')",
        ("not-a-date" if poison else "2026-01-01 00:00:00",),
    )
    conn.commit()
    conn.close()
    return db


def _naive_cells(path: Path) -> list[tuple[str, str, str]]:
    conn = _connect(path)
    try:
        naive = []
        for table, cols in _TIMESTAMP_COLUMNS.items():
            for col in cols:
                for (value,) in conn.execute(
                    f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL"
                ):
                    if not (value.endswith("Z") or value[-6] in "+-"):
                        naive.append((table, col, value))
        return naive
    finally:
        conn.close()


def _scalar(path: Path, sql: str) -> int:
    conn = _connect(path)
    try:
        return conn.execute(sql).fetchone()[0]
    finally:
        conn.close()


def test_startup_once_migrates_naive_database_before_first_fetch(
    local_pipeline_stub: str,
) -> None:
    """
    INVARIANT: `prismis-daemon --once` on a pre-change database leaves user_version 1 and
               zero naive cells, converted before the fetch wrote its rows
    BREAKS: the first request or fetch after deploy runs against naive rows the TUI cannot parse
    """
    configure_local_services(
        Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub
    )
    db = _seed_old_database(f"{local_pipeline_stub}/feed.xml")
    assert _naive_cells(db), "precondition: the seeded database holds naive cells"

    with patch(_HEALTH_CHECK_MOCK):
        result = CliRunner().invoke(app, ["--once"])

    assert result.exit_code == 0, result.output
    assert _scalar(db, "PRAGMA user_version") == 1
    assert _naive_cells(db) == []

    fetched_rows = _scalar(db, "SELECT COUNT(*) FROM content WHERE id != 'old-1'")
    assert fetched_rows >= 1, "the --once run must have fetched the stub feed's items"
    backup = db.with_name(db.name + ".bak-tz")
    assert backup.exists(), "the migration must have backed up before rewriting"
    assert _scalar(backup, "SELECT COUNT(*) FROM content WHERE id != 'old-1'") == 0, (
        "the backup holds rows the fetch wrote: the fetch ran before the migration"
    )
    assert _scalar(
        backup, "SELECT COUNT(*) FROM content WHERE created_at = '2026-01-01 00:00:00'"
    ) == 1, "the backup must hold the pre-migration naive row"


def test_startup_once_unconvertible_database_stops_before_any_fetch(
    local_pipeline_stub: str,
) -> None:
    """
    INVARIANT: a database the migration cannot convert fails startup before a fetch runs
    BREAKS: the daemon serves and fetches against a half-converted database
    """
    configure_local_services(
        Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub
    )
    db = _seed_old_database(f"{local_pipeline_stub}/feed.xml", poison=True)

    with patch(_HEALTH_CHECK_MOCK):
        result = CliRunner().invoke(app, ["--once"])

    assert result.exit_code != 0, result.output
    assert "content.published_at" in result.output
    assert _scalar(db, "PRAGMA user_version") == 0
    assert _scalar(db, "SELECT COUNT(*) FROM content") == 1, (
        "no fetch may run after a failed migration"
    )
    assert (
        _scalar(db, "SELECT COUNT(*) FROM sources WHERE last_fetched_at = '2026-01-01 00:00:00'")
        == 1
    ), "the source must not have been fetched"

