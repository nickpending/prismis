"""Shared test fixtures for all tests."""

import tempfile
from pathlib import Path
import pytest

import sys

# Add src to path for absolute imports
src_path = str(Path(__file__).parent.parent / "src")
sys.path.insert(0, src_path)

# Import from the package properly
from prismis_daemon import database
from prismis_daemon import config


def init_db(path: Path) -> None:
    return database.init_db(path)


def load_config() -> dict:
    return config.load_config()


@pytest.fixture
def test_db(monkeypatch) -> Path:
    """Create a temporary test database for each test."""
    # Create temp directory
    temp_dir = tempfile.mkdtemp()

    # Set XDG_DATA_HOME so Storage() uses our test directory
    data_dir = Path(temp_dir) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(data_dir))

    # Now Storage() will use data_dir/prismis/prismis.db
    db_path = data_dir / "prismis" / "prismis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)

    # Initialize database with schema
    init_db(db_path)

    yield db_path

    # Cleanup after test
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def llm_config() -> dict:
    """Load LLM configuration from config file for integration tests."""
    try:
        config = load_config()
        return config.get("llm", {})
    except Exception:
        # If config doesn't exist, skip tests that need it
        pytest.skip("Config file not found at ~/.config/prismis/config.toml")


@pytest.fixture
def full_config() -> dict:
    """Load full configuration including context for integration tests."""
    try:
        config = load_config()
        return config
    except Exception:
        # If config doesn't exist, skip tests that need it
        pytest.skip("Config file not found at ~/.config/prismis/config.toml")
