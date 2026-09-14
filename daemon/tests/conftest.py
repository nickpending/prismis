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


_STUB_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Stub Feed</title><link>{base}/</link><description>local</description>
  <item><title>First stub item</title><link>{base}/a</link>
    <description>A short body the summarizer can chew on.</description>
    <guid>stub-a</guid></item>
  <item><title>Second stub item</title><link>{base}/b</link>
    <description>Another short body, distinct from the first.</description>
    <guid>stub-b</guid></item>
</channel></rss>
"""


@pytest.fixture
def local_pipeline_stub() -> Iterator[str]:
    """A local HTTP server standing in for both third parties the chain touches.

    Yields the base URL. Serves `/feed.xml` (link 1) and an OpenAI-shaped
    `/v1/chat/completions` (links 3 and 4).

    The chain's whole point is driving the real orchestrator through real
    collaborators, which left it runnable only where a full config with real
    credentials exists — in practice the deploy host. That made the one thing nobody
    could exercise from a clone the one thing least covered, and the bug the first real
    run found (deep extraction billing while the report said skipped-by-flag) was
    wiring, which a local run would have caught for free.

    llm-core resolves services.toml from XDG_CONFIG_HOME before Path.home()
    (llm_core/services.py), and a service may set key_required = false, so a test can
    point a service at this port and need no credential. The LLM is the one collaborator
    the constitution permits standing in for; nothing else here is faked — real
    fetchers, real Storage, real Embedder, real orchestrator.
    """
    import http.server
    import json as _json

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # keep pytest output clean
            return

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/a", "/b"):
                self._send(
                    b"<html><body><p>Stub article body, long enough to summarize.</p>"
                    b"</body></html>",
                    "text/html",
                )
            elif self.path.startswith("/feed.xml"):
                base = f"http://{self.headers.get('Host', '127.0.0.1')}"
                self._send(_STUB_FEED.format(base=base).encode(), "application/rss+xml")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = {
                "id": "stub-completion",
                "object": "chat.completion",
                "model": "stub-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": _json.dumps(
                                {
                                    "summary": "A stubbed summary.",
                                    "reading_summary": "A stubbed reading summary.",
                                    "alpha_insights": [],
                                    "patterns": [],
                                    "entities": [],
                                    "quotes": [],
                                    "tools": [],
                                    "urls": [],
                                    "priority": "low",
                                    "matched_interests": [],
                                    "reasoning": "stubbed",
                                }
                            ),
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 10,
                    "total_tokens": 20,
                },
            }
            self._send(_json.dumps(payload).encode(), "application/json")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)
