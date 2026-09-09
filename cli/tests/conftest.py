"""Shared test fixtures for CLI tests."""

import os
import tempfile
from pathlib import Path
import pytest

# Popped at import, not in the fixture below, and this ordering is the whole point.
# Rich resolves a Console's color system once, in its constructor, and caches it — only
# is_terminal is re-read later. Every CLI module builds its console at module scope
# (extract.py, list.py, search.py and the rest), which happens while pytest imports the
# test modules, before any fixture has run. A fixture-time delenv therefore leaves those
# consoles already committed to emitting SGR codes, and a test asserting on a literal
# substring of their output measures the runner's color negotiation instead of what the
# command said. conftest is imported before the modules under test, so this is early
# enough. The fixture keeps its own delenv for the subprocesses tests spawn.
os.environ.pop("FORCE_COLOR", None)


from prismis_daemon.database import init_db


@pytest.fixture(autouse=True)
def isolated_xdg_env(tmp_path_factory, monkeypatch) -> Path:
    """Seal the CLI suite from the developer's XDG directories.

    api_client.py:44 and remote.py:24 both read XDG_CONFIG_HOME first and only fall back
    to Path.home(), so patching Path.home() alone misses the primary lookup path.

    FORCE_COLOR is sealed here too, and is deleted rather than overridden: Rich gives it
    precedence over both NO_COLOR and TERM=dumb, so nothing else turns it off. With it
    set, Rich splits rendered output into styled spans and a literal substring a test
    asserts on — a flag name, a status line — is absent from the captured text, so the
    test measures which color mode the runner negotiated instead of what the CLI said.
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
    monkeypatch.delenv("FORCE_COLOR", raising=False)

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

