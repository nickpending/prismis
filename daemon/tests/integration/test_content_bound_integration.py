"""An article larger than the model's window is stored and summarized, not refused.

Invariant protected (bounded-llm-content SC-3):
  - an item whose content is over the bound goes through the real orchestrator's
    `analyze_and_store_item` against a provider that refuses any request body over
    400,000 bytes (the way openai/gpt-5.4-nano refused https://ascii.rest/), is stored
    with its full content, a summary and analysis `content_bounded`, and no request
    was refused

Real collaborators: the real summarizer, evaluator, orchestrator, Storage and the
llm-core client. The fake is the provider boundary, a local HTTP server that enforces
the window in bytes.
"""

import http.server
import json
import os
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.llm_call import MAX_CONTENT_BYTES
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import _completion_payload, configure_local_services

# The provider's window, in request-body bytes.
_WINDOW_BYTES = 400_000


class _NullFetcher:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


class _WindowStub:
    """Provider that answers a canned completion, or a 400 for an oversized body."""

    def __init__(self) -> None:
        self.accepted: list[int] = []
        self.refused: list[int] = []

    def serve(self) -> tuple[http.server.ThreadingHTTPServer, str]:
        stub = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                if length > _WINDOW_BYTES:
                    stub.refused.append(length)
                    status = 400
                    body = json.dumps(
                        {"error": {"message": "context_length_exceeded"}}
                    ).encode()
                else:
                    stub.accepted.append(length)
                    status = 200
                    body = json.dumps(
                        _completion_payload(
                            json.dumps(
                                {
                                    "summary": "A stubbed summary.",
                                    "reading_summary": "A stubbed reading summary.",
                                    "alpha_insights": [],
                                    "patterns": [],
                                    "quotes": [],
                                    "tools": [],
                                    "urls": [],
                                    "substantive": True,
                                    "priority": "low",
                                    "matched_interests": [],
                                    "reasoning": "stubbed",
                                }
                            ),
                            "stub-model",
                        )
                    ).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        host, port = server.server_address[0], server.server_address[1]
        assert isinstance(host, str), "loopback bind always yields a str host"
        return server, f"http://{host}:{port}"


@pytest.fixture
def window_stub() -> Iterator[tuple[_WindowStub, str]]:
    stub = _WindowStub()
    server, url = stub.serve()
    try:
        yield stub, url
    finally:
        server.shutdown()
        server.server_close()


def _ascii_rest_like() -> str:
    """Over a million characters, about half of them 3-byte box-drawing characters."""
    return "─" * 550_000 + "word " * 110_000


def _orchestrator(
    test_db: Path, url: str
) -> tuple[DaemonOrchestrator, Storage, str, dict]:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), url)
    config = Config.from_file()
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Feed")
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NullFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )
    source = {"id": source_id, "type": "rss", "name": "Feed", "url": "x"}
    return orchestrator, storage, source_id, source


def _item(source_id: str, content: str) -> ContentItem:
    return ContentItem(
        source_id=source_id,
        external_id=str(uuid.uuid4()),
        title="ascii.rest",
        url=f"https://example.com/{uuid.uuid4().hex[:8]}",
        content=content,
    )


def test_an_article_over_the_window_is_stored_with_content_bounded(
    test_db: Path, isolated_xdg_env: Path, window_stub: tuple[_WindowStub, str]
) -> None:
    """
    INVARIANT: an over-bound article is stored in full, summarized, and marked
    content_bounded; the provider refused nothing
    BREAKS: the summarizer sends the whole article, the provider refuses it with a
            400 on every cycle, and the page is never stored
    """
    stub, url = window_stub
    orchestrator, storage, source_id, source = _orchestrator(test_db, url)
    content = _ascii_rest_like()
    total_bytes = len(content.encode())
    assert total_bytes > _WINDOW_BYTES

    result = orchestrator.analyze_and_store_item(_item(source_id, content), source)

    assert stub.refused == []
    assert len(stub.accepted) >= 2, "summarizer and evaluator each made a request"
    assert result is not None
    row = storage.get_content_by_id(result["content_id"])
    assert row is not None
    assert row["content"] == content
    assert row["summary"] == "A stubbed summary."
    bounded = row["analysis"]["content_bounded"]
    assert bounded["total_bytes"] == total_bytes
    assert 0 < bounded["sent_bytes"] <= MAX_CONTENT_BYTES


def test_an_article_under_the_bound_stores_no_content_bounded(
    test_db: Path, isolated_xdg_env: Path, window_stub: tuple[_WindowStub, str]
) -> None:
    """
    INVARIANT: an item under the bound carries no content_bounded key
    BREAKS: the record is written for every item, so it no longer says which items
            were summarized from a cut
    """
    stub, url = window_stub
    orchestrator, storage, source_id, source = _orchestrator(test_db, url)

    result = orchestrator.analyze_and_store_item(
        _item(source_id, "A short article. " * 100), source
    )

    assert result is not None
    row = storage.get_content_by_id(result["content_id"])
    assert row is not None
    assert "content_bounded" not in row["analysis"]
