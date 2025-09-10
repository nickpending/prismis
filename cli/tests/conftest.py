"""Shared test fixtures for CLI tests."""

import tempfile
from pathlib import Path
import sys
import pytest
from typer.testing import CliRunner

# Add daemon src to path for storage/database imports
daemon_src = Path(__file__).parent.parent.parent / "daemon" / "src"
sys.path.insert(0, str(daemon_src))

from prismis_daemon.database import init_db  # noqa: E402


@pytest.fixture
def test_db() -> Path:
    """Create a temporary test database for each test."""
    # Create temp directory
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"

    # Initialize database with schema
    init_db(db_path)

    yield db_path

    # Cleanup after test
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def cli_runner() -> CliRunner:
    """Create a Typer CLI test runner."""
    return CliRunner()


@pytest.fixture
def mock_home_dir(test_db: Path, monkeypatch) -> Path:
    """Mock the home directory to use test database."""
    # Create a temporary config directory structure
    temp_home = test_db.parent / "home"
    temp_home.mkdir(exist_ok=True)
    config_dir = temp_home / ".config" / "prismis"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Move test database to expected location
    import shutil

    shutil.move(str(test_db), str(config_dir / "prismis.db"))

    # Patch Path.home() to return our temp home
    monkeypatch.setattr(Path, "home", lambda: temp_home)

    return config_dir / "prismis.db"
