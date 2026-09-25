"""Shared test fixtures for CLI tests."""

import asyncio
import dataclasses
import http.server
import json as _json
import os
import socket
import tempfile
import threading
import time
import uuid
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
import uvicorn

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


from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.database import init_db
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# The API key both the sealed daemon config and the sealed [remote] config agree on.
# One source of truth so a live_daemon test never drifts between the two files it
# writes into the same sealed config.toml.
TEST_API_KEY = "prismis-cli-test-key"

# Embedded in seeded content for the one test that needs a real deep-extraction
# failure (test_per_item_failure_does_not_abort_batch). The stub LLM server below
# looks for this marker in the prompt it receives and answers with a body that
# fails ContentDeepExtractor's own JSON parse — a real failure produced by real
# content, not an injected exception.
DEEP_EXTRACT_FAILURE_MARKER = "TRIGGER-DEEP-EXTRACT-FAILURE"


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
def test_db() -> Iterator[Path]:
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


@dataclasses.dataclass
class LiveDaemon:
    """A real prismis_daemon API server, reachable over a real loopback socket.

    `base_url` and `api_key` are also written into the sealed config.toml's
    [remote] section, so `cli.extract.APIClient()` — which builds its own client
    from config rather than accepting an injected one — reaches this daemon too.
    `db_path` is the exact file the daemon's own `Storage()` resolves via
    XDG_DATA_HOME, so a test seeds content by opening a second `Storage(db_path)`
    against the same file.
    """

    base_url: str
    api_key: str
    db_path: Path


def seed_content(
    storage: Storage,
    source_id: str,
    *,
    title: str,
    priority: str = "high",
    content: str = "Article body long enough to summarize and extract from.",
    analysis: dict[str, Any] | None = None,
    published_at: datetime | None = None,
) -> str:
    """Insert one content row and narrow the id to `str`.

    `Storage.add_content` returns `str | None` — None on a duplicate external_id.
    A fresh uuid4 external_id per call means that never happens here; the assert
    documents the assumption instead of leaving it implicit.

    `published_at` controls the order `GET /api/entries` returns same-priority
    items in (`ORDER BY c.published_at DESC` — storage.py's
    `get_content_by_priority`), for a test that needs a deterministic order.
    """
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=str(uuid.uuid4()),
            title=title,
            url=f"http://example.com/{uuid.uuid4().hex[:8]}",
            content=content,
            priority=priority,
            analysis=analysis,
            published_at=published_at,
        )
    )
    assert content_id is not None, "expected a newly inserted content id, got a duplicate"
    return content_id


def _write_remote_config(cfg_dir: Path, base_url: str, api_key: str) -> None:
    """Point the sealed config.toml's [remote] section at a real daemon.

    `cli.extract.APIClient()` constructs itself from config with no injection
    point (unlike `_make_client()` in the api_client unit tests), so this is the
    only way to redirect it onto a fixture's ephemeral port. `is_remote_mode()`
    (remote.py:64) is keyed off [remote].url alone; `[api]` is left untouched.
    """
    config_path = cfg_dir / "config.toml"
    lines = config_path.read_text().splitlines()
    out = []
    in_remote = False
    for line in lines:
        if line.strip() == "[remote]":
            in_remote = True
            out.append(line)
            # Real url/key lines go right under the header they belong to —
            # appending them after the whole-file loop instead would land them
            # under whatever section happens to be last, and tomllib would
            # parse them there instead of into [remote].
            out.append(f'url = "{base_url}"')
            out.append(f'key = "{api_key}"')
            continue
        if in_remote and line.startswith("["):
            in_remote = False
        if in_remote and line.startswith(("# url", "# key")):
            continue
        out.append(line)
    config_path.write_text("\n".join(out) + "\n")


def _wait_until_started(server: uvicorn.Server, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while not server.started and time.time() < deadline:
        time.sleep(0.01)
    assert server.started, "uvicorn test server did not start in time"


def _run_live_daemon(
    cfg_dir: Path, deep_service_name: str | None
) -> Iterator[LiveDaemon]:
    """Run the real `prismis_daemon.api.app` on a real loopback socket.

    Same shape as `local_pipeline_stub` (daemon/tests/conftest.py): a real
    server, in a background thread, torn down at the end of the test. Unlike
    that fixture this serves the daemon's own FastAPI app — real Storage, real
    Config, real `verify_api_key` — so the CLI's real APIClient talks to a real
    daemon instead of a mock standing in for one.
    """
    cfg_dir.joinpath("config.toml").write_text(
        DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    )
    cfg_dir.joinpath("context.md").write_text(DEFAULT_CONTEXT_MD)

    data_home = Path(os.environ["XDG_DATA_HOME"])
    db_path = data_home / "prismis" / "prismis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)

    if deep_service_name:
        out = []
        for line in cfg_dir.joinpath("config.toml").read_text().splitlines():
            if line.startswith(("deep_service", "# deep_service")):
                out.append(f'deep_service = "{deep_service_name}"')
            else:
                out.append(line)
        cfg_dir.joinpath("config.toml").write_text("\n".join(out) + "\n")

    from prismis_daemon.api import app

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    host, port = sock.getsockname()
    base_url = f"http://{host}:{port}"

    if deep_service_name:
        reset_circuit_breaker(deep_service_name)
        app.state.deep_extractor = ContentDeepExtractor(deep_service_name)
    else:
        app.state.deep_extractor = None

    _write_remote_config(cfg_dir, base_url, TEST_API_KEY)

    config = uvicorn.Config(app, log_level="warning")
    server = uvicorn.Server(config)

    def _run() -> None:
        asyncio.run(server.serve(sockets=[sock]))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    _wait_until_started(server)

    try:
        yield LiveDaemon(base_url=base_url, api_key=TEST_API_KEY, db_path=db_path)
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)
        app.state.deep_extractor = None
        if deep_service_name:
            reset_circuit_breaker(deep_service_name)


@pytest.fixture
def live_daemon(isolated_xdg_env: Path) -> Iterator[LiveDaemon]:
    """A real daemon with no deep-extraction service configured.

    `POST /api/entries/{id}/extract` answers 503 "not configured" for this
    daemon, which is the real shape of the server-side error the extract_entry
    HTTP-error test needs. Use `live_daemon_deep` for a daemon that can extract.
    """
    yield from _run_live_daemon(isolated_xdg_env, deep_service_name=None)


@pytest.fixture
def live_daemon_deep(isolated_xdg_env: Path) -> Iterator[LiveDaemon]:
    """A real daemon wired to a real (local, stubbed) deep-extraction LLM.

    The LLM is the one collaborator the constitution permits standing in for
    (Principle I); everything else here is real: real Storage, real Config,
    real ContentDeepExtractor, real CircuitBreaker keyed to a fresh per-test
    service name so no test can leak circuit state into another.

    A seeded item's content containing `DEEP_EXTRACT_FAILURE_MARKER` makes the
    stub answer with a body that fails `ContentDeepExtractor`'s own JSON parse,
    producing a genuine extraction failure without injecting an exception
    anywhere — the same real-content-drives-real-branch technique
    `local_pipeline_stub` uses for its own stub.
    """
    service_name = f"prismis-cli-test-deep-{uuid.uuid4().hex[:8]}"

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # keep pytest output clean
            return

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            if DEEP_EXTRACT_FAILURE_MARKER.encode() in raw:
                message_content = "not valid json"
            else:
                message_content = _json.dumps(
                    {"synthesis": "Stubbed deep synthesis.", "quotables": []}
                )
            payload = {
                "id": "stub-deep-completion",
                "object": "chat.completion",
                "model": "stub-deep-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": message_content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 5,
                    "total_tokens": 10,
                },
            }
            body = _json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    llm_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    llm_host, llm_port = llm_server.server_address[0], llm_server.server_address[1]
    assert isinstance(llm_host, str), "loopback bind always yields a str host"
    llm_thread = threading.Thread(target=llm_server.serve_forever, daemon=True)
    llm_thread.start()

    llm_core_dir = isolated_xdg_env.parent / "llm-core"
    llm_core_dir.mkdir(parents=True, exist_ok=True)
    llm_core_dir.joinpath("services.toml").write_text(
        f'default_service = "{service_name}"\n'
        f'[services.{service_name}]\n'
        'adapter = "openai"\n'
        f'base_url = "http://{llm_host}:{llm_port}/v1"\n'
        "key_required = false\n"
        'default_model = "stub-deep-model"\n'
    )

    try:
        yield from _run_live_daemon(isolated_xdg_env, deep_service_name=service_name)
    finally:
        llm_server.shutdown()
        llm_server.server_close()
        llm_thread.join(timeout=2.0)
