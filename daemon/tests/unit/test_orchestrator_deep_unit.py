"""Unit tests for orchestrator deep extraction gate -- INV-002 and SC-5.

INV-002: Deep extraction failure must NEVER raise into the main pipeline.
Item must be stored with light summary only; analysis must have no deep_extraction key.

SC-5: When deep extraction succeeds, the embedding text fed to Embedder must
combine summary + synthesis so search reflects the richer content.

Real collaborators throughout: the real Summarizer and Evaluator drive the real
llm-core `complete()` against a local HTTP stub standing in for the LLM (the one
collaborator the constitution permits faking), the real Notifier, and the real Config
loaded from the sealed XDG config. Storage is real, over the sealed test database --
what actually landed is read back through Storage's own public methods, not tracked by
patching create_or_update_content.

The rss_fetcher, deep_extractor and (in the SC-5 test) embedder stay as small,
constructor-injected stand-ins: they are the seams DaemonOrchestrator's own __init__
exists to take real-or-substitute collaborators through, not a patch of module or
instance state, and the deep-extraction and embedding LOGIC these two tests exist to
prove (INV-002's failure handling, SC-5's text composition) is what they let this file
control deterministically. Driving deep extraction itself through a real LLM stub is
SC-4's job, covering the three dedicated deep-extraction test files.
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
from prismis_daemon.embeddings import Embedder
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.notifier import Notifier
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

_SUMMARY_TEXT = "Light summary text."


class _NullFetcher:
    """A fetcher whose source is never selected by these tests' source dicts."""

    def fetch_content(self, source, **kw):
        return []


def _high_priority_payload(summary: str) -> bytes:
    """An OpenAI-shaped chat-completion body serving both the summarizer and the
    evaluator, mirroring conftest.local_pipeline_stub's canned response but with a
    HIGH priority and a matched interest -- what these tests need to reach the
    deep-extraction gate, which local_pipeline_stub's own fixed "low" does not.
    """
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
                            "summary": summary,
                            "reading_summary": "Reading summary.",
                            "alpha_insights": [],
                            "patterns": [],
                            "entities": [],
                            "quotes": [],
                            "tools": [],
                            "urls": [],
                            "priority": "high",
                            "matched_interests": ["AI"],
                            "reasoning": "Matches AI interest.",
                        }
                    ),
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
    }
    return _json.dumps(payload).encode()


@pytest.fixture
def high_priority_llm_stub() -> Iterator[str]:
    """A local HTTP server answering llm-core's chat-completions call with a fixed
    HIGH-priority, one-matched-interest result -- these two tests' shared need,
    isolated here rather than in conftest.py because local_pipeline_stub's own fixed
    "low" priority serves the rest of the suite and cannot be reused for this.
    """

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = _high_priority_payload(_SUMMARY_TEXT)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _real_config(base_url: str) -> Config:
    """Wire the sealed config's light/deep services at the local stub and load it."""
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, base_url)
    return Config.from_file()


def test_inv002_deep_failure_does_not_block_storage(
    test_db: Path, isolated_xdg_env: Path, high_priority_llm_stub: str
) -> None:
    """
    INV-002: When deep_extractor.extract() raises, the pipeline must still call
    create_or_update_content and the stored item must have no deep_extraction key.

    BREAKS: A missing try/except (or a bare re-raise) in the orchestrator means
    any gpt-5-mini hiccup kills the item -- it never gets stored. One rate-limit
    error takes down the entire content pipeline.
    """
    from prismis_daemon.deep_extractor import ContentDeepExtractor
    from prismis_daemon.orchestrator import DaemonOrchestrator

    config = _real_config(high_priority_llm_stub)
    assert config.llm_deep_service is not None, (
        "configure_local_services always sets a deep service"
    )

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    # Extractor that always raises
    class FailingExtractor(ContentDeepExtractor):
        def extract(self, content, title="", url=""):
            raise RuntimeError("Simulated gpt-5-mini rate limit error")

    failing_extractor = FailingExtractor(config.llm_deep_service)

    from prismis_daemon.models import ContentItem as CI

    pipeline_item = CI(
        source_id=source_id,
        external_id="deep-fail-001",
        title="High Priority Article",
        url="https://example.com/deep-fail",
        content="Substantial article content for deep extraction.",
        analysis={"metrics": {"score": 90}},
    )

    class _StubRSSFetcher:
        def fetch_content(self, source, **kw):
            return [pipeline_item]

    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_StubRSSFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        deep_extractor=failing_extractor,
    )

    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    stats = orchestrator.fetch_source_content(source_dict)

    # INV-002 assertion 1: pipeline did not crash -- stats returned
    assert stats is not None, (
        "fetch_source_content must return stats even on deep failure"
    )
    assert len(stats.get("errors", [])) == 0, (
        f"Deep extraction failure must not appear in pipeline errors: {stats['errors']}"
    )
    # ...but it is returned, so the degradation is readable without logs (#72).
    assert len(stats["deep_extract_failures"]) == 1, stats["deep_extract_failures"]
    assert "rate limit" in stats["deep_extract_failures"][0]

    # INV-002 assertion 2: item was stored -- read back through Storage's own public
    # surface rather than a patched create_or_update_content.
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("deep-fail-001",)
    ).fetchone()
    assert row is not None, "create_or_update_content must have stored the item"

    # INV-002 assertion 3: stored analysis has no deep_extraction key
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None, "Stored item must be retrievable"
    stored_analysis = stored.get("analysis") or {}
    assert "deep_extraction" not in stored_analysis, (
        "INV-002: deep extraction failure must leave no deep_extraction key in analysis"
    )


def test_sc5_embedding_combines_summary_and_synthesis(
    test_db: Path, isolated_xdg_env: Path, high_priority_llm_stub: str
) -> None:
    """
    SC-5: When deep extraction succeeds, text_for_embedding must be
    summary + "\\n\\n" + synthesis so semantic search reflects the richer content.

    BREAKS: If the orchestrator only embeds the light summary, queries matching
    the synthesis text (counterintuitive findings, buried ledes) return no results
    even though the synthesis was stored -- search is blind to the deep content.

    Seam used: orchestrator.embedder is set in __init__ as self.embedder = embedder or Embedder().
    Injecting a stub Embedder that records text= arguments bypasses the real (heavy,
    locally-loaded) sentence-transformers model without patching any internal module --
    it subclasses Embedder itself, so the constructor's declared type is satisfied
    exactly, the same technique the deep_extractor stand-ins below use.
    SEARCHED orchestrator.py for text_for_embedding construction -- found at lines 337-342.
    VERIFIED: self.embedder is publicly accessible and replaceable post-construction.
    """
    from prismis_daemon.deep_extractor import ContentDeepExtractor
    from prismis_daemon.orchestrator import DaemonOrchestrator

    DEEP_SYNTHESIS = "Counterintuitive: revenue shrank despite headline growth figures."

    config = _real_config(high_priority_llm_stub)
    assert config.llm_deep_service is not None, (
        "configure_local_services always sets a deep service"
    )

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    # Extractor that succeeds and returns a synthesis
    class _SucceedingExtractor(ContentDeepExtractor):
        def extract(self, content, title="", url=""):
            return {
                "synthesis": DEEP_SYNTHESIS,
                "quotables": [],
                "model": "gpt-5-mini-test",
                "extracted_at": "2026-04-27T12:00:00+00:00",
            }

    # Embedder subclass that records the text= argument instead of running the real
    # sentence-transformers model.
    class _RecordingEmbedder(Embedder):
        def __init__(self) -> None:
            super().__init__()
            self.recorded_texts: list[str] = []

        def generate_embedding(self, text: str, title: str = "") -> list[float]:
            self.recorded_texts.append(text)
            # Return a minimal 384-dim zero vector so add_embedding doesn't error
            return [0.0] * 384

        def get_dimension(self) -> int:
            return 384

    from prismis_daemon.models import ContentItem as CI

    pipeline_item = CI(
        source_id=source_id,
        external_id="sc5-test-001",
        title="High Priority Article",
        url="https://example.com/sc5-test",
        content="Article content for SC-5 embedding test.",
        analysis={"metrics": {"score": 90}},
    )

    class _StubRSSFetcher:
        def fetch_content(self, source, **kw):
            return [pipeline_item]

    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    recording_embedder = _RecordingEmbedder()
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_StubRSSFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        deep_extractor=_SucceedingExtractor(config.llm_deep_service),
        embedder=recording_embedder,
    )

    orchestrator.fetch_source_content(source_dict)

    # SC-5: exactly one embedding call was made
    assert len(recording_embedder.recorded_texts) == 1, (
        f"Expected exactly 1 embedding call, got {len(recording_embedder.recorded_texts)}"
    )

    embedded_text = recording_embedder.recorded_texts[0]

    # SC-5: text must contain both the light summary and the deep synthesis
    assert _SUMMARY_TEXT in embedded_text, (
        "SC-5: embedding text must include the light summary"
    )
    assert DEEP_SYNTHESIS in embedded_text, (
        "SC-5: embedding text must include the deep synthesis so search reflects it"
    )

    # SC-5: synthesis comes AFTER summary (summary\\n\\nsynthesis order per orchestrator:342)
    summary_pos = embedded_text.index(_SUMMARY_TEXT)
    synthesis_pos = embedded_text.index(DEEP_SYNTHESIS)
    assert summary_pos < synthesis_pos, (
        "SC-5: summary must precede synthesis in embedding text"
    )
