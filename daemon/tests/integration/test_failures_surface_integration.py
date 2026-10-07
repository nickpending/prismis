"""A failure surfaces where its caller already reports failure (gh #58).

Invariant protected:
  - a defect is never turned into the value a genuine empty result produces: a prune
    count that cannot be read is a 500, not 0; a vote query that cannot run is an
    "Error", not "No voted articles found"; an evaluation reply that cannot be parsed
    fails the item, not "no priority"; an archival or backfill that cannot run raises,
    not a zero count
  - the fetch cycle's isolation boundaries (one source, one item) keep the cycle going
    and record the traceback at WARNING or above

Real collaborators throughout: real Storage over a sealed database that is broken by
dropping a table, the real FastAPI app, the real orchestrator, summarizer and evaluator.
The only fakes are the LLM provider boundary (`complete`) and hand-written fetchers and a
hand-written summarizer passed through the orchestrator's constructor, as the other
orchestrator tests do.
"""

import json
import logging
import os
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.context_auto_updater import ContextAutoUpdater
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import TEST_API_KEY, add_new_content, configure_local_services, make_config

_HEADERS = {"X-API-Key": TEST_API_KEY}

_READABLE = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)

_LLM_COMPLETE = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock


@pytest.fixture(autouse=True)
def _clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _drop_table(db_path: Path, table: str) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(f"DROP TABLE {table}")


# --- SC-5: the prune endpoints ----------------------------------------------------------


def test_prune_endpoints_answer_500_when_the_count_query_fails(test_db: Path) -> None:
    """
    INVARIANT: A count that cannot be read is a 500 on both prune endpoints
    BREAKS: Storage.count_unprioritized returns 0 on a database error, so the count
            endpoint reports "Found 0 unprioritized items" and POST /api/prune answers
            200 "No unprioritized items to prune" over a database it never read
    """
    _drop_table(test_db, "content")
    client = TestClient(app)

    count = client.get("/api/prune/count", headers=_HEADERS)
    prune = client.post("/api/prune", headers=_HEADERS)

    assert count.status_code == 500, count.text
    assert prune.status_code == 500, prune.text
    assert "Failed to count unprioritized items" in count.json()["message"]
    assert "Failed to prune items" in prune.json()["message"]
    assert "No unprioritized items to prune" not in prune.text
    assert "no such table" not in count.text + prune.text, "detail goes to the log"


def test_storage_count_unprioritized_raises_the_database_error(test_db: Path) -> None:
    """
    INVARIANT: count_unprioritized raises sqlite3.Error, as its docstring declares
    BREAKS: a caller that does not wrap it (the CLI, the TUI's count) reads 0
    """
    storage = Storage(test_db)
    _drop_table(test_db, "content")

    with pytest.raises(sqlite3.Error):
        storage.count_unprioritized()


# --- SC-6: the context auto-updater -----------------------------------------------------


def _updater_over_a_broken_vote_query(
    test_db: Path, isolated_xdg_env: Path
) -> ContextAutoUpdater:
    """An updater that is due to run, with enough votes, whose vote query cannot run.

    Dropping `content.summary` (a column only get_content_by_feedback selects) leaves
    get_feedback_statistics working, so the failure lands exactly in
    `_get_voted_articles`.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Feed")
    content_id = add_new_content(
        storage,
        ContentItem(
            source_id=source_id,
            external_id="voted-1",
            title="Voted",
            url="https://example.com/voted",
            content=_READABLE,
        ),
    )
    with sqlite3.connect(test_db) as conn:
        conn.execute(
            "UPDATE content SET user_feedback = 'up' WHERE id = ?", (content_id,)
        )
        conn.execute("ALTER TABLE content DROP COLUMN summary")
    config = make_config(
        context_auto_update_enabled=True, context_auto_update_min_votes=1
    )
    return ContextAutoUpdater(config, storage)


def test_update_returns_an_error_when_the_vote_query_fails(
    test_db: Path,
    isolated_xdg_env: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: a vote query that cannot run is (False, "Error: ...") with the traceback
               logged, never (False, "No voted articles found")
    BREAKS: _get_voted_articles returns [] on any storage error, so a broken database
            reads as "nobody voted" and the context is never updated, silently
    """
    updater = _updater_over_a_broken_vote_query(test_db, isolated_xdg_env)

    with caplog.at_level(logging.WARNING):
        ok, message = updater.update()

    assert ok is False
    assert message.startswith("Error:"), message
    assert "No voted articles found" not in message
    failure = [
        r
        for r in caplog.records
        if r.levelno >= logging.ERROR and r.exc_info and "summary" in str(r.exc_info[1])
    ]
    assert failure, "the traceback of the real failure must be logged"


def test_update_with_no_votes_still_says_no_voted_articles(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """
    INVARIANT: a database with no votes is still "No voted articles found"
    BREAKS: the fix over-reaches and a genuine empty result reads as an error
    """
    storage = Storage(test_db)
    config = make_config(
        context_auto_update_enabled=True, context_auto_update_min_votes=1
    )
    updater = ContextAutoUpdater(config, storage)

    assert updater._get_voted_articles() == []


# --- SC-7: an evaluation reply that cannot be parsed -------------------------------------


def test_evaluate_content_raises_when_the_reply_cannot_be_parsed() -> None:
    """
    INVARIANT: valid JSON of the wrong shape fails the evaluation
    BREAKS: _parse_evaluation_response swallows the parse failure into a
            ContentEvaluation with priority None, and the item is stored unprioritized
    """
    evaluator = ContentEvaluator("prismis-openai")

    # extract_json hands the evaluator only objects, so an array reaches the parser
    # when the parser is called directly.
    with pytest.raises(AttributeError):
        evaluator._parse_evaluation_response(["not", "an", "object"])  # type: ignore[arg-type]

    # A reply that is an object, through the real evaluate_content, whose priority is
    # not a string.
    reply = json.dumps({"priority": 5, "matched_interests": ["AI"]})
    with patch(_LLM_COMPLETE, return_value=_result(reply)):  # claudex-guard: allow-mock
        with pytest.raises(AttributeError):
            evaluator.evaluate_content(content="c", title="t", url="u", context="ctx")


class _NullFetcher:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


class _FetcherReturning:
    def __init__(self, items: list[ContentItem]) -> None:
        self.items = items

    def fetch_content(self, source: dict, **kw: object) -> list[ContentItem]:
        return self.items


class _FetcherRaising:
    def fetch_content(self, source: dict, **kw: object) -> list:
        raise RuntimeError("unexpected fetch defect")


class _SummarizerRaisingFor:
    """The real summarizer, except that one title's analysis hits an unexpected defect."""

    def __init__(self, real: ContentSummarizer, title: str) -> None:
        self.real = real
        self.title = title

    def summarize_with_analysis(self, **kwargs):
        if kwargs["title"] == self.title:
            raise RuntimeError("unexpected analysis defect")
        return self.real.summarize_with_analysis(**kwargs)


def _orchestrator(
    config: Config,
    storage: Storage,
    *,
    rss=None,
    reddit=None,
    summarizer=None,
    embedder=None,
) -> DaemonOrchestrator:
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=rss or _NullFetcher(),
        reddit_fetcher=reddit or _NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=summarizer or ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        embedder=embedder,
    )


def _item(source_id: str, title: str) -> ContentItem:
    return ContentItem(
        source_id=source_id,
        external_id=f"ext-{title}",
        title=title,
        url=f"https://example.com/{title}",
        content=_READABLE,
    )


def _result(text: str) -> MagicMock:  # claudex-guard: allow-mock
    result = MagicMock()  # claudex-guard: allow-mock
    result.text = text
    result.tokens.input = 1
    result.tokens.output = 1
    result.cost = 0.0
    result.model = "stub-model"
    result.duration_ms = 1
    return result


def _config(local_pipeline_stub: str) -> Config:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    return Config.from_file()


def test_a_cycle_isolates_a_failed_source_and_a_failed_item_and_logs_both(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: an unexpected failure in one source's fetch, or in one item's analysis,
               costs only that source or that item, and is logged with its traceback
    BREAKS: the cycle stops, another source's or item's results are lost, or the failure
            is recorded as a bare message with no traceback, so an operator cannot tell
            a defect from a hiccup
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    storage.add_source("https://broken.example.com/feed", "rss", "Broken")
    reddit_id = storage.add_source("reddit://fine", "reddit", "Fine")
    orchestrator = _orchestrator(
        config,
        storage,
        rss=_FetcherRaising(),
        reddit=_FetcherReturning([_item(reddit_id, "boom"), _item(reddit_id, "fine")]),
        summarizer=_SummarizerRaisingFor(
            ContentSummarizer(config.llm_light_service), "boom"
        ),
    )

    with caplog.at_level(logging.WARNING):
        stats = orchestrator.run_once()

    assert any("unexpected fetch defect" in e for e in stats["errors"]), stats["errors"]
    assert any("unexpected analysis defect" in e for e in stats["errors"]), stats["errors"]
    assert stats["total_analyzed"] == 2, "the second source still ran"
    assert stats["total_new"] == 1, "the item after the failed one was analyzed and stored"
    stored = {
        row[0] for row in storage.conn.execute("SELECT external_id FROM content")
    }
    assert stored == {"ext-fine"}, f"only the item that did not fail is stored: {stored}"

    logged = {
        str(r.exc_info[1])
        for r in caplog.records
        if r.levelno >= logging.WARNING and r.exc_info
    }
    assert "unexpected fetch defect" in logged, "fetch failure logged without traceback"
    assert "unexpected analysis defect" in logged, (
        "analysis failure logged without traceback"
    )


@pytest.mark.parametrize(
    ("priority", "matched_interests"),
    [(5, ["AI"]), ("high", "AI, Python")],
    ids=["non-string-priority", "matched-interests-not-a-list"],
)
def test_a_malformed_evaluation_reply_fails_the_item_in_the_cycle(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    priority: object,
    matched_interests: object,
) -> None:
    """
    INVARIANT: an item whose evaluation reply cannot be parsed is recorded in
               stats['errors'] and is not stored
    BREAKS: the item is stored with priority null and never evaluated again
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    reddit_id = storage.add_source("reddit://fine", "reddit", "Fine")
    orchestrator = _orchestrator(
        config, storage, reddit=_FetcherReturning([_item(reddit_id, "odd")])
    )
    reply = json.dumps(
        {
            "summary": "A summary.",
            "reading_summary": "A reading summary.",
            "alpha_insights": [],
            "patterns": [],
            "entities": [],
            "quotes": [],
            "tools": [],
            "urls": [],
            "priority": priority,
            "matched_interests": matched_interests,
            "reasoning": "r",
        }
    )

    with patch(_LLM_COMPLETE, return_value=_result(reply)):  # claudex-guard: allow-mock
        stats = orchestrator.run_once()

    assert len(stats["errors"]) == 1, stats["errors"]
    assert "Failed to analyze item 'odd'" in stats["errors"][0]
    assert stats["total_new"] == 0
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("ext-odd",)
    ).fetchone()
    assert row is None, "an unparseable evaluation must not be stored as unprioritized"


# --- SC-8: archival and backfill ----------------------------------------------------------


def test_archival_policy_raises_when_the_storage_call_fails(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    INVARIANT: run_archival_policy raises the storage failure to its scheduler
    BREAKS: it returns {"archived_count": 0}, which reads as "nothing was old enough"
    """
    config = make_config(archival_enabled=True)
    storage = Storage(test_db)
    _drop_table(test_db, "content")
    orchestrator = _orchestrator(config, storage)

    with pytest.raises(sqlite3.Error):
        orchestrator.run_archival_policy()


def test_backfill_raises_when_the_storage_call_fails(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """
    INVARIANT: backfill_embeddings raises the storage failure to its scheduler
    BREAKS: it returns {"processed": 0, "failed": 0}, which reads as "nothing to backfill"
    """
    config = make_config()
    storage = Storage(test_db)
    _drop_table(test_db, "content")
    orchestrator = _orchestrator(config, storage)

    with pytest.raises(sqlite3.Error):
        orchestrator.backfill_embeddings()


class _EmbedderFailingFor:
    def __init__(self, bad_title: str) -> None:
        self.bad_title = bad_title

    def generate_embedding(self, text: str, title: str = "") -> list[float]:
        if title == self.bad_title:
            raise RuntimeError("unexpected embedding defect")
        return [0.0] * 384


def test_backfill_counts_one_items_embedding_failure_and_continues(
    test_db: Path,
    isolated_xdg_env: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: one item's embedding failure is `failed` and the loop continues
    BREAKS: the per-item boundary is removed along with the outer catch and one bad item
            stops the backfill for every item after it, or is counted as processed
    """
    config = make_config()
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Feed")
    for title in ("bad", "good"):
        add_new_content(storage, _item(source_id, title))
    orchestrator = _orchestrator(
        config, storage, embedder=_EmbedderFailingFor("bad")
    )

    with caplog.at_level(logging.WARNING):
        result = orchestrator.backfill_embeddings()

    assert result == {"processed": 1, "failed": 1}
    assert any(
        r.exc_info and "unexpected embedding defect" in str(r.exc_info[1])
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ), "the failed item's traceback must be logged"
