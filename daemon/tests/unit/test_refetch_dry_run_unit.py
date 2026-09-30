"""Unit tests for refetch.py's --dry-run cost estimate (SC-3, refetch-unreadable).

SC-3: the dry-run cost estimate is an upper bound: the summed 99th-percentile
real summarize, evaluate and classify_kind cost per item in the observability
log -- not a hard-coded constant, not a median or mean, not a sum of every cost
ever logged. These tests seed the observability log directly (real
ObservabilityLogger, real JSONL files) and drive
`upper_bound_cost_estimate`/`run_refetch` against it, rather than reading
the arithmetic off the source.

Real Storage and a real DaemonOrchestrator throughout (Principle I); the
youtube/reddit fetchers are poisoned to raise if ever invoked, which is how
"makes no extraction" is proved rather than merely read off run_refetch's
dry-run branch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.observability import get_logger as get_obs_logger
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.readability import format_youtube_no_transcript
from prismis_daemon.refetch import run_refetch, upper_bound_cost_estimate
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer


class _PoisonFetcher:
    """Records every call it receives, then raises -- proves "makes no
    extraction" (SC-3) by the call count staying zero, which survives even
    where a caller's own try/except would otherwise swallow the raise and
    hide that the call happened at all."""

    def __init__(self) -> None:
        self.calls = 0

    def refetch_transcript(self, *_a, **_kw):
        self.calls += 1
        raise AssertionError("dry run must not re-extract a YouTube transcript")

    def refetch_one(self, *_a, **_kw):
        self.calls += 1
        raise AssertionError("dry run must not re-extract a Reddit submission")

    def fetch_content(self, *_a, **_kw):
        self.calls += 1
        raise AssertionError("dry run must not fetch")


def _build_orchestrator(
    config: Config, storage: Storage, poison: _PoisonFetcher
) -> DaemonOrchestrator:
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=poison,
        reddit_fetcher=poison,
        youtube_fetcher=poison,
        file_fetcher=poison,
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )


def _seed_unreadable_youtube_item(storage: Storage, video_id: str) -> None:
    source_id = storage.add_source(
        f"https://youtube.com/@chan-{video_id}", "youtube", "Chan"
    )
    url = f"https://www.youtube.com/watch?v={video_id}"
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=url,
            title="Unreadable Video",
            url=url,
            content=format_youtube_no_transcript("Unreadable Video"),
        )
    )


def test_dry_run_reports_the_selection_and_makes_no_extraction_or_write(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """SC-3: a dry run reports how many items would be re-extracted and
    touches neither a fetcher nor storage's write path.
    BREAKS: A dry run that calls the fetchers (even without acting on the
    result) crashes against _PoisonFetcher; one that patches title_only
    "for free" would leave a readable row where the seeded unreadable one was.
    """
    config = Config.from_file()
    storage = Storage(test_db)
    poison = _PoisonFetcher()
    orchestrator = _build_orchestrator(config, storage, poison)

    _seed_unreadable_youtube_item(storage, "one")
    _seed_unreadable_youtube_item(storage, "two")

    report = run_refetch(orchestrator, "youtube", limit=50, dry_run=True)

    assert poison.calls == 0, "SC-3: a dry run must call no fetcher at all"
    assert report.dry_run is True
    assert report.selected == 2
    assert report.outcomes == []

    rows = storage.get_unreadable_content("youtube", limit=50)
    assert len(rows) == 2, "no row may have been patched to readable by the dry run"
    for row in rows:
        assert row["analysis"] is None or not row["analysis"].get("title_only")


def test_dry_run_cost_estimate_is_the_summed_p99_real_cost_per_action(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """SC-3: the estimate sums, per action, the 99th percentile of that action's
    successful real cost_usd entries, so an expensive call counts (it is a bound,
    not a typical cost); a failed call's cost and an unrelated action
    (deep_extract) do not.
    BREAKS: A median or mean per action (the 2026-09-30 youtube backfill cost
    $0.0077 per item against a $0.0030 median estimate), summing every logged
    cost, or a flat constant all give a different number here.
    """
    config = Config.from_file()
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage, _PoisonFetcher())

    logger = get_obs_logger()
    # summarize: p99 of [0.01, 0.01, 0.10] = 0.10 -- a median (0.01) or mean
    # (0.04) would produce a visibly different estimate below.
    for cost in (0.01, 0.10, 0.01):
        logger.log("llm.call", action="summarize", status="success", cost_usd=cost)
    # evaluate: p99 of [0.004, 0.006] = 0.006
    for cost in (0.004, 0.006):
        logger.log("llm.call", action="evaluate", status="success", cost_usd=cost)
    # classify_kind: p99 of [0.001] = 0.001
    logger.log("llm.call", action="classify_kind", status="success", cost_usd=0.001)
    # noise that must not count toward the estimate: a failed call, an
    # unrelated action, and a non-numeric cost (api.openai.com-shaped).
    logger.log("llm.call", action="summarize", status="error", cost_usd=99.0)
    logger.log("llm.call", action="deep_extract", status="success", cost_usd=5.0)
    logger.log("llm.call", action="summarize", status="success", cost_usd=None)
    logger.log("fetcher.complete", status="success")

    _seed_unreadable_youtube_item(storage, "one")
    _seed_unreadable_youtube_item(storage, "two")
    _seed_unreadable_youtube_item(storage, "three")

    expected_per_item = 0.10 + 0.006 + 0.001
    assert upper_bound_cost_estimate(get_obs_logger().base_dir) == pytest.approx(
        expected_per_item
    )

    report = run_refetch(orchestrator, "youtube", limit=50, dry_run=True)

    assert report.selected == 3
    assert report.estimated_cost == pytest.approx(expected_per_item * 3)


def test_dry_run_cost_estimate_with_no_history_is_zero(
    test_db: Path, isolated_xdg_env: Path
) -> None:
    """A fresh install with no observability history yet (or one whose
    configured service reports no cost, e.g. api.openai.com) gets a $0
    estimate rather than the dry run raising."""
    config = Config.from_file()
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage, _PoisonFetcher())

    _seed_unreadable_youtube_item(storage, "one")

    report = run_refetch(orchestrator, "youtube", limit=50, dry_run=True)

    assert report.selected == 1
    assert report.estimated_cost == 0.0
