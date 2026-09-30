"""Unit tests for the title_only flag and its deep-extraction gate (SC-3).

Real collaborators throughout, per Principle I: real Storage over a sealed test
database, the real ContentSummarizer/ContentEvaluator/ContentDeepExtractor driven
through llm-core's real `complete()` against a local HTTP stub (the one collaborator
the constitution permits faking), and a hand-written stand-in RSS fetcher passed
through DaemonOrchestrator's own constructor seam -- not a patch of internal state.
"""

from __future__ import annotations

import http.server
import json as _json
import os
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.readability import RSS_NO_CONTENT_FALLBACK
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

_READABLE_CONTENT = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)


class _NullFetcher:
    def fetch_content(self, source, **kw):
        return []


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _low_priority_payload() -> bytes:
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
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    return _json.dumps(payload).encode()


def _high_priority_payload() -> bytes:
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
                            "priority": "high",
                            "matched_interests": ["AI"],
                            "reasoning": "Matches AI interest.",
                            "synthesis": "A stubbed deep synthesis.",
                            "quotables": [],
                        }
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    return _json.dumps(payload).encode()


def _serve(payload_fn) -> http.server.ThreadingHTTPServer:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = payload_fn()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    return server


@pytest.fixture
def low_priority_llm_stub() -> Iterator[str]:
    server = _serve(_low_priority_payload)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


@pytest.fixture
def high_priority_llm_stub() -> Iterator[str]:
    server = _serve(_high_priority_payload)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _real_config(base_url: str) -> Config:
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, base_url)
    return Config.from_file()


def test_title_only_true_for_unreadable_false_for_readable(
    test_db: Path, isolated_xdg_env: Path, low_priority_llm_stub: str
) -> None:
    """
    SC-3: both a not-readable item and a readable item are still summarised,
    prioritised and stored. The not-readable item's analysis carries
    title_only: true; the readable item's carries title_only: false.
    BREAKS: A caller computing title_only itself (instead of build_llm_analysis
    doing it from the content it was given) can drift out of sync with the one
    shared readability check.
    """
    from prismis_daemon.orchestrator import DaemonOrchestrator

    config = _real_config(low_priority_llm_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    unreadable_item = ContentItem(
        source_id=source_id,
        external_id="unreadable-001",
        title="No Content Article",
        url="https://example.com/no-content",
        content=RSS_NO_CONTENT_FALLBACK,
    )
    readable_item = ContentItem(
        source_id=source_id,
        external_id="readable-001",
        title="Readable Article",
        url="https://example.com/readable",
        content=_READABLE_CONTENT,
    )

    class _StubFetcher:
        def fetch_content(self, source, **kw):
            return [unreadable_item, readable_item]

    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_StubFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )

    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    stats = orchestrator.fetch_source_content(source_dict)
    assert stats["items_new"] == 2, stats

    unreadable_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("unreadable-001",)
    ).fetchone()
    readable_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("readable-001",)
    ).fetchone()

    unreadable_stored = storage.get_content_by_id(unreadable_row["id"])
    readable_stored = storage.get_content_by_id(readable_row["id"])

    assert unreadable_stored is not None
    assert readable_stored is not None
    assert unreadable_stored["analysis"]["title_only"] is True
    assert readable_stored["analysis"]["title_only"] is False
    # Both were still summarised/prioritised/stored -- not dropped or left blank.
    assert unreadable_stored["summary"]
    assert readable_stored["summary"]


def test_title_only_item_never_deep_extracts_even_when_priority_passes_gate(
    test_db: Path, isolated_xdg_env: Path, high_priority_llm_stub: str
) -> None:
    """
    SC-3: the orchestrator does not deep-extract a title-only item even when its
    priority passes the deep-extraction gate. A readable HIGH-priority item (same
    stub, same auto_extract="high" config) does deep-extract, proving the gate
    isn't simply disabled outright.
    """
    from prismis_daemon.deep_extractor import ContentDeepExtractor
    from prismis_daemon.orchestrator import DaemonOrchestrator

    config = _real_config(high_priority_llm_stub)
    assert config.llm_deep_service is not None
    assert config.auto_extract == "high"

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    title_only_item = ContentItem(
        source_id=source_id,
        external_id="title-only-deep-001",
        title="Title Only High Priority",
        url="https://example.com/title-only",
        content=RSS_NO_CONTENT_FALLBACK,
    )
    readable_item = ContentItem(
        source_id=source_id,
        external_id="readable-deep-001",
        title="Readable High Priority",
        url="https://example.com/readable-deep",
        content=_READABLE_CONTENT,
    )

    class _StubFetcher:
        def fetch_content(self, source, **kw):
            return [title_only_item, readable_item]

    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_StubFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        deep_extractor=ContentDeepExtractor(config.llm_deep_service),
    )

    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    orchestrator.fetch_source_content(source_dict)

    title_only_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("title-only-deep-001",)
    ).fetchone()
    readable_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("readable-deep-001",)
    ).fetchone()

    title_only_stored = storage.get_content_by_id(title_only_row["id"])
    readable_stored = storage.get_content_by_id(readable_row["id"])

    assert title_only_stored is not None
    assert readable_stored is not None
    assert title_only_stored["analysis"]["title_only"] is True
    assert "deep_extraction" not in title_only_stored["analysis"], (
        "SC-3: a title-only item must never be deep-extracted"
    )
    assert readable_stored["analysis"]["title_only"] is False
    assert "deep_extraction" in readable_stored["analysis"], (
        "the gate must still deep-extract a readable HIGH-priority item"
    )
