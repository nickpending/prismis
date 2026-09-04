"""Shared test fixtures for CLI tests."""

import tempfile
from pathlib import Path
import pytest


from prismis_daemon.database import init_db


@pytest.fixture(autouse=True)
def isolated_xdg_env(tmp_path_factory, monkeypatch) -> Path:
    """Seal the CLI suite from the developer's XDG directories.

    api_client.py:44 and remote.py:24 both read XDG_CONFIG_HOME first and only fall back
    to Path.home(), so patching Path.home() alone misses the primary lookup path.
    """
    root = tmp_path_factory.mktemp("xdg")
    home = root / "home"
    cfg_home = root / "config"
    data_home = root / "data"
    for d in (home, cfg_home, data_home):
        d.mkdir(parents=True, exist_ok=True)
    (cfg_home / "prismis").mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))

    return cfg_home / "prismis"


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

