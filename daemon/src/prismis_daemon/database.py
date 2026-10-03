"""Database initialization for Prismis daemon."""

import os
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Optional

# Every datetime column the schema declares, per table. Migration 1 rewrites and then
# audits exactly these columns.
_TIMESTAMP_COLUMNS: dict[str, tuple[str, ...]] = {
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

# A stored shape the rewrite knows how to convert: 'YYYY-MM-DD HH:MM:SS' or
# 'YYYY-MM-DDTHH:MM:SS' with optional fractional seconds and no offset.
_NAIVE_SHAPE = (
    "{c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9][ T]"
    "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*'"
    " AND {c} NOT GLOB '*[+-][0-9][0-9]:[0-9][0-9]' AND {c} NOT LIKE '%Z'"
)

# A value that already carries an explicit offset (or Z) after an RFC3339 date-time.
_AWARE_SHAPE = (
    "{c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T"
    "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*'"
    " AND ({c} GLOB '*[+-][0-9][0-9]:[0-9][0-9]' OR {c} LIKE '%Z')"
)

# Aware but space-separated ('YYYY-MM-DD HH:MM:SS[.fff]+HH:MM' or 'Z'): the offset is
# right, only the separator is not RFC3339. 20,070 published_at and 193 fetched_at cells
# on cerebro held this shape on 2026-10-02; the first run of this migration neither
# rewrote nor accepted them and refused to start the daemon.
_AWARE_SPACE_SHAPE = (
    "{c} GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9] "
    "[0-9][0-9]:[0-9][0-9]:[0-9][0-9]*'"
    " AND ({c} GLOB '*[+-][0-9][0-9]:[0-9][0-9]' OR {c} LIKE '%Z')"
)

_TZ_TRIGGER_EXPR = "strftime('%Y-%m-%dT%H:%M:%f','now') || '+00:00'"


def _migrate_1_tz_aware_timestamps(conn: sqlite3.Connection) -> None:
    """Give every stored datetime an explicit +00:00 offset.

    Runs inside the caller's transaction. The backfill UPDATEs fire the updated_at
    triggers, which would overwrite every updated_at with the migration time, so the
    triggers are dropped first and recreated (tz-aware) afterwards. Raises when any
    cell is still not an offset-bearing timestamp, which rolls the transaction back.
    """
    for table in ("sources", "categories", "content"):
        conn.execute(f"DROP TRIGGER IF EXISTS update_{table}_timestamp")

    for table, columns in _TIMESTAMP_COLUMNS.items():
        for col in columns:
            conn.execute(
                f"UPDATE {table} SET {col} = replace(substr({col}, 1, 19), ' ', 'T')"
                f" || substr({col}, 20) || '+00:00'"
                f" WHERE {_NAIVE_SHAPE.format(c=col)}"
            )
            conn.execute(
                f"UPDATE {table} SET {col} = replace(substr({col}, 1, 19), ' ', 'T')"
                f" || substr({col}, 20)"
                f" WHERE {_AWARE_SPACE_SHAPE.format(c=col)}"
            )

    for table in ("sources", "categories", "content"):
        conn.execute(
            f"CREATE TRIGGER update_{table}_timestamp AFTER UPDATE ON {table} BEGIN "
            f"UPDATE {table} SET updated_at = {_TZ_TRIGGER_EXPR} WHERE id = NEW.id; END"
        )

    unconverted = []
    for table, columns in _TIMESTAMP_COLUMNS.items():
        for col in columns:
            (count,) = conn.execute(
                f"SELECT COUNT(*) FROM {table}"
                f" WHERE {col} IS NOT NULL AND NOT ({_AWARE_SHAPE.format(c=col)})"
            ).fetchone()
            if count:
                unconverted.append(f"{table}.{col}: {count} unconverted cells")
    if unconverted:
        raise sqlite3.IntegrityError(
            "tz-aware timestamp migration left cells without an offset ("
            + "; ".join(unconverted)
            + ")"
        )


# Numbered migrations: entry N takes a database from user_version N-1 to N.
_MIGRATIONS: list[Callable[[sqlite3.Connection], None]] = [
    _migrate_1_tz_aware_timestamps,
]


def _backup_beside(conn: sqlite3.Connection, db_path: Path) -> None:
    """Write a VACUUM INTO copy of the database as it is now, next to it.

    A backup left by an earlier attempt that rolled back is kept under a
    timestamped name rather than reused or overwritten, so the new backup always
    holds the state the migration is about to rewrite.
    """
    backup = db_path.with_name(db_path.name + ".bak-tz")
    if backup.exists():
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
        backup.rename(backup.with_name(f"{backup.name}.{stamp}"))
    conn.execute("VACUUM INTO ?", (str(backup),))


def _apply_migrations(conn: sqlite3.Connection, db_path: Path) -> None:
    """Apply every migration the database has not seen, one transaction each."""
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    pending = _MIGRATIONS[version:]
    if not pending:
        return
    _backup_beside(conn, db_path)
    previous_isolation = conn.isolation_level
    conn.isolation_level = None  # explicit BEGIN/COMMIT below
    try:
        for number, migrate in enumerate(pending, start=version + 1):
            conn.execute("BEGIN IMMEDIATE")
            try:
                migrate(conn)
                conn.execute(f"PRAGMA user_version = {number}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
    finally:
        conn.isolation_level = previous_isolation


def init_db(db_path: Optional[Path] = None) -> Path:
    """Initialize the Prismis database with schema.

    Creates the database at $XDG_DATA_HOME/prismis/prismis.db if it doesn't exist,
    and applies the schema from schema.sql.

    Args:
        db_path: Optional custom database path for testing.
                 Defaults to $XDG_DATA_HOME/prismis/prismis.db
                 (or ~/.local/share/prismis/prismis.db)

    Returns:
        Path to the created/verified database

    Raises:
        sqlite3.Error: If database creation fails
    """
    # Determine database path - databases go in XDG_DATA_HOME per XDG spec
    if db_path is None:
        xdg_data_home = os.environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
        )
        data_dir = Path(xdg_data_home) / "prismis"
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "prismis.db"

    # Read schema from package
    schema_file = Path(__file__).parent / "schema.sql"
    if not schema_file.exists():
        raise FileNotFoundError(f"Schema file not found: {schema_file}")

    schema_sql = schema_file.read_text()

    # Connect and apply schema
    conn = sqlite3.connect(db_path)
    try:
        # Load sqlite-vec extension before running schema
        conn.enable_load_extension(True)
        try:
            conn.load_extension("vec0")
        except sqlite3.OperationalError:
            # Fallback: try loading from common paths
            try:
                import sqlite_vec

                conn.load_extension(sqlite_vec.loadable_path())
            except (ImportError, sqlite3.OperationalError) as e:
                raise sqlite3.Error(
                    f"Failed to load sqlite-vec extension: {e}. "
                    "Ensure sqlite-vec is installed: uv add sqlite-vec"
                ) from e
        finally:
            conn.enable_load_extension(False)

        is_new_database = (
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='content'"
            ).fetchone()
            is None
        )

        # Execute the entire schema as a script
        conn.executescript(schema_sql)
        conn.commit()

        if is_new_database:
            # The schema just written already emits the current shape: nothing to
            # convert, nothing to back up.
            conn.execute(f"PRAGMA user_version = {len(_MIGRATIONS)}")
            conn.commit()
        else:
            _apply_migrations(conn, db_path)

        # Verify tables were created
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]

        expected_tables = {"categories", "content", "source_categories", "sources"}
        created_tables = set(tables)

        if not expected_tables.issubset(created_tables):
            missing = expected_tables - created_tables
            raise sqlite3.Error(f"Failed to create tables: {missing}")

        print(f"Database initialized at: {db_path}")
        print(f"Tables created: {', '.join(sorted(created_tables))}")

        return db_path

    except sqlite3.Error as e:
        conn.rollback()
        raise sqlite3.Error(f"Failed to initialize database: {e}") from e
    finally:
        conn.close()


def get_db_connection(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Get a connection to the Prismis database.

    Ensures WAL mode and other pragmas are set correctly.

    Args:
        db_path: Optional custom database path.
                 Defaults to $XDG_DATA_HOME/prismis/prismis.db
                 (or ~/.local/share/prismis/prismis.db)

    Returns:
        SQLite connection with proper settings
    """
    if db_path is None:
        xdg_data_home = os.environ.get(
            "XDG_DATA_HOME", str(Path.home() / ".local" / "share")
        )
        db_path = Path(xdg_data_home) / "prismis" / "prismis.db"

    if not db_path.exists():
        raise FileNotFoundError(
            f"Database not found at {db_path}. Run init_db() first."
        )

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # Enable column access by name

    # Load sqlite-vec extension for vector search
    conn.enable_load_extension(True)
    try:
        conn.load_extension("vec0")
    except sqlite3.OperationalError as e:
        # Fallback: try loading from common paths
        try:
            import sqlite_vec

            conn.load_extension(sqlite_vec.loadable_path())
        except (ImportError, sqlite3.OperationalError) as fallback_error:
            raise sqlite3.Error(
                f"Failed to load sqlite-vec extension: {e}. "
                "Ensure sqlite-vec is installed: uv add sqlite-vec"
            ) from fallback_error
    finally:
        conn.enable_load_extension(False)

    # Ensure WAL mode and pragmas
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


if __name__ == "__main__":
    # Allow running directly to initialize database
    init_db()
