"""A failed Reddit comment fetch is recorded on the stored item (gh #58).

Invariant protected:
  - `comments_outcome` survives the analysis merge into the stored row, and only for the
    post whose comment fetch failed; a post with no comments stores no such key

Real collaborators: the real RedditFetcher building the item, the real orchestrator's
`analyze_and_store_item` merging and storing it, real Storage. The fakes are the praw
submission (a third party's object) and the LLM provider boundary served by the local stub.
"""

import os
from pathlib import Path

import requests

from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services
from fixtures.reddit_mocks import create_self_post_mock


class _NullFetcher:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


def test_a_failed_comment_fetch_reaches_storage_and_a_quiet_post_stores_no_outcome(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    INVARIANT: the failed post stores comments_outcome fetch_failed; the quiet one stores none
    BREAKS: the failure is dropped by the merge, or a post with zero comments is marked
            failed, so the two can never be told apart in storage or the API
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    config = Config.from_file()
    storage = Storage(test_db)
    source_id = storage.add_source("reddit://python", "reddit", "python")
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
    fetcher = RedditFetcher(config=config)
    source = {
        "id": source_id,
        "type": "reddit",
        "name": "python",
        "url": "reddit://python",
    }

    failed = create_self_post_mock(permalink="/r/python/comments/1/failed/")
    failed.comments.replace_more.side_effect = requests.exceptions.ConnectionError("down")
    quiet = create_self_post_mock(permalink="/r/python/comments/2/quiet/")

    stored = {}
    for name, submission in (("failed", failed), ("quiet", quiet)):
        item = fetcher._to_content_item(submission, source_id)
        result = orchestrator.analyze_and_store_item(item, source)
        assert result is not None
        row = storage.get_content_by_id(result["content_id"])
        assert row is not None
        stored[name] = row["analysis"]

    assert stored["failed"]["comments_outcome"] == {
        "outcome": "fetch_failed",
        "detail": "ConnectionError",
    }
    assert "comments_outcome" not in stored["quiet"]
    assert "metrics" in stored["failed"] and "metrics" in stored["quiet"]
