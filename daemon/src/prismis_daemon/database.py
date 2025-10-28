"""Database initialization for Prismis daemon."""

import os
import sqlite3
from pathlib import Path
from typing import Optional


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
                )
        finally:
            conn.enable_load_extension(False)

        # Execute the entire schema as a script
        conn.executescript(schema_sql)
        conn.commit()

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
        raise sqlite3.Error(f"Failed to initialize database: {e}")
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
        except (ImportError, sqlite3.OperationalError):
            raise sqlite3.Error(
                f"Failed to load sqlite-vec extension: {e}. "
                "Ensure sqlite-vec is installed: uv add sqlite-vec"
            )
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
