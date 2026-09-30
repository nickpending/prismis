"""Integration tests for the known-readable skip/readmit dedup cycle (SC-4).

Drives the real orchestrator, a real RSSFetcher, and real Storage over two fetch
cycles against a local HTTP server standing in for both the feed and the articles
it links to (Principle I). Only the LLM is faked, via the same local-stub pattern
`conftest.local_pipeline_stub` and the deep-extraction test files use.
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
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

_JS_WALL_HTML = (
    b"<!doctype html><html><body>"
    b"You need to enable JavaScript to run this app."
    b"</body></html>"
)

_ARTICLE_HTML = (
    b"<!doctype html><html><body><article>"
    b"<h1>A Real Article</h1>"
    b"<p>This is a genuine article body, long enough for trafilatura's "
    b"extraction heuristics to treat it as the main content rather than "
    b"boilerplate chrome around it, with a second sentence for good measure.</p>"
    b"</article></body></html>"
)

_FEED_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Readmit Test Feed</title><link>{base}/</link><description>local</description>
  <item><title>Readable Item</title><link>{base}/article-readable</link>
    <description>fallback</description><guid isPermaLink="false">item-readable</guid></item>
  <item><title>Flips Item</title><link>{base}/article-flips</link>
    <description>fallback</description><guid isPermaLink="false">item-flips</guid></item>
  <item><title>Always Unreadable Item</title><link>{base}/article-unreadable</link>
    <description>fallback</description><guid isPermaLink="false">item-unreadable</guid></item>
</channel></rss>
"""


class _ReadmitServer:
    """Serves the feed and its three articles, counting hits per path and
    flipping /article-flips from a JS-wall notice to a real article on its
    second hit -- the "becomes readable on retry" case SC-4 describes."""

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                if self.path == "/feed.xml":
                    base = f"http://{self.headers.get('Host', '127.0.0.1')}"
                    body = _FEED_TEMPLATE.format(base=base).encode()
                    self._send(body, "application/rss+xml")
                elif self.path == "/article-readable":
                    self._send(_ARTICLE_HTML, "text/html")
                elif self.path == "/article-flips":
                    if outer.hits[self.path] == 1:
                        self._send(_JS_WALL_HTML, "text/html")
                    else:
                        self._send(_ARTICLE_HTML, "text/html")
                elif self.path == "/article-unreadable":
                    self._send(_JS_WALL_HTML, "text/html")
                else:
                    self.send_error(404)

            def _send(self, body: bytes, content_type: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address[0], self._server.server_address[1]
        assert isinstance(host, str)
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def readmit_server() -> Iterator[_ReadmitServer]:
    server = _ReadmitServer()
    try:
        yield server
    finally:
        server.shutdown()


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


class _LLMStub:
    """Counts completion calls -- summarize_with_analysis + evaluate_content is
    exactly two calls per item analysed, so this count is a direct, non-timing
    proof of whether an item's LLM pass ran at all."""

    def __init__(self) -> None:
        self.call_count = 0
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                if not self.path.endswith("/chat/completions"):
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                self.rfile.read(length)
                outer.call_count += 1
                body = _low_priority_payload()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address[0], self._server.server_address[1]
        assert isinstance(host, str)
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True
        )
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def llm_stub() -> Iterator[_LLMStub]:
    stub = _LLMStub()
    try:
        yield stub
    finally:
        stub.shutdown()


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class _NullFetcher:
    def fetch_content(self, source, **kw):
        return []


def _build_orchestrator(
    storage: Storage, config: Config, rss_fetcher: RSSFetcher
) -> DaemonOrchestrator:
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=rss_fetcher,
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )


def _real_config(base_url: str) -> Config:
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, base_url)
    return Config.from_file()


def test_readmit_cycle_skip_known_readable_avoids_reextraction(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub, readmit_server: _ReadmitServer
) -> None:
    """
    SC-4: on a second fetch cycle, an item already stored readably is not
    re-extracted at all -- the article endpoint it points at is hit exactly
    once (during cycle 1), never again on cycle 2.
    BREAKS: Passing only the orchestrator's own dedup filter the known-readable
    set (and not the fetcher itself) still re-fetches every already-readable
    article on every cycle, since the fetcher extracts before the orchestrator
    ever gets to filter anything out.
    """
    config = _real_config(llm_stub.base_url)
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{readmit_server.base_url}/feed.xml", "rss", "Readmit Feed"
    )
    source_dict = {
        "id": source_id,
        "url": f"{readmit_server.base_url}/feed.xml",
        "type": "rss",
        "name": "Readmit Feed",
        "active": True,
    }
    fetcher = RSSFetcher(max_items=10, config=config)
    orchestrator = _build_orchestrator(storage, config, fetcher)

    orchestrator.fetch_source_content(source_dict)
    assert readmit_server.hits.get("/article-readable") == 1

    orchestrator.fetch_source_content(source_dict)
    assert readmit_server.hits.get("/article-readable") == 1, (
        "SC-4: an already-readable item's article must not be fetched again"
    )


def test_readmit_cycle_force_refetch_reextracts_an_already_readable_item(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub, readmit_server: _ReadmitServer
) -> None:
    """
    SC-4: force_refetch still processes every item -- an item already stored
    readably is re-extracted (its article endpoint is hit again) and
    re-analysed (its LLM pass runs again) rather than the fetcher substituting
    a placeholder because the item happens to be known-readable.
    BREAKS: Computing known_readable_ids from storage and handing it to the
    fetcher unconditionally (keyed only on source_type == "file", never on
    force_refetch) makes every one of RSS/Reddit/YouTube's skip paths fire
    during a forced refetch too, silently overwriting an already-readable
    item's row with title_only: true and placeholder content -- the opposite
    of what forcing a refetch is for.
    """
    config = _real_config(llm_stub.base_url)
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{readmit_server.base_url}/feed.xml", "rss", "Readmit Feed"
    )
    source_dict = {
        "id": source_id,
        "url": f"{readmit_server.base_url}/feed.xml",
        "type": "rss",
        "name": "Readmit Feed",
        "active": True,
    }
    fetcher = RSSFetcher(max_items=10, config=config)
    orchestrator = _build_orchestrator(storage, config, fetcher)

    orchestrator.fetch_source_content(source_dict)
    assert readmit_server.hits.get("/article-readable") == 1
    calls_after_cycle1 = llm_stub.call_count

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("item-readable",)
    ).fetchone()
    stored_before = storage.get_content_by_id(row["id"])
    assert stored_before is not None
    assert stored_before["analysis"]["title_only"] is False

    orchestrator.fetch_source_content(source_dict, force_refetch=True)

    assert readmit_server.hits.get("/article-readable") == 2, (
        "SC-4: force_refetch must re-extract an already-readable item's "
        "article, not skip it"
    )
    assert llm_stub.call_count == calls_after_cycle1 + 6, (
        "force_refetch must re-analyse all 3 items (summarize + evaluate "
        "each), including the one already stored readably"
    )

    stored_after = storage.get_content_by_id(row["id"])
    assert stored_after is not None
    assert stored_after["analysis"]["title_only"] is False, (
        "SC-4: force-refetching an already-readable item must not degrade it "
        "to title_only: true via a fetcher skip path"
    )
    assert "genuine article body" in (stored_after["content"] or "")


def test_readmit_cycle_replaces_content_in_place_once_a_title_only_item_becomes_readable(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub, readmit_server: _ReadmitServer
) -> None:
    """
    SC-4: a stored title-only item is extracted again on the next cycle, and
    when its fresh content is readable the existing row's content is replaced
    and re-analysed in place -- no duplicate row, not counted as new.
    """
    config = _real_config(llm_stub.base_url)
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{readmit_server.base_url}/feed.xml", "rss", "Readmit Feed"
    )
    source_dict = {
        "id": source_id,
        "url": f"{readmit_server.base_url}/feed.xml",
        "type": "rss",
        "name": "Readmit Feed",
        "active": True,
    }
    fetcher = RSSFetcher(max_items=10, config=config)
    orchestrator = _build_orchestrator(storage, config, fetcher)

    cycle1 = orchestrator.fetch_source_content(source_dict)
    assert cycle1["items_new"] == 3, cycle1
    assert llm_stub.call_count == 6, "3 items x (summarize + evaluate)"

    flips_row_before = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("item-flips",)
    ).fetchone()
    stored_before = storage.get_content_by_id(flips_row_before["id"])
    assert stored_before is not None
    assert stored_before["analysis"]["title_only"] is True

    cycle2 = orchestrator.fetch_source_content(source_dict)
    assert cycle2["items_new"] == 0, cycle2
    assert llm_stub.call_count == 8, (
        "only item-flips (now readable) should have been re-analysed: "
        "+1 summarize +1 evaluate, and nothing for the other two items"
    )

    flips_row_after = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("item-flips",)
    ).fetchone()
    assert flips_row_after["id"] == flips_row_before["id"], (
        "SC-4: readmitting a title-only item must update its row, not duplicate it"
    )

    all_flips_rows = storage.conn.execute(
        "SELECT COUNT(*) AS n FROM content WHERE external_id = ?", ("item-flips",)
    ).fetchone()
    assert all_flips_rows["n"] == 1

    stored_after = storage.get_content_by_id(flips_row_after["id"])
    assert stored_after is not None
    assert stored_after["analysis"]["title_only"] is False, stored_after["analysis"]
    assert "genuine article body" in (stored_after["content"] or "")


def test_readmit_cycle_leaves_a_still_unreadable_item_unanalysed_and_unchanged(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub, readmit_server: _ReadmitServer
) -> None:
    """
    SC-4: when a title-only item's fresh content is still not readable, it is
    not re-analysed on the retry cycle.
    """
    config = _real_config(llm_stub.base_url)
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{readmit_server.base_url}/feed.xml", "rss", "Readmit Feed"
    )
    source_dict = {
        "id": source_id,
        "url": f"{readmit_server.base_url}/feed.xml",
        "type": "rss",
        "name": "Readmit Feed",
        "active": True,
    }
    fetcher = RSSFetcher(max_items=10, config=config)
    orchestrator = _build_orchestrator(storage, config, fetcher)

    orchestrator.fetch_source_content(source_dict)
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?",
        ("item-unreadable",),
    ).fetchone()
    stored_before = storage.get_content_by_id(row["id"])
    assert stored_before is not None
    assert stored_before["analysis"]["title_only"] is True
    calls_after_cycle1 = llm_stub.call_count

    orchestrator.fetch_source_content(source_dict)

    row_after = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?",
        ("item-unreadable",),
    ).fetchone()
    assert row_after["id"] == row["id"]

    # The article endpoint IS still hit on the retry (extraction re-runs so a
    # later cycle can detect it becoming readable) -- only the re-analysis is
    # skipped.
    assert readmit_server.hits.get("/article-unreadable") == 2

    # item-flips becomes readable on this same cycle (its own second hit) and
    # is legitimately re-analysed (+2 calls); item-unreadable and item-readable
    # must contribute none, so the total rises by exactly 2 -- proving
    # item-unreadable's LLM pass did not run a second time.
    assert llm_stub.call_count == calls_after_cycle1 + 2, (
        "SC-4: a still-unreadable item must not be re-analysed on the retry cycle"
    )
    stored_after = storage.get_content_by_id(row_after["id"])
    assert stored_after is not None
    assert stored_after["analysis"]["title_only"] is True
