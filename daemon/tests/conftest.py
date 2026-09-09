"""Shared test fixtures for all tests."""

import dataclasses
import os
import re
import socket
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismis_daemon import config, database
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD

# The API key the sealed config is written with. Every test that authenticates against
# the API imports this rather than hardcoding a literal, so there is one source of truth
# and no real key can be committed. Production's own generator (defaults.ensure_config)
# is random, which is why the template is formatted here instead of calling it.
TEST_API_KEY = "prismis-test-key"

# Popped at import, not in the fixture below, and this ordering is the whole point.
# Rich resolves a Console's color system once, in its constructor, and caches it — only
# is_terminal is re-read later. __main__.py and orchestrator.py both build a console at
# module scope, which happens while pytest imports the test modules, before any fixture
# has run. A fixture-time delenv therefore leaves those consoles already committed to
# emitting SGR codes, and a test asserting on a literal substring of their output
# measures the runner's color negotiation instead of what the command said. conftest is
# imported before the modules under test, so this is early enough. The fixture keeps its
# own delenv for the subprocesses tests spawn.
os.environ.pop("FORCE_COLOR", None)


_ANSI_SGR = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove SGR escapes so an assertion is about CONTENT, not styled bytes.

    Rich renders a long option as two separately-styled spans — a dash, a reset, then
    the rest — so a literal `--chain` is absent from styled output entirely. Whether
    styling is on depends on the runner: Rich checks TTY_COMPATIBLE, then FORCE_COLOR,
    then isatty(), so a test asserting on raw CLI text measures which of those the
    environment happened to set.

    The FORCE_COLOR seal below closes one of those paths. This closes all of them, so
    use it for any assertion against rendered CLI output rather than relying on the
    environment being clean. It lives here, not in one test file, because the first fix
    for this was a file-private helper and the same defect reappeared two days later in
    a different file.
    """
    return _ANSI_SGR.sub("", text)


def init_db(path: Path) -> None:
    return database.init_db(path)


def load_config() -> config.Config:
    return config.Config.from_file()


def make_config(**overrides) -> config.Config:
    """Build a valid Config from the sealed config file, with fields overridden.

    Config has 26 required fields (config.py:19-59); hand-listing them in a test is how
    a test rots the next time one is added or renamed. Loading the production template
    and replacing only the field under test keeps the test about that field.
    """
    return dataclasses.replace(load_config(), **overrides)


@pytest.fixture(autouse=True)
def isolated_xdg_env(tmp_path_factory, monkeypatch) -> Path:
    """Seal the suite from the developer's XDG directories.

    Production resolves config/data/state from the environment at call time
    (config.py:182-185, defaults.py:113, database.py:29,107, locking.py:13,
    observability.py:22), including in the subprocess spawned by
    test_daemon_integration.py — which only an env-level seal can reach. Without this
    the suite's pass/fail counts are a property of the developer's $HOME rather than of
    this repo: 113 failures against an empty config, 153 against the developer's own.

    A complete, valid config is materialized from the production template so the suite
    runs against the same shape production ships, not a hand-written stand-in that drifts.

    FORCE_COLOR is sealed for the same reason and is deleted rather than overridden:
    Rich gives it precedence over both NO_COLOR and TERM=dumb, so nothing else turns it
    off. With it set, Rich splits a long option into separately styled spans — a dash,
    a reset, then the rest — and the literal "--chain" is absent from the output. A test
    asserting on rendered CLI text then measures which color mode the runner negotiated,
    passing on a developer's piped stdout and failing under CI's FORCE_COLOR.

    Tests needing a *different* environment set their own value: pytest instantiates
    autouse fixtures before the non-autouse fixtures and test bodies that override them
    (test_db, test_dual_service_config_unit.py:225, test_llm_core_migration_unit.py:252).
    """
    root = tmp_path_factory.mktemp("xdg")
    home = root / "home"
    cfg_home = root / "config"
    data_home = root / "data"
    state_home = root / "state"
    cache_home = root / "cache"
    for d in (home, cfg_home, data_home, state_home, cache_home):
        d.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    cfg_dir = cfg_home / "prismis"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_dir.joinpath("config.toml").write_text(
        DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    )
    cfg_dir.joinpath("context.md").write_text(DEFAULT_CONTEXT_MD)

    return cfg_dir


@pytest.fixture
def hung_peer() -> Iterator[str]:
    """A local TCP listener that accepts connections and then never answers.

    Yields `host:port`.

    A timeout claim is only worth what the clock says, and a clock needs something to
    wait on. Every third-party endpoint that could stall is either unreliable or
    unreachable from CI, so the stall is produced here instead: this accepts the
    connection — so the code under test gets past connect and blocks on the read, which
    is where a real stalled peer leaves it — and then says nothing. Nothing stands in
    for the code under test; it opens a real socket, to this.

    The accept loop blocks rather than polling. A timeout on the listener turns it into
    a spin that holds the GIL, which does not merely slow the test — it inflates the
    elapsed time any caller measures, so a timing assertion ends up measuring this
    fixture instead of the code under test. Shutdown unblocks the blocked accept with a
    connection of its own instead.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(8)
    host, port = listener.getsockname()

    accepted: list[socket.socket] = []
    stop = threading.Event()

    def accept_forever() -> None:
        while not stop.is_set():
            try:
                conn, _addr = listener.accept()
            except OSError:
                return
            accepted.append(conn)

    thread = threading.Thread(target=accept_forever, daemon=True)
    thread.start()
    try:
        yield f"{host}:{port}"
    finally:
        stop.set()
        try:
            socket.create_connection((host, port), timeout=1.0).close()
        except OSError:
            pass
        thread.join(timeout=2.0)
        listener.close()
        for conn in accepted:
            conn.close()


@pytest.fixture
def api_key() -> str:
    """The API key the sealed config was written with."""
    return TEST_API_KEY


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
def llm_config() -> str:
    """The light-summarization service name, for tests that construct LLM clients.

    ContentSummarizer/ContentEvaluator take a service name (__main__.py:67), not a
    settings dict.
    """
    return load_config().llm_light_service


@pytest.fixture
def full_config() -> config.Config:
    """Load full configuration including context for integration tests."""
    return load_config()
