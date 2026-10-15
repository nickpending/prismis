"""#96: votes act on their own only when [context] learn_from_votes is on.

Learned preferences credit each vote to the evaluator's own matched_interests, so one
downvote on a post scored High taught "User dislikes: <the High topics it matched>"
and cerebro's unprioritized share went from ~55% to ~81%. Until voting is redesigned,
neither the evaluator nor the context.md auto-updater acts on votes unless the user
turns learn_from_votes on -- including installs whose config.toml the old template
wrote with auto_update_enabled = true.
"""

import dataclasses
import os
from pathlib import Path

import pytest
from rich.console import Console

from prismis_daemon.__main__ import build_scheduler
from prismis_daemon.config import Config
from prismis_daemon.context_auto_updater import ContextAutoUpdater
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer
from prismis_daemon.verify_chain import build_orchestrator

from conftest import TEST_API_KEY, add_new_content, configure_local_services

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


@pytest.mark.parametrize(
    "learn_from_votes", [True, False], ids=["learning-on", "learning-off-by-default"]
)
def test_the_daemon_cycle_scores_without_votes_unless_learning_is_on(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    learn_from_votes: bool,
) -> None:
    """
    INVARIANT: with the shipped template, run_once evaluates new items with no learned
               preferences, whatever the votes; with learn_from_votes on, they reach
               the evaluator
    BREAKS: run_once fetching learned preferences regardless of the switch stores
            preference_influenced True on the learning-off row, which is how one
            downvote reached every evaluation on cerebro
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
        notifier=Notifier(),
        config=config,
    )

    stats = orchestrator.run_once()

    assert stats["total_new"] == 1, stats["errors"]
    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = 'new-item'"
    ).fetchone()
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    assert stored["analysis"]["preference_influenced"] is learn_from_votes


def test_a_config_written_before_96_does_not_rewrite_context_from_votes(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """
    INVARIANT: a config.toml from the old template (auto_update_enabled = true, no
               learn_from_votes key) neither schedules nor runs the context.md
               auto-updater, with enough votes to qualify
    BREAKS: honouring auto_update_enabled alone leaves every existing install --
            cerebro included -- rewriting context.md from the same per-topic counts
            that taught the dislike
    """
    template = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    assert template.count("learn_from_votes = false\n") == 1, "template switch moved"
    assert template.count("auto_update_enabled = false\n") == 1, "template switch moved"
    pre_96 = template.replace("learn_from_votes = false\n", "").replace(
        "auto_update_enabled = false\n", "auto_update_enabled = true\n"
    )
    config_path = Path(os.environ["XDG_CONFIG_HOME"]) / "prismis" / "config.toml"
    config_path.write_text(pre_96)
    config = Config.from_file()
    storage = Storage(test_db)
    _seed_cerebro_votes(storage)

    should, reason = ContextAutoUpdater(config, storage).should_update()
    scheduler, _ = build_scheduler(
        config, build_orchestrator(config, storage, Console(quiet=True)), storage
    )

    assert should is False, reason
    assert "learn_from_votes" in reason, reason
    assert scheduler.get_job("context_auto_update") is None
    assert scheduler.get_job("fetch_and_analyze") is not None, "the scheduler was built"
