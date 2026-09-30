"""Integration tests for the orchestrator's kind classification step -- content-kind
work order, job 2 (SC-3).

SC-3: given a running orchestrator with the classifier configured, when a new item is
summarised and evaluated, the stored item's analysis carries kind (or null when
unclassified) and kind_confidence, and the call is logged as an llm.call observability
event with action classify_kind and its billed cost, like the summarize and evaluate
calls; when the classifier call fails, the item is still stored with its summary and
priority, the kind is absent, and the failure is recorded in the run's stats rather
than raised (INV-002).

Real collaborators throughout: the real Summarizer and Evaluator drive the real
llm-core `complete()` against a local HTTP stub standing in for the LLM, real Storage
over the sealed test database, and a real DaemonOrchestrator with a real KindClassifier.
Only submit_decision -- kind_classifier's own provider boundary, the decisions
endpoint isn't chat-completions shaped so it never goes through llm_client.complete()
-- is stood in for, per Principle I. What actually landed is read back through
Storage's own public methods, and observability events are read back through the real
JSONL log, not tracked by patching either.
"""

from __future__ import annotations

import dataclasses
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest
from conftest import configure_local_services

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.kind_classifier import KindClassifier
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger, set_run_id
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer
from prismis_daemon.verify_chain import read_run_events

# The decisions-endpoint provider boundary itself (Principle I's one permitted fake).
_PATCH_SUBMIT = "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock

_KIND_SERVICE = "prismis-openrouter-kind-test"


class _FakeDecisionCall:
    """Minimal stand-in for kind_classifier.DecisionCall."""

    def __init__(self, answers: dict, cost: float = 0.00007) -> None:
        self.answers = answers
        self.model = "typesafe/jev-1.13-test"
        self.cost = cost
        self.duration_ms = 42


def _kind_answer(choice: str, confidence: float) -> dict:
    return {"kind": {"type": "choice", "choice": choice, "confidence": confidence}}


class _NullFetcher:
    """A fetcher whose source is never selected by this test's source dict."""

    def fetch_content(self, source, **kw):
        return []


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _events_for(run_id: str) -> list[dict]:
    return read_run_events(get_logger().base_dir, run_id)


class _SingleItemFetcher:
    """A fetcher that always returns the one item a test hands it."""

    def __init__(self, item: ContentItem) -> None:
        self._item = item

    def fetch_content(self, source, **kw):
        return [self._item]


def _configured_orchestrator(
    local_pipeline_stub: str, storage: Storage, item: ContentItem
) -> tuple[DaemonOrchestrator, Config]:
    """A real orchestrator with light services + a real KindClassifier wired in,
    the same shape __main__.py builds when kind_service IS configured."""
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, local_pipeline_stub)
    config = Config.from_file()
    config = dataclasses.replace(config, llm_kind_service=_KIND_SERVICE)

    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_SingleItemFetcher(item),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        kind_classifier=KindClassifier(_KIND_SERVICE),
    )
    return orchestrator, config


def _source_dict(source_id: str) -> dict:
    return {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }


def test_sc3_classified_item_stores_kind_and_logs_observability_event(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    SC-3: a confident classification lands in the stored item's analysis as "kind" and
    "kind_confidence", and an llm.call event with action=classify_kind and its billed
    cost is recorded, exactly like the summarize/evaluate calls.

    BREAKS: The orchestrator never calls the classifier, silently drops the result
    before storage, or the classify_kind observability event goes unlogged.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    pipeline_item = ContentItem(
        source_id=source_id,
        external_id="kind-success-001",
        title="Foo 2.0 Released",
        url="https://example.com/kind-success",
        content="Foo 2.0 ships with a new plugin system.",
        analysis={},
    )
    orchestrator, _config = _configured_orchestrator(
        local_pipeline_stub, storage, pipeline_item
    )

    fake = _FakeDecisionCall(_kind_answer("release", 0.92))
    with patch(_PATCH_SUBMIT, return_value=fake):
        stats = orchestrator.fetch_source_content(_source_dict(source_id))

    assert stats["errors"] == [], stats["errors"]
    assert stats["kind_classify_failures"] == [], stats["kind_classify_failures"]

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("kind-success-001",)
    ).fetchone()
    assert row is not None, "create_or_update_content must have stored the item"

    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    stored_analysis = stored.get("analysis") or {}
    assert stored_analysis.get("kind") == "release"
    assert stored_analysis.get("kind_confidence") == pytest.approx(0.92)
    # The light pass itself must still have run -- SC-3's item is one that was
    # "summarised and evaluated", not one where kind classification replaced it.
    assert stored.get("summary")
    assert stored.get("priority") is not None

    events = [
        e
        for e in _events_for(run_id)
        if e.get("event") == "llm.call" and e.get("action") == "classify_kind"
    ]
    assert len(events) == 1, f"expected exactly one classify_kind event, got {events}"
    event = events[0]
    assert event["status"] == "success"
    assert event["cost_usd"] == pytest.approx(0.00007)


def test_sc3_unclassified_item_stores_null_kind(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    SC-3's "or null when unclassified" clause: a low-confidence answer still stores
    kind_confidence, but kind itself is None/null, not a forced guess.
    BREAKS: A below-threshold answer is stored as if it were a trusted kind.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    pipeline_item = ContentItem(
        source_id=source_id,
        external_id="kind-unclassified-001",
        title="An Ambiguous Post",
        url="https://example.com/kind-unclassified",
        content="Hard to say what kind of thing this is.",
        analysis={},
    )
    orchestrator, _config = _configured_orchestrator(
        local_pipeline_stub, storage, pipeline_item
    )

    fake = _FakeDecisionCall(_kind_answer("release", 0.4))
    with patch(_PATCH_SUBMIT, return_value=fake):
        stats = orchestrator.fetch_source_content(_source_dict(source_id))

    assert stats["errors"] == []
    assert stats["kind_classify_failures"] == []

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("kind-unclassified-001",)
    ).fetchone()
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None, "Stored item must be retrievable"
    stored_analysis = stored.get("analysis") or {}
    assert stored_analysis.get("kind") is None
    assert "kind" in stored_analysis, "the key itself must be present, holding null"


def test_inv002_kind_classification_failure_does_not_block_storage(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    INV-002 (SC-3): when the classifier call itself fails (network, auth, non-2xx),
    the item is still stored with its light summary and priority, "kind" is absent
    from analysis, and the failure is recorded in the run's stats rather than raised
    or counted as a pipeline error.

    BREAKS: A classifier hiccup propagates out of fetch_source_content and the item
    is lost entirely, or the failure is silently swallowed with no trace in stats.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    pipeline_item = ContentItem(
        source_id=source_id,
        external_id="kind-fail-001",
        title="An Article During An Outage",
        url="https://example.com/kind-fail",
        content="Content that never gets classified.",
        analysis={},
    )
    orchestrator, _config = _configured_orchestrator(
        local_pipeline_stub, storage, pipeline_item
    )

    with patch(_PATCH_SUBMIT, side_effect=RuntimeError("connection refused")):
        stats = orchestrator.fetch_source_content(_source_dict(source_id))

    # INV-002 assertion 1: not raised, not counted as a pipeline error
    assert stats["errors"] == [], (
        f"kind classification failure must not appear in pipeline errors: {stats['errors']}"
    )
    # ...but it is returned, so the degradation is readable without logs
    assert len(stats["kind_classify_failures"]) == 1, stats["kind_classify_failures"]
    assert "connection refused" in stats["kind_classify_failures"][0]

    # INV-002 assertion 2: item was stored anyway, with its summary and priority
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("kind-fail-001",)
    ).fetchone()
    assert row is not None, "create_or_update_content must have stored the item"

    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    assert stored.get("summary"), "item must keep its light summary despite the failure"
    assert stored.get("priority") is not None, "item must keep its evaluated priority"

    # INV-002 assertion 3: no kind key at all -- absent, not null
    stored_analysis = stored.get("analysis") or {}
    assert "kind" not in stored_analysis, (
        "a failed call must leave no kind key in analysis, not a null one"
    )
