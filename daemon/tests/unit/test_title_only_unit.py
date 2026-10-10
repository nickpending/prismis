"""Unit tests for the title_only flag and its deep-extraction gate (SC-3).

Real collaborators throughout, per Principle I: real Storage over a sealed test
database, the real ContentSummarizer/ContentEvaluator/ContentDeepExtractor driven
through llm-core's real `complete()` against a local HTTP stub (the one collaborator
the constitution permits faking), and a hand-written stand-in RSS fetcher passed
through DaemonOrchestrator's own constructor seam -- not a patch of internal state.
"""

from __future__ import annotations

import http.server
import itertools
import json as _json
import os
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from prismis_daemon.analysis import build_llm_analysis, title_only_reason
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluation, ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.readability import (
    DISCUSSION_HEADER,
    RSS_NO_CONTENT_FALLBACK,
    format_discussion,
    format_reddit_link_only,
)
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer, ContentSummary

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


# ---------------------------------------------------------------------------
# title-only-reasons (SC-3, SC-5, SC-6): why an item is title-only.
#
# `analysis.title_only_reason` is the one producer of the reason; these drive it
# directly over its input space, through `build_llm_analysis`, and through the real
# orchestrator pipeline (real Storage, real summarizer and evaluator, an LLM stub at
# the HTTP boundary).
# ---------------------------------------------------------------------------

_READABLE = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)
_NAVIGATION = "Home\nAbout\nContact us\nPrivacy\nTerms"
_FAILED_429 = {"outcome": "fetch_failed", "detail": "HTTP 429"}
_EMPTY_OUTCOME = {"outcome": "empty", "detail": ""}
_EXTRACTED = {"outcome": "extracted", "detail": ""}


@pytest.mark.parametrize(
    ("content", "fetch_outcome", "substantive", "expected"),
    [
        pytest.param(_READABLE, None, True, None, id="readable-substantive"),
        pytest.param(_READABLE, None, None, None, id="readable-verdict-unknown"),
        pytest.param(_READABLE, _EXTRACTED, True, None, id="readable-extracted"),
        pytest.param(
            _READABLE, None, False, "model:not_substantive", id="readable-model-says-no"
        ),
        pytest.param(
            _READABLE,
            _FAILED_429,
            False,
            "model:not_substantive",
            id="readable-fallback-after-failed-fetch-model-says-no",
        ),
        pytest.param(_NAVIGATION, None, None, "content:no_prose", id="code-rule"),
        pytest.param(_NAVIGATION, None, True, "content:no_prose", id="code-beats-model"),
        pytest.param(
            _NAVIGATION, _EMPTY_OUTCOME, None, "content:no_prose", id="empty-outcome"
        ),
        pytest.param(
            _NAVIGATION,
            _FAILED_429,
            None,
            "fetch_failed:HTTP 429; content:no_prose",
            id="failed-fetch-prefixes",
        ),
        pytest.param(
            "",
            {"outcome": "fetch_failed", "detail": "no response"},
            False,
            "fetch_failed:no response; content:empty",
            id="failed-fetch-empty-content",
        ),
        pytest.param(
            RSS_NO_CONTENT_FALLBACK,
            None,
            None,
            "content:placeholder:rss_no_content",
            id="placeholder",
        ),
    ],
)
def test_title_only_reason_yields_each_reason(
    content: str,
    fetch_outcome: dict[str, str] | None,
    substantive: bool | None,
    expected: str | None,
) -> None:
    """
    BREAKS: Reading an unknown verdict as false, letting the model override a failed
    code check, or dropping the fetch_failed prefix each changes one row here.
    """
    assert title_only_reason(content, fetch_outcome, substantive) == expected


_ALL_CONTENT = [_READABLE, _NAVIGATION, "", RSS_NO_CONTENT_FALLBACK]
_ALL_OUTCOMES = [None, _FAILED_429, _EMPTY_OUTCOME, _EXTRACTED]
_ALL_VERDICTS = [True, False, None]


def _summary(substantive: bool | None) -> ContentSummary:
    return ContentSummary(
        summary="s",
        reading_summary="r",
        alpha_insights=[],
        patterns=[],
        quotes=[],
        tools=[],
        urls=[],
        metadata={},
        substantive=substantive,
    )


@pytest.mark.parametrize(
    ("content", "fetch_outcome", "substantive"),
    list(itertools.product(_ALL_CONTENT, _ALL_OUTCOMES, _ALL_VERDICTS)),
)
def test_build_llm_analysis_stores_title_only_exactly_when_a_reason_is_set(
    content: str, fetch_outcome: dict[str, str] | None, substantive: bool | None
) -> None:
    """
    SC-5 over every combination: `title_only` is true exactly when
    `title_only_reason` is set, a readable substantive item gets no reason, and the
    stored reason is what the one helper returns.
    BREAKS: Computing the flag separately from the reason lets the two disagree.
    """
    evaluation = ContentEvaluation(priority=None, matched_interests=[])

    analysis = build_llm_analysis(
        _summary(substantive), evaluation, content, fetch_outcome
    )

    reason = title_only_reason(content, fetch_outcome, substantive)
    assert analysis["title_only_reason"] == reason
    assert analysis["title_only"] is (reason is not None)
    if content == _READABLE and substantive is not False:
        assert analysis["title_only_reason"] is None


# ---------------------------------------------------------------------------
# hn-discussion SC-3: a link line over a discussion with prose is readable, with
# content_basis = discussion; over an empty discussion it stays title-only.
# ---------------------------------------------------------------------------

_LINK_ONLY = format_reddit_link_only("https://blog.example.org/post")
_THREAD_BODY = (
    "I read this yesterday and found the second half more convincing than the first."
)
# Reddit's author line carries "u/", HN's does not.
_DISCUSSION_ITEMS = [
    pytest.param(
        _LINK_ONLY
        + format_discussion([{"author": "someone", "body": _THREAD_BODY}], "u/"),
        id="reddit",
    ),
    pytest.param(
        _LINK_ONLY + format_discussion([{"author": "pg", "body": _THREAD_BODY}]),
        id="hn",
    ),
]
_EMPTY_DISCUSSION = f"{_LINK_ONLY}\n\n{DISCUSSION_HEADER}"
_NO_EVALUATION = ContentEvaluation(priority=None, matched_interests=[])


@pytest.mark.parametrize("content", _DISCUSSION_ITEMS)
def test_a_link_over_a_discussion_with_prose_is_readable_with_discussion_basis(
    content: str,
) -> None:
    """
    BREAKS: the old link-without-article rule makes this title-only and records no
    basis, so a blocked article with a substantive thread is stored as a bare title.
    """
    assert title_only_reason(content, _FAILED_429, None) is None

    analysis = build_llm_analysis(_summary(True), _NO_EVALUATION, content, _FAILED_429)

    assert analysis["content_basis"] == "discussion"
    assert analysis["title_only"] is False
    assert analysis["title_only_reason"] is None


def test_a_link_over_an_empty_discussion_is_title_only_with_no_basis() -> None:
    """
    BREAKS: treating the discussion header as a discussion rescues an item with
    nothing to read, or records a basis for it.
    """
    assert (
        title_only_reason(_EMPTY_DISCUSSION, None, None)
        == "content:link_without_article"
    )

    analysis = build_llm_analysis(_summary(None), _NO_EVALUATION, _EMPTY_DISCUSSION)

    assert "content_basis" not in analysis
    assert analysis["title_only"] is True
    assert analysis["title_only_reason"] == "content:link_without_article"


def test_content_with_an_article_records_no_basis_even_beside_a_discussion() -> None:
    """
    BREAKS: recording the basis whenever a discussion block exists warns the reader
    that the article was unavailable when it was not.
    """
    content = f"{_LINK_ONLY}\n\n{_READABLE}" + format_discussion(
        [{"author": "pg", "body": _THREAD_BODY}]
    )

    analysis = build_llm_analysis(_summary(True), _NO_EVALUATION, content)

    assert "content_basis" not in analysis


def test_a_discussion_the_model_calls_not_substantive_keeps_its_basis() -> None:
    content = _LINK_ONLY + format_discussion([{"author": "pg", "body": _THREAD_BODY}])

    analysis = build_llm_analysis(_summary(False), _NO_EVALUATION, content)

    assert analysis["title_only_reason"] == "model:not_substantive"
    assert analysis["content_basis"] == "discussion"


# ---------------------------------------------------------------------------
# Through the real pipeline: the model's verdict and the fetcher's outcome
# ---------------------------------------------------------------------------


class _LLMStub:
    """An OpenAI-shaped completion endpoint whose light reply carries a configurable
    `substantive` verdict (omitted when None) and counts the requests it serves."""

    def __init__(self) -> None:
        self.substantive: bool | None = True
        # When set, the summarize request's (system, user) prompts decide the verdict,
        # standing in for a model that does what the prompt it was given asks.
        self.verdict: Callable[[str, str], bool | None] | None = None
        self.summarize_requests: list[tuple[str, str]] = []
        self.requests = 0
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                request = _json.loads(self.rfile.read(length))
                outer.requests += 1
                system, user = (m["content"] for m in request["messages"][:2])
                verdict = outer.substantive
                if "SUBSTANTIVE:" in system:
                    outer.summarize_requests.append((system, user))
                    if outer.verdict is not None:
                        verdict = outer.verdict(system, user)
                reply: dict[str, Any] = {
                    "summary": "A stubbed summary.",
                    "reading_summary": "A stubbed reading summary.",
                    "alpha_insights": [],
                    "patterns": [],
                    "quotes": [],
                    "tools": [],
                    "urls": [],
                    "priority": "low",
                    "matched_interests": [],
                    "reasoning": "stubbed",
                }
                if verdict is not None:
                    reply["substantive"] = verdict
                payload = {
                    "id": "stub",
                    "object": "chat.completion",
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": _json.dumps(reply)},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
                body = _json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address[0], self._server.server_address[1]
        assert isinstance(host, str)
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
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


class _RecordingFetcher:
    """Returns fixed items and records the `known_readable_ids` it was handed."""

    def __init__(self, items: list[ContentItem]) -> None:
        self.items = items
        self.known_readable_ids: set[str] | None = None

    def fetch_content(self, source, **kw):
        self.known_readable_ids = kw.get("known_readable_ids")
        return self.items


def _orchestrator(
    base_url: str, storage: Storage, rss_fetcher: _RecordingFetcher | _NullFetcher | None = None
) -> DaemonOrchestrator:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), base_url)
    config = Config.from_file()
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=rss_fetcher or _NullFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )


def _source(source_id: str) -> dict[str, Any]:
    return {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }


def _stored_analysis(storage: Storage, external_id: str) -> dict[str, Any]:
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", (external_id,)
    ).fetchone()
    assert row is not None, f"{external_id} was not stored"
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    return stored["analysis"]


@pytest.mark.parametrize(
    ("substantive", "expected_reason"),
    [(False, "model:not_substantive"), (True, None), (None, None)],
    ids=["model-says-no", "model-says-yes", "verdict-absent"],
)
def test_pipeline_stores_the_model_verdict_as_the_reason(
    test_db: Path,
    isolated_xdg_env: Path,
    llm_stub: _LLMStub,
    substantive: bool | None,
    expected_reason: str | None,
) -> None:
    """
    SC-5 through `build_llm_analysis` in the pipeline: readable content the model
    calls not substantive is stored title-only with `model:not_substantive`; a yes
    or an absent verdict stores no reason.
    BREAKS: A summarizer that never parses `substantive`, or a pipeline that does
    not pass it on, stores no reason for the False row.
    """
    llm_stub.substantive = substantive
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _orchestrator(llm_stub.base_url, storage)
    item = ContentItem(
        source_id=source_id,
        external_id="verdict-item",
        title="Verdict Item",
        url="https://example.com/verdict",
        content=_READABLE,
    )

    result = orchestrator.analyze_and_store_item(item, _source(source_id))

    assert result is not None
    analysis = _stored_analysis(storage, "verdict-item")
    assert analysis["title_only_reason"] == expected_reason
    assert analysis["title_only"] is (expected_reason is not None)


_JOKE = "Ha, this is the best joke I have heard all week."


def _model_following_its_prompt(system: str, user: str) -> bool | None:
    """The verdict of a model that does what the prompt it was given asks.

    Told the article is unavailable but asked whether the piece itself is present, it
    says no; asked whether the discussion says something substantive, it judges the
    thread, and a thread that is only the joke says nothing.
    """
    if "reader discussion in the text" not in system:
        return False
    return _JOKE not in user


@pytest.mark.parametrize(
    ("body", "expected_reason"),
    [(_THREAD_BODY, None), (_JOKE, "model:not_substantive")],
    ids=["substantive-thread", "joke-thread"],
)
def test_pipeline_summarizes_a_discussion_basis_item_as_discussion(
    test_db: Path,
    isolated_xdg_env: Path,
    llm_stub: _LLMStub,
    body: str,
    expected_reason: str | None,
) -> None:
    """
    SC-4: the summarize request for a discussion-basis item tells the model the article
    is unavailable and the text is discussion and carries the discussion-based
    substantive wording; a substantive thread's item is not marked
    model:not_substantive and a one-line joke thread's still is.
    BREAKS: a pipeline that does not pass the basis to the summarizer sends the
    piece-itself wording, which marks every rescued item not substantive; one that
    always passes the discussion wording would stop a joke thread being caught.
    """
    llm_stub.verdict = _model_following_its_prompt
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _orchestrator(llm_stub.base_url, storage)
    item = ContentItem(
        source_id=source_id,
        external_id="discussion-item",
        title="Discussion Item",
        url="https://example.com/discussion",
        content=_LINK_ONLY + format_discussion([{"author": "pg", "body": body}]),
    )

    result = orchestrator.analyze_and_store_item(item, _source(source_id))

    assert result is not None
    [(system, user)] = llm_stub.summarize_requests
    assert "ARTICLE UNAVAILABLE" in user
    assert "reader discussion about the story, not the article" in user
    assert "reader discussion in the text" in system
    assert "contains the piece itself" not in system
    analysis = _stored_analysis(storage, "discussion-item")
    assert analysis["content_basis"] == "discussion"
    assert analysis["title_only_reason"] == expected_reason


def test_pipeline_sends_an_item_with_its_article_the_piece_itself_wording(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub
) -> None:
    """
    BREAKS: sending the discussion note for every item tells the model its articles
    are unavailable.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _orchestrator(llm_stub.base_url, storage)
    item = ContentItem(
        source_id=source_id,
        external_id="article-item",
        title="Article Item",
        url="https://example.com/article",
        content=_READABLE,
    )

    orchestrator.analyze_and_store_item(item, _source(source_id))

    [(system, user)] = llm_stub.summarize_requests
    assert "ARTICLE UNAVAILABLE" not in user
    assert "contains the piece itself" in system
    assert "content_basis" not in _stored_analysis(storage, "article-item")


def test_pipeline_replaces_a_stored_discussion_basis_when_the_article_arrives(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub
) -> None:
    """
    BREAKS: merging a stored analysis's keys back in keeps the old
    content_basis on an item that now has its article, so the reader is told the
    article is unavailable beside a summary of it.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _orchestrator(llm_stub.base_url, storage)
    item = ContentItem(
        source_id=source_id,
        external_id="recovered-item",
        title="Recovered Item",
        url="https://example.com/recovered",
        content=_READABLE,
        analysis={"content_basis": "discussion"},
    )

    orchestrator.analyze_and_store_item(item, _source(source_id))

    assert "content_basis" not in _stored_analysis(storage, "recovered-item")


def test_pipeline_carries_the_fetch_outcome_into_stored_analysis_and_reason(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub
) -> None:
    """
    SC-3 and SC-5: a fetcher's `fetch_outcome` rides in the item's analysis beside
    metrics, survives the orchestrator merge into stored analysis, and prefixes the
    reason when the fetch failed.
    BREAKS: Passing no outcome from the item to `build_llm_analysis` stores the bare
    content rule; dropping it in the merge loses `fetch_outcome` from storage.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _orchestrator(llm_stub.base_url, storage)
    item = ContentItem(
        source_id=source_id,
        external_id="failed-fetch-item",
        title="Failed Fetch Item",
        url="https://example.com/failed",
        content=RSS_NO_CONTENT_FALLBACK,
        analysis={"metrics": {"score": 1}, "fetch_outcome": _FAILED_429},
    )

    result = orchestrator.analyze_and_store_item(item, _source(source_id))

    assert result is not None
    analysis = _stored_analysis(storage, "failed-fetch-item")
    assert analysis["fetch_outcome"] == _FAILED_429
    assert analysis["metrics"] == {"score": 1}
    assert (
        analysis["title_only_reason"]
        == "fetch_failed:HTTP 429; content:placeholder:rss_no_content"
    )


def test_fetch_cycle_settles_model_only_items_and_retries_content_items(
    test_db: Path, isolated_xdg_env: Path, llm_stub: _LLMStub
) -> None:
    """
    SC-6: over stored items title-only for `model:not_substantive`, for
    `content:no_prose`, and a readable one, a fetch cycle hands the fetcher a
    `known_readable_ids` holding the model-only and readable items but not the
    content one, and spends no LLM call on the model-only item while it still
    re-analyses the content item whose fresh fetch is readable.
    BREAKS: Treating a `model:` row as unreadable re-extracts and re-analyses it every
    cycle; treating a `content:` row as settled stops its retry.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    def seed(external_id: str, analysis: dict[str, Any]) -> None:
        storage.add_content(
            ContentItem(
                source_id=source_id,
                external_id=external_id,
                title=external_id,
                url=f"https://example.com/{external_id}",
                content=_READABLE if external_id != "content-only" else _NAVIGATION,
                analysis=analysis,
            )
        )

    seed("model-only", {"title_only": True, "title_only_reason": "model:not_substantive"})
    seed("content-only", {"title_only": True, "title_only_reason": "content:no_prose"})
    seed("readable", {"title_only": False, "title_only_reason": None})

    fetched = [
        ContentItem(
            source_id=source_id,
            external_id=external_id,
            title=external_id,
            url=f"https://example.com/{external_id}",
            content=_READABLE,
        )
        for external_id in ("model-only", "content-only", "readable")
    ]
    fetcher = _RecordingFetcher(fetched)
    orchestrator = _orchestrator(llm_stub.base_url, storage, rss_fetcher=fetcher)

    stats = orchestrator.fetch_source_content(_source(source_id))

    assert fetcher.known_readable_ids is not None
    assert fetcher.known_readable_ids == {"model-only", "readable"}
    assert stats["items_processed"] == 1
    assert llm_stub.requests >= 1
    assert _stored_analysis(storage, "model-only")["title_only_reason"] == (
        "model:not_substantive"
    ), "a settled model-only item must not be re-analysed"
    assert _stored_analysis(storage, "content-only")["title_only"] is False
