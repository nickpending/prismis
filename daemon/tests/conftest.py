"""Shared test fixtures for all tests."""

import copy
import dataclasses
import difflib
import json
import os
import re
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from prismis_daemon import config, database, llm_client
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD
from prismis_daemon.models import ContentItem
from prismis_daemon.observability import reset_logger, set_run_id
from prismis_daemon.storage import Storage

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


@pytest.fixture(autouse=True)
def _fresh_observability_logger() -> Iterator[None]:
    """Give every test its own observability logger and no run id.

    The logger is a process-wide singleton that fixes its directory from
    XDG_DATA_HOME on first use; without this, a test that set XDG_DATA_HOME to a
    temp dir left later tests writing into that deleted dir (gh #86).
    """
    reset_logger()
    set_run_id(None)
    yield
    reset_logger()
    set_run_id(None)


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


def init_db(path: Path) -> Path:
    return database.init_db(path)


def add_new_content(storage: Storage, item: ContentItem | dict[str, Any]) -> str:
    """Add content and assert it was newly inserted, narrowing the id to `str`.

    `Storage.add_content` returns `str | None` — None on a duplicate external_id.
    Every test that inserts content it then reads or mutates by id already assumes
    the insert was new; this makes that assumption explicit instead of leaving
    call sites to pass a possibly-None id downstream.
    """
    content_id = storage.add_content(item)
    assert content_id is not None, "expected a newly inserted content id, got a duplicate"
    return content_id


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
        DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY).replace(
            "[daemon]\n", '[daemon]\nfetch_allow_private_hosts = ["127.0.0.1"]\n', 1
        )
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
def test_db(monkeypatch) -> Iterator[Path]:
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


# The items carry real subject matter (a Python tooling release, in the production
# default context's medium-priority topics) because a recorded model reply is only as
# informative as what it was asked about: given placeholder text the production model
# correctly declines to prioritize, and a pipeline test then has nothing to assert on.
_STUB_ARTICLE_A = (
    "uv 0.9 adds a resolver cache that makes repeated dependency resolution up to ten "
    "times faster. The Python package manager now reuses resolution results between "
    "runs, so adding one dependency to a large project no longer re-resolves the whole "
    "tree. The release also ships a rewritten lockfile format and ruff-style "
    "diagnostics for conflicting version constraints."
)
_STUB_ARTICLE_B = (
    "ruff 0.14 promotes a set of preview lint rules to stable and speeds up its "
    "formatter on large monorepos. The Python linter now reports unused suppression "
    "comments by default, and its configuration file gains per-directory overrides "
    "that mirror how uv handles workspaces."
)
_STUB_FEED = (
    """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Stub Feed</title><link>{base}/</link><description>local</description>
  <item><title>uv 0.9 adds a resolver cache</title><link>{base}/a</link>
    <description>"""
    + _STUB_ARTICLE_A
    + """</description>
    <guid>stub-a</guid></item>
  <item><title>ruff 0.14 stabilizes preview rules</title><link>{base}/b</link>
    <description>"""
    + _STUB_ARTICLE_B
    + """</description>
    <guid>stub-b</guid></item>
</channel></rss>
"""
)


# Deep-extraction stub controls -----------------------------------------------------
# The canned text `local_pipeline_stub` returns for the deep schema, and the marker a
# caller embeds in the `content` it hands to ContentDeepExtractor.extract() to make the
# stub's /v1/chat/completions response sleep before answering. Both travel through the
# real request path (content -> user prompt -> HTTP body), so a test that wants a slow
# extraction controls it by choosing what real content it feeds the real extractor,
# rather than a stand-in extractor sleeping in Python instead of on the wire.
DEEP_EXTRACT_STUB_SYNTHESIS = "A stubbed deep synthesis for the local pipeline stub."
DEEP_EXTRACT_STUB_MODEL = "stub-model"
DEEP_EXTRACT_DELAY_PREFIX = "STUB_DELAY_SECONDS="
_REQUEST_DELAY_PATTERN = re.compile(
    re.escape(DEEP_EXTRACT_DELAY_PREFIX) + r"(\d+(?:\.\d+)?)"
)


# Recorded LLM replies -----------------------------------------------------------------
# A test marked `recorded_llm` has `local_pipeline_stub` answer from a recording of what
# the production model really said instead of the canned payload. One file per test,
# `<RECORDINGS_DIR>/<test module>/<test name>.json`, holding an ordered list of
# `{request, reply, model}`; the Nth call the test makes is answered from entry N.
#
# Replay is the default, in every gate and CI run, and it is strict: a missing file, a
# call beyond the recorded ones, or a live request whose text differs from the recorded
# one fails the test with a message naming the file and the command that re-records. It
# never falls back to the canned reply and never reaches a provider. Only dates and the
# model name are ignored when comparing requests (`_normalize_request`).
#
# Record is operator-run: PRISMIS_RECORD_LLM names a service in the operator's real
# llm-core services.toml and the stub forwards each request there, with the model
# replaced by PRISMIS_RECORD_MODEL (or the service's default_model), and writes what
# came back. The real services.toml and apiconf key are resolved at import, because the
# autouse `isolated_xdg_env` fixture re-points HOME and XDG_CONFIG_HOME for every test.
RECORDINGS_DIR = Path(__file__).parent / "fixtures" / "llm_recordings"
RECORD_LLM_ENV = "PRISMIS_RECORD_LLM"
RECORD_MODEL_ENV = "PRISMIS_RECORD_MODEL"
RECORDED_LLM_MARKER = "recorded_llm"

_MODEL_TOKEN = "<model>"
_DATE_TOKEN = "<date>"
_ISO_DATE = re.compile(r"(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)")
_LONG_DATE = re.compile(
    r"\b(?:January|February|March|April|May|June|July|August|September|October"
    r"|November|December) \d{1,2}, \d{4}\b"
)


@dataclasses.dataclass(frozen=True)
class RecordTarget:
    """The real service a record-mode stub forwards to."""

    base_url: str
    api_key: str | None
    model: str
    headers: dict[str, str]


def _real_llm_core_dir() -> Path:
    """The operator's real llm-core directory, by llm_client's own resolution order."""
    override = os.environ.get("LLM_CORE_CONFIG_DIR")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "llm-core"
    return Path.home() / ".config" / "llm-core"


def resolve_record_target(
    service: str, model: str | None, llm_core_dir: Path
) -> RecordTarget:
    """Resolve `service` in `llm_core_dir`'s services.toml into a forwarding target.

    Runs `llm_client.resolve_service` and `llm_client.load_api_key`, so recording uses
    exactly the service definition and key lookup production uses. `llm_core_dir` is
    passed through LLM_CORE_CONFIG_DIR for the duration of the call.
    """
    saved = os.environ.get("LLM_CORE_CONFIG_DIR")
    os.environ["LLM_CORE_CONFIG_DIR"] = str(llm_core_dir)
    try:
        svc = llm_client.resolve_service(service)
        key = llm_client.load_api_key(svc)
    finally:
        if saved is None:
            os.environ.pop("LLM_CORE_CONFIG_DIR", None)
        else:
            os.environ["LLM_CORE_CONFIG_DIR"] = saved
    chosen = model or svc.default_model
    if not chosen:
        raise llm_client.ConfigError(
            f'Service "{service}" has no default_model; set {RECORD_MODEL_ENV}.'
        )
    headers: dict[str, str] = {}
    if svc.app_title is not None:
        headers["X-OpenRouter-Title"] = svc.app_title
    if svc.app_url is not None:
        headers["HTTP-Referer"] = svc.app_url
    return RecordTarget(
        base_url=svc.base_url.rstrip("/"), api_key=key, model=chosen, headers=headers
    )


def _resolve_import_time_record_target() -> RecordTarget | Exception | None:
    service = os.environ.get(RECORD_LLM_ENV)
    if not service:
        return None
    try:
        return resolve_record_target(
            service, os.environ.get(RECORD_MODEL_ENV), _real_llm_core_dir()
        )
    except Exception as e:  # reported when a recorded test first needs the target
        return e


_IMPORT_TIME_RECORD_TARGET = _resolve_import_time_record_target()


def _normalize_text(text: str) -> str:
    return _LONG_DATE.sub(_DATE_TOKEN, _ISO_DATE.sub(_DATE_TOKEN, text))


_Json = str | int | float | bool | None | list["_Json"] | dict[str, "_Json"]


def _normalize_node(node: _Json) -> _Json:
    if isinstance(node, str):
        return _normalize_text(node)
    if isinstance(node, list):
        return [_normalize_node(n) for n in node]
    if isinstance(node, dict):
        return {k: _normalize_node(v) for k, v in node.items()}
    return node


def _normalize_request(body: dict[str, Any]) -> dict[str, Any]:
    """The request with its model name and dates replaced by fixed tokens.

    These are the only volatile parts a replay ignores: the stub is addressed under a
    stub model name while the recording carries the real one, and a prompt built from
    "today" carries a different date on every run. Everything else must match.
    """
    out = _normalize_node(copy.deepcopy(body))
    assert isinstance(out, dict)
    if "model" in out:
        out["model"] = _MODEL_TOKEN
    return out


def _completion_payload(content: str, model: str) -> dict[str, Any]:
    return {
        "id": "stub-completion",
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }


class LlmRecorder:
    """Per-test record/replay state behind `local_pipeline_stub`'s completions route.

    `handle` returns the HTTP status and body to answer a request with. A refusal is a
    400, never a 5xx: the openai SDK retries 5xx, and each retry would consume another
    recorded entry. Every refusal is also kept in `failures`, which the `llm_recorder`
    fixture turns into a test failure at teardown, so a caller that swallows the error
    cannot hide it.
    """

    def __init__(
        self,
        path: Path,
        nodeid: str,
        target: RecordTarget | Exception | None,
    ) -> None:
        self.path = path
        self.target = target
        self.failures: list[str] = []
        self.refused = False
        self.calls = 0
        self._lock = threading.Lock()
        self._recorded: list[dict[str, Any]] = []
        self.command = (
            f"cd daemon && {RECORD_LLM_ENV}=<service> "
            f"{RECORD_MODEL_ENV}=openai/gpt-5.4-nano uv run pytest {nodeid}"
        )

    def _load(self) -> list[dict[str, Any]] | None:
        """The recorded entries as the file holds them now; None when it is absent."""
        return json.loads(self.path.read_text()) if self.path.exists() else None

    @property
    def recording(self) -> bool:
        return self.target is not None

    def _refuse(self, message: str) -> tuple[int, dict[str, Any]]:
        full = f"{message} Recording file: {self.path}. To re-record: {self.command}"
        self.failures.append(full)
        self.refused = True
        return 400, {"error": {"message": full, "type": "recorded_llm"}}

    def handle(self, body: dict[str, Any]) -> tuple[int, bytes]:
        with self._lock:
            index = self.calls
            status, payload = (
                self._record(index, body) if self.recording else self._replay(index, body)
            )
            if status == 200:
                self.calls += 1
            return status, payload

    def _replay(self, index: int, body: dict[str, Any]) -> tuple[int, bytes]:
        entries = self._load()
        if entries is None:
            return self._refuse_bytes("No recording exists for this test.")
        if index >= len(entries):
            return self._refuse_bytes(
                f"Call {index + 1} has no recorded reply; the recording holds "
                f"{len(entries)}."
            )
        entry = entries[index]
        want = json.dumps(_normalize_request(entry["request"]), indent=1, sort_keys=True)
        got = json.dumps(_normalize_request(body), indent=1, sort_keys=True)
        if want != got:
            diff = "\n".join(
                list(
                    difflib.unified_diff(
                        want.splitlines(),
                        got.splitlines(),
                        "recorded",
                        "live",
                        lineterm="",
                        n=1,
                    )
                )[:24]
            )
            return self._refuse_bytes(
                f"Call {index + 1} differs from the recorded request:\n{diff}\n"
            )
        return 200, json.dumps(
            _completion_payload(entry["reply"], entry["model"])
        ).encode()

    def _refuse_bytes(self, message: str) -> tuple[int, bytes]:
        status, payload = self._refuse(message)
        return status, json.dumps(payload).encode()

    def _record(self, index: int, body: dict[str, Any]) -> tuple[int, bytes]:
        target = self.target
        if isinstance(target, Exception) or target is None:
            return self._refuse_bytes(f"Record mode could not resolve its service: {target}")
        sent = {**body, "model": target.model}
        headers = {"Content-Type": "application/json", **target.headers}
        if target.api_key:
            headers["Authorization"] = f"Bearer {target.api_key}"
        req = urllib.request.Request(
            f"{target.base_url}/chat/completions",
            data=json.dumps(sent).encode(),
            headers=headers,
            method="POST",
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(req, timeout=180) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as e:
            return self._refuse_bytes(
                f"Provider answered {e.code}: {e.read().decode('utf-8', 'replace')[:500]}"
            )
        except urllib.error.URLError as e:
            return self._refuse_bytes(f"Provider unreachable: {e.reason}")
        reply = json.loads(raw)
        content = reply["choices"][0]["message"].get("content") or ""
        self._recorded.append(
            {"request": sent, "reply": content, "model": reply.get("model", target.model)}
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._recorded, indent=2, ensure_ascii=False) + "\n"
        )
        return 200, raw

    def finalize(self) -> None:
        """Fail the test if replay was refused, or the recording was not fully used.

        A recording that is missing, or holds more replies than the test asked for, is
        out of step with the code; so is a refusal the code under test swallowed. A test
        that provoked a refusal on purpose reads `failures`, asserts on it and clears it.
        """
        if not self.refused and not self.recording:
            entries = self._load()
            if entries is None:
                self._refuse("No recording exists for this test.")
            elif self.calls < len(entries):
                self._refuse(
                    f"The test made {self.calls} call(s); the recording holds "
                    f"{len(entries)}."
                )
        if self.failures:
            pytest.fail(
                "recorded LLM replay failed:\n" + "\n".join(self.failures), pytrace=False
            )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{RECORDED_LLM_MARKER}: the test's LLM calls replay recorded real replies "
        f"through local_pipeline_stub (re-record with {RECORD_LLM_ENV})",
    )


@pytest.fixture
def llm_recordings_dir() -> Path:
    """Where recordings live. A test module may override this to use its own dir."""
    return RECORDINGS_DIR


@pytest.fixture
def llm_record_target() -> RecordTarget | Exception | None:
    """The record-mode forwarding target; None means replay. Overridable by tests."""
    return _IMPORT_TIME_RECORD_TARGET


@pytest.fixture
def llm_recorder(
    request: pytest.FixtureRequest,
    llm_recordings_dir: Path,
    llm_record_target: RecordTarget | Exception | None,
) -> Iterator[LlmRecorder | None]:
    """The recorder for a test marked `recorded_llm`; None for every other test."""
    if request.node.get_closest_marker(RECORDED_LLM_MARKER) is None:
        yield None
        return
    path = llm_recordings_dir / request.module.__name__ / f"{request.node.name}.json"
    nodeid = request.node.nodeid
    recorder = LlmRecorder(path, nodeid, llm_record_target)
    yield recorder
    recorder.finalize()


@pytest.fixture
def local_pipeline_stub(llm_recorder: LlmRecorder | None) -> Iterator[str]:
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

    The completion payload carries both the light-summarizer schema and the deep
    extraction schema (`synthesis`/`quotables`) in one response, so the same stub
    serves ContentSummarizer, ContentEvaluator and ContentDeepExtractor without
    branching on which of them is asking. A caller that needs the response delayed —
    to drive a real ContentDeepExtractor through a slow-extraction scenario without a
    stand-in extractor's own `time.sleep()` — embeds
    `f"{DEEP_EXTRACT_DELAY_PREFIX}<seconds>"` in the content or title it passes in;
    the marker rides along in the request body this handler reads.

    A test marked `recorded_llm` gets `llm_recorder` instead of the canned payload: the
    completions route replays (or, with PRISMIS_RECORD_LLM set, records) the production
    model's real replies; see "Recorded LLM replies" above.
    """
    import http.server
    import json as _json

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:  # keep pytest output clean
            return

        def _send(self, body: bytes, content_type: str, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path in ("/a", "/b"):
                article = _STUB_ARTICLE_A if self.path == "/a" else _STUB_ARTICLE_B
                self._send(
                    f"<html><body><p>{article}</p></body></html>".encode(), "text/html"
                )
            elif self.path.startswith("/feed.xml"):
                # `?links=external` makes the items link to example.com instead of this
                # server, for a test whose recorded prompt must not carry the stub's
                # ephemeral port; the article fetch then fails and the fetcher uses the
                # feed entry's own text.
                if "links=external" in self.path:
                    base = "https://example.com"
                else:
                    base = f"http://{self.headers.get('Host', '127.0.0.1')}"
                self._send(_STUB_FEED.format(base=base).encode(), "application/rss+xml")
            else:
                self.send_error(404)

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length)
            delay = _REQUEST_DELAY_PATTERN.search(
                raw_body.decode("utf-8", errors="ignore")
            )
            if delay:
                time.sleep(float(delay.group(1)))
            if llm_recorder is not None:
                status, answer = llm_recorder.handle(_json.loads(raw_body))
                self._send(answer, "application/json", status)
                return
            payload = _completion_payload(
                _json.dumps(
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
                        "synthesis": DEEP_EXTRACT_STUB_SYNTHESIS,
                        "quotables": [],
                    }
                ),
                DEEP_EXTRACT_STUB_MODEL,
            )
            self._send(_json.dumps(payload).encode(), "application/json")

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str), "loopback bind always yields a str host"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Route every outbound HTTP call at a proxy port nothing listens on.

    Containment, not an assertion: a guard that fails to fire cannot reach a real
    service from a gate-run test, and fails with a proxy error instead. Loopback is the
    one exemption (the local stub).
    """
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    # Loopback is exempt so a test can still reach `local_pipeline_stub`; every other
    # host, including any provider, goes to the dead proxy and fails.
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


LOCAL_LIGHT_SERVICE = "prismis-verify-stub"
LOCAL_DEEP_SERVICE = "prismis-verify-stub-deep"


def configure_local_services(cfg_home: Path, base_url: str) -> None:
    """Point the sealed config's light and deep services at `local_pipeline_stub`.

    Two service names on one stub, because circuit breakers are keyed by service name:
    sharing one would make a deep-circuit test open the light circuit too.

    Deliberately mirrors the deploy host: a deep service IS configured and auto_extract
    is "high". Disabling the deep service instead would make build_orchestrator return
    deep_extractor=None whatever the --full flag says, so a regression in that gate
    could not manifest. Verified: with deep_service off, reverting the gate leaves the
    local end-to-end test green.
    """
    llm_core = cfg_home / "llm-core"
    llm_core.mkdir(parents=True, exist_ok=True)
    services = f'default_service = "{LOCAL_LIGHT_SERVICE}"\n'
    for name in (LOCAL_LIGHT_SERVICE, LOCAL_DEEP_SERVICE):
        # key_required=false means no secret.
        services += (
            f"[services.{name}]\n"
            'adapter = "openai"\n'
            f'base_url = "{base_url}/v1"\n'
            "key_required = false\n"
            'default_model = "stub-model"\n'
        )
    (llm_core / "services.toml").write_text(services)

    path = cfg_home / "prismis" / "config.toml"
    out = []
    for line in path.read_text().splitlines():
        if line.startswith("light_service"):
            out.append(f'light_service = "{LOCAL_LIGHT_SERVICE}"')
        elif line.startswith(("deep_service", "# deep_service")):
            out.append(f'deep_service = "{LOCAL_DEEP_SERVICE}"')
        elif line.startswith("auto_extract"):
            out.append('auto_extract = "high"')
        else:
            out.append(line)
    path.write_text("\n".join(out) + "\n")
