"""Unit tests for DaemonOrchestrator.analyze_and_store_item (SC-1).

SC-1: the fetch loop's per-item work (summarize, evaluate, build_llm_analysis,
merge with fetcher metrics, kind classification, the deep-extraction gate with
the title_only skip, create_or_update_content, embedding) lives in one method
that takes an item and its source and returns what it stored. This file drives
that method directly, not through fetch_source_content -- the seam job 2's
refetch command reuses.

Real collaborators throughout, per Principle I: real Storage over a sealed test
database, the real ContentSummarizer/ContentEvaluator driven through llm-core's
real `complete()` against a local HTTP stub (the one collaborator the
constitution permits faking), and a hand-written stand-in kind classifier
passed through the constructor seam for the INV-002 failure case -- not a
patch of internal state.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

_READABLE_CONTENT = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)


class _NullFetcher:
    """A fetcher whose source is never selected by these tests' source dicts."""

    def fetch_content(self, source, **kw):
        return []


class _FailingKindClassifier:
    """Stands in for KindClassifier.classify raising -- INV-002's failure path."""

    def classify(self, **kwargs):
        raise RuntimeError("Simulated decisions-endpoint failure")


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _real_config(base_url: str) -> Config:
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, base_url)
    return Config.from_file()


def _build_orchestrator(config: Config, storage: Storage, kind_classifier=None):
    from prismis_daemon.orchestrator import DaemonOrchestrator

    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NullFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        kind_classifier=kind_classifier,
    )


def test_analyze_and_store_item_returns_what_it_stored(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """A fresh, readable item is summarized, evaluated and stored, and the
    method's return dict carries the content_id and is_new flag the caller
    needs to update its own stats -- without going through fetch_source_content.
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _build_orchestrator(config, storage)

    item = ContentItem(
        source_id=source_id,
        external_id="direct-001",
        title="Direct Call Article",
        url="https://example.com/direct",
        content=_READABLE_CONTENT,
        analysis={"metrics": {"score": 42}},
    )
    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    result = orchestrator.analyze_and_store_item(item, source_dict)

    assert result is not None, "a readable item with content must be stored"
    assert result["is_new"] is True
    assert result["kind_classify_failure"] is None
    assert result["deep_extract_failure"] is None
    assert result["content_id"], "returned content_id must be usable by the caller"

    stored = storage.get_content_by_id(result["content_id"])
    assert stored is not None
    assert stored["summary"]
    assert stored["analysis"]["title_only"] is False
    # Fetcher metrics must survive the merge (SC-1's "merge with fetcher metrics").
    assert stored["analysis"]["metrics"] == {"score": 42}

    # Calling again with the same external_id updates the same row in place.
    result2 = orchestrator.analyze_and_store_item(item, source_dict)
    assert result2 is not None
    assert result2["is_new"] is False
    assert result2["content_id"] == result["content_id"]


def test_analyze_and_store_item_returns_none_when_summarization_skips(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """Empty content makes summarize_with_analysis return None; the method must
    return None rather than storing a blank item (mirrors the fetch loop's own
    `if not summary_result: continue`).
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _build_orchestrator(config, storage)

    item = ContentItem(
        source_id=source_id,
        external_id="empty-001",
        title="Empty Article",
        url="https://example.com/empty",
        content="",
    )
    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    result = orchestrator.analyze_and_store_item(item, source_dict)

    assert result is None
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("empty-001",)
    ).fetchone()
    assert row is None, "nothing must be stored when summarization is skipped"


def test_analyze_and_store_item_kind_failure_does_not_block_storage(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """INV-002: a raising kind classifier must not stop the item from being
    stored. The failure comes back in the return dict for the caller to record
    in its own stats, instead of raising past this method.
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    orchestrator = _build_orchestrator(
        config, storage, kind_classifier=_FailingKindClassifier()
    )

    item = ContentItem(
        source_id=source_id,
        external_id="kind-fail-001",
        title="Kind Failure Article",
        url="https://example.com/kind-fail",
        content=_READABLE_CONTENT,
    )
    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    result = orchestrator.analyze_and_store_item(item, source_dict)

    assert result is not None, "kind classification failure must not block storage"
    assert result["kind_classify_failure"] is not None
    assert "Simulated decisions-endpoint failure" in result["kind_classify_failure"]

    stored = storage.get_content_by_id(result["content_id"])
    assert stored is not None
    assert "kind" not in stored["analysis"], (
        "INV-002: a failed kind classification must leave no kind key"
    )
