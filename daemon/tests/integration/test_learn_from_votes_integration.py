"""#96: votes never reach scoring as learned preferences, and nothing rewrites context.md.

Learned preferences credited each vote to the evaluator's own matched_interests, so one
downvote on a post scored High taught "User dislikes: <the High topics it matched>" and
cerebro's unprioritized share went from ~55% to ~81%. The learned-preferences block and
the context.md auto-updater are gone; learn_from_votes is now the switch for carve-out
proposals, which are not built yet, so turning it on changes nothing the daemon sends.
"""

import dataclasses
import importlib.util
import inspect
import logging
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from rich.console import Console

from prismis_daemon import analysis, evaluator as evaluator_module
from prismis_daemon.__main__ import build_scheduler
from prismis_daemon.api import app, get_storage
from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer
from prismis_daemon.verify_chain import build_orchestrator

from conftest import (
    TEST_API_KEY,
    add_new_content,
    configure_local_services,
    message_text,
)

_HIGH_TOPICS = [
    "LLM-driven security tools",
    "AI-driven features in security tooling (e.g. Burp Suite AI)",
    "Human-in-the-loop & human-AI capability arbitrage",
]

_READABLE = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)

# What the retired block put into an evaluator's system prompt, and the strings its
# for_llm_context summary was built from.
_LEARNED_MARKERS = ("LEARNED USER PREFERENCES", "User dislikes", "User prefers")


class _NoItems:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


class _OneItem:
    def __init__(self, item: ContentItem) -> None:
        self.item = item

    def fetch_content(self, source: dict, **kw: object) -> list[ContentItem]:
        return [self.item]


def _seed_cerebro_votes(storage: Storage) -> None:
    """cerebro's pattern: upvotes on items matching High topics, then one downvote on
    an item that matched the same three High topics."""
    source_id = storage.add_source("https://votes.example.com/feed", "rss", "Votes")
    votes = [("up", _HIGH_TOPICS)] * 5 + [("down", _HIGH_TOPICS)]
    for n, (vote, topics) in enumerate(votes):
        content_id = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=f"voted-{n}",
                title=f"Voted {n}",
                url=f"https://votes.example.com/{n}",
                content=_READABLE,
                analysis={"matched_interests": topics},
            ),
        )
        storage.update_content_status(content_id, user_feedback=vote)


def _system_prompts(requests: list[dict[str, Any]]) -> list[str]:
    return [message_text(request, "system") for request in requests]


@pytest.mark.parametrize(
    "learn_from_votes", [True, False], ids=["learning-on", "learning-off-by-default"]
)
def test_run_once_sends_no_learned_preferences_whatever_the_switch(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    stub_requests: list[dict[str, Any]],
    learn_from_votes: bool,
) -> None:
    """
    INVARIANT: run_once evaluates a new item with no learned-preferences block in any
               system prompt and stores no preference_influenced key, with cerebro's
               votes in the database and learn_from_votes on or off
    BREAKS: the learning-on row sends "User dislikes: <High topics>" to the evaluator,
            which is how one downvote reached every evaluation on cerebro
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    config = Config.from_file()
    assert config.learn_from_votes is False, "the shipped template must ship it off"
    if learn_from_votes:
        config = dataclasses.replace(config, learn_from_votes=True)

    storage = Storage(test_db)
    _seed_cerebro_votes(storage)
    source_id = storage.add_source("https://news.example.com/feed", "reddit", "News")
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NoItems(),
        reddit_fetcher=_OneItem(
            ContentItem(
                source_id=source_id,
                external_id="new-item",
                title="New item",
                url="https://news.example.com/new",
                content=_READABLE,
            )
        ),
        youtube_fetcher=_NoItems(),
        file_fetcher=_NoItems(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        config=config,
    )

    stats = orchestrator.run_once()

    assert stats["total_new"] == 1, stats["errors"]
    systems = _system_prompts(stub_requests)
    assert any("evaluates articles" in s for s in systems), "the evaluator was reached"
    for system in systems:
        for marker in _LEARNED_MARKERS:
            assert marker not in system
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = 'new-item'"
    ).fetchone()
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    assert "preference_influenced" not in stored["analysis"]


def test_the_learned_preferences_machinery_no_longer_exists() -> None:
    """An import or call of any piece of it fails."""
    assert not hasattr(analysis, "get_learned_preferences")
    assert (
        "learned_preferences"
        not in inspect.signature(ContentEvaluator.evaluate_content).parameters
    )
    assert (
        "learned_preferences"
        not in inspect.signature(ContentEvaluator._build_evaluation_prompt).parameters
    )
    assert "preference_influenced" not in {
        f.name for f in dataclasses.fields(evaluator_module.ContentEvaluation)
    }
    with pytest.raises(AttributeError):
        getattr(analysis, "get_learned_preferences")  # noqa: B009


def test_feedback_statistics_endpoint_keeps_its_counts_and_drops_the_prompt_summary(
    test_db: Path,
) -> None:
    """
    INVARIANT: GET /api/feedback/statistics still returns totals, by_source,
               topics_upvoted, topics_downvoted and time_period with the values the
               votes give, and no for_llm_context key
    BREAKS: removing the summary by dropping the whole statistics query would take
            :context suggest's inputs with it
    """
    storage = Storage(test_db)
    _seed_cerebro_votes(storage)

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        response = TestClient(app).get(
            "/api/feedback/statistics?since_days=30",
            headers={"X-API-Key": TEST_API_KEY},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert set(data) == {
        "totals",
        "by_source",
        "topics_upvoted",
        "topics_downvoted",
        "time_period",
    }
    assert data["totals"] == {"upvotes": 5, "downvotes": 1, "total_votes": 6}
    assert data["time_period"] == "last 30 days"
    assert data["topics_upvoted"] == [{"topic": t, "count": 5} for t in _HIGH_TOPICS]
    assert data["topics_downvoted"] == [{"topic": t, "count": 1} for t in _HIGH_TOPICS]
    assert data["by_source"][0]["source_name"] == "Votes"
    assert data["by_source"][0]["upvotes"] == 5
    assert data["by_source"][0]["downvotes"] == 1


def test_a_config_written_before_this_change_loads_and_nothing_rewrites_context(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: a config.toml with the auto-update keys and learn_from_votes = true loads,
               registers no context-update job, and no context.md rewrite or backup
               happens with enough votes to have qualified the old auto-updater
    BREAKS: honouring the old keys would rewrite context.md from the per-topic counts
            that taught the dislike; refusing them would stop every existing install
            from starting
    """
    template = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    assert template.count("learn_from_votes = false\n") == 1, "template switch moved"
    assert "auto_update" not in template and "backup_count" not in template
    config_path = isolated_xdg_env / "config.toml"
    config_path.write_text(
        template.replace(
            "learn_from_votes = false\n",
            "learn_from_votes = true\nauto_update_enabled = true\n"
            "auto_update_interval_days = 7\nauto_update_min_votes = 5\n"
            "backup_count = 3\n",
        )
    )
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    context_path = isolated_xdg_env / "context.md"
    before = context_path.read_text()

    with caplog.at_level(logging.WARNING, logger="prismis_daemon.config"):
        config = Config.from_file()
    storage = Storage(test_db)
    _seed_cerebro_votes(storage)
    orchestrator = build_orchestrator(config, storage, Console(quiet=True))
    scheduler, _ = build_scheduler(config, orchestrator, storage)
    orchestrator.run_once()

    assert config.learn_from_votes is True
    assert any("auto_update_enabled" in r.message for r in caplog.records)
    job_ids = {job.id for job in scheduler.get_jobs()}
    assert "fetch_and_analyze" in job_ids, "the scheduler was built"
    assert not [j for j in job_ids if "context" in j], job_ids
    assert importlib.util.find_spec("prismis_daemon.context_auto_updater") is None
    assert context_path.read_text() == before
    assert not (isolated_xdg_env / "context_backups").exists()
    assert not (isolated_xdg_env / ".context_last_update").exists()


def test_the_shipped_template_carries_learn_from_votes_and_no_auto_update_keys() -> (
    None
):
    template = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)

    assert template.count("learn_from_votes = false\n") == 1
    for retired in (
        "auto_update_enabled",
        "auto_update_interval_days",
        "auto_update_min_votes",
        "backup_count",
    ):
        assert retired not in template
