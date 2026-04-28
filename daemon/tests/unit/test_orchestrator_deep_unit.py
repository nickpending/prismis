"""Unit tests for orchestrator deep extraction gate -- INV-002 and SC-5.

INV-002: Deep extraction failure must NEVER raise into the main pipeline.
Item must be stored with light summary only; analysis must have no deep_extraction key.

SC-5: When deep extraction succeeds, the embedding text fed to Embedder must
combine summary + synthesis so search reflects the richer content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.storage import Storage


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> None:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def test_inv002_deep_failure_does_not_block_storage(test_db: Path) -> None:
    """
    INV-002: When deep_extractor.extract() raises, the pipeline must still call
    create_or_update_content and the stored item must have no deep_extraction key.

    BREAKS: A missing try/except (or a bare re-raise) in the orchestrator means
    any gpt-5-mini hiccup kills the item -- it never gets stored. One rate-limit
    error takes down the entire content pipeline.
    """
    from prismis_daemon.deep_extractor import ContentDeepExtractor
    from prismis_daemon.orchestrator import DaemonOrchestrator

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    # Extractor that always raises
    class FailingExtractor(ContentDeepExtractor):
        def extract(self, content, title="", url=""):
            raise RuntimeError("Simulated gpt-5-mini rate limit error")

    failing_extractor = FailingExtractor("prismis-openai-deep")

    # Minimal stubs -- only what orchestrator.__init__ needs
    class _NullFetcher:
        def fetch(self, *a, **kw):
            return []

    class _FakeSummaryResult:
        summary = "Light summary text."
        reading_summary = "Reading summary."
        alpha_insights = []
        patterns = []
        entities = []
        quotes = []
        tools = []
        urls = []
        metadata = {"summarization_mode": "standard", "word_count": 10}

    class _FakeSummarizer:
        def summarize_with_analysis(self, **kw):
            return _FakeSummaryResult()

    class _FakeEvaluationResult:
        class _Priority:
            value = "high"

        priority = _Priority()
        matched_interests = ["AI"]
        reasoning = "Matches AI interest."
        preference_influenced = False

    class _FakeEvaluator:
        def evaluate_content(self, **kw):
            return _FakeEvaluationResult()

    class _FakeNotifier:
        def send_high_priority_notification(self, *a, **kw):
            pass

    class _FakeConfig:
        auto_extract = "high"
        context = "AI, machine learning"
        llm_light_service = "prismis-openai"
        llm_deep_service = "prismis-openai-deep"

        # Make dict-style access safe
        def get(self, key, default=None):
            return getattr(self, key, default)

    # Record whether create_or_update_content is called
    called_ids = []
    original_create = storage.create_or_update_content

    def tracking_create(item_arg):
        result = original_create(item_arg)
        called_ids.append(result[0])
        return result

    storage.create_or_update_content = tracking_create

    # Build a minimal source dict that matches what orchestrator expects
    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    config = _FakeConfig()
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NullFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=_FakeSummarizer(),
        evaluator=_FakeEvaluator(),
        notifier=_FakeNotifier(),
        config=config,
        deep_extractor=failing_extractor,
    )

    # Inject the item directly into the pipeline step that processes items
    # by patching the fetcher's fetch result for this source
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

    orchestrator.rss_fetcher = _StubRSSFetcher()

    # Pre-seed existing IDs as empty so deduplication doesn't skip our item
    storage.get_existing_external_ids = lambda sid: set()

    stats = orchestrator.fetch_source_content(source_dict)

    # INV-002 assertion 1: pipeline did not crash -- stats returned
    assert stats is not None, (
        "fetch_source_content must return stats even on deep failure"
    )
    assert len(stats.get("errors", [])) == 0, (
        f"Deep extraction failure must not appear in pipeline errors: {stats['errors']}"
    )

    # INV-002 assertion 2: item was stored
    assert len(called_ids) == 1, (
        f"create_or_update_content must be called exactly once; called {len(called_ids)} times"
    )

    # INV-002 assertion 3: stored analysis has no deep_extraction key
    stored = storage.get_content_by_id(called_ids[0])
    assert stored is not None, "Stored item must be retrievable"
    stored_analysis = stored.get("analysis") or {}
    assert "deep_extraction" not in stored_analysis, (
        "INV-002: deep extraction failure must leave no deep_extraction key in analysis"
    )


def test_sc5_embedding_combines_summary_and_synthesis(test_db: Path) -> None:
    """
    SC-5: When deep extraction succeeds, text_for_embedding must be
    summary + "\\n\\n" + synthesis so semantic search reflects the richer content.

    BREAKS: If the orchestrator only embeds the light summary, queries matching
    the synthesis text (counterintuitive findings, buried ledes) return no results
    even though the synthesis was stored -- search is blind to the deep content.

    Seam used: orchestrator.embedder is set in __init__ as self.embedder = embedder or Embedder().
    Injecting a stub Embedder that records text= arguments bypasses the real model
    without patching any internal module.
    SEARCHED orchestrator.py for text_for_embedding construction -- found at lines 337-342.
    VERIFIED: self.embedder is publicly accessible and replaceable post-construction.
    """
    from prismis_daemon.deep_extractor import ContentDeepExtractor
    from prismis_daemon.orchestrator import DaemonOrchestrator

    LIGHT_SUMMARY = "Light summary text."
    DEEP_SYNTHESIS = "Counterintuitive: revenue shrank despite headline growth figures."

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

    # Stub Embedder that records the text= argument
    class _RecordingEmbedder:
        def __init__(self):
            self.recorded_texts = []

        def generate_embedding(self, text: str, title: str = ""):
            self.recorded_texts.append(text)
            # Return a minimal 384-dim zero vector so add_embedding doesn't error
            return [0.0] * 384

        def get_dimension(self):
            return 384

    class _FakeSummaryResult:
        summary = LIGHT_SUMMARY
        reading_summary = "Reading summary."
        alpha_insights = []
        patterns = []
        entities = []
        quotes = []
        tools = []
        urls = []
        metadata = {"summarization_mode": "standard", "word_count": 10}

    class _FakeSummarizer:
        def summarize_with_analysis(self, **kw):
            return _FakeSummaryResult()

    class _FakeEvaluationResult:
        class _Priority:
            value = "high"

        priority = _Priority()
        matched_interests = ["AI"]
        reasoning = "Matches AI interest."
        preference_influenced = False

    class _FakeEvaluator:
        def evaluate_content(self, **kw):
            return _FakeEvaluationResult()

    class _FakeNotifier:
        def send_high_priority_notification(self, *a, **kw):
            pass

    class _FakeConfig:
        auto_extract = "high"
        context = "AI, machine learning"
        llm_light_service = "prismis-openai"
        llm_deep_service = "prismis-openai-deep"

        def get(self, key, default=None):
            return getattr(self, key, default)

    class _NullFetcher:
        def fetch(self, *a, **kw):
            return []

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
        summarizer=_FakeSummarizer(),
        evaluator=_FakeEvaluator(),
        notifier=_FakeNotifier(),
        config=_FakeConfig(),
        deep_extractor=_SucceedingExtractor("prismis-openai-deep"),
        embedder=recording_embedder,
    )

    storage.get_existing_external_ids = lambda sid: set()
    orchestrator.fetch_source_content(source_dict)

    # SC-5: exactly one embedding call was made
    assert len(recording_embedder.recorded_texts) == 1, (
        f"Expected exactly 1 embedding call, got {len(recording_embedder.recorded_texts)}"
    )

    embedded_text = recording_embedder.recorded_texts[0]

    # SC-5: text must contain both the light summary and the deep synthesis
    assert LIGHT_SUMMARY in embedded_text, (
        "SC-5: embedding text must include the light summary"
    )
    assert DEEP_SYNTHESIS in embedded_text, (
        "SC-5: embedding text must include the deep synthesis so search reflects it"
    )

    # SC-5: synthesis comes AFTER summary (summary\\n\\nsynthesis order per orchestrator:342)
    summary_pos = embedded_text.index(LIGHT_SUMMARY)
    synthesis_pos = embedded_text.index(DEEP_SYNTHESIS)
    assert summary_pos < synthesis_pos, (
        "SC-5: summary must precede synthesis in embedding text"
    )
