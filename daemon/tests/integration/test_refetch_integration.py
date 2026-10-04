"""Integration tests for `prismis-daemon refetch` (SC-2, SC-3, refetch-unreadable).

Drives `refetch.run_refetch` directly -- house convention for a typer command's
logic (verify_chain.execute_chain is the same shape, tested the same way,
never through the CLI wrapper) -- over a real Storage, a real
DaemonOrchestrator and real fetcher code. Per Principle I: the RSS recovery
path's outbound article fetch goes through a real local HTTP server; the
YouTube recovery path runs a real subprocess stand-in for yt-dlp (mirrors
test_youtube_fetcher_unit.py's own pattern); only the LLM is faked, via the
same `local_pipeline_stub` every other real-orchestrator integration test in
this suite uses.
"""

from __future__ import annotations

import http.server
import json as jsonlib
import os
import sys
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.llm_client import CompleteResult, TokenUsage
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.readability import RSS_NO_CONTENT_FALLBACK, format_youtube_no_transcript
from prismis_daemon.refetch import run_refetch
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import configure_local_services

# complete() -- the LLM provider boundary Principle I permits faking (it backs
# every "complete" attribute the internal-mock guard allowlists), patched at
# the name llm_call.py actually calls.
_PATCH_COMPLETE = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock

_ARTICLE_HTML = (
    b"<!doctype html><html><body><article>"
    b"<h1>A Real Article</h1>"
    b"<p>This is a genuine article body, long enough for trafilatura's "
    b"extraction heuristics to treat it as the main content rather than "
    b"boilerplate chrome around it, with a second sentence for good measure.</p>"
    b"</article></body></html>"
)


class _ArticleServer:
    """Serves one real, extractable article at /recoverable; every other
    path 404s -- the shape a still-unreadable item's re-extraction attempt
    (a dead or now-removed link) actually hits. Counts hits per path so a
    dry-run test can prove no request was ever made, not just that no row
    changed."""

    def __init__(self) -> None:
        self.hits: dict[str, int] = {}
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_GET(self) -> None:
                outer.hits[self.path] = outer.hits.get(self.path, 0) + 1
                if self.path == "/recoverable":
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.send_header("Content-Length", str(len(_ARTICLE_HTML)))
                    self.end_headers()
                    self.wfile.write(_ARTICLE_HTML)
                else:
                    self.send_error(404)

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
def article_server() -> Iterator[_ArticleServer]:
    server = _ArticleServer()
    try:
        yield server
    finally:
        server.shutdown()


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class _NullFetcher:
    def fetch_content(self, source, **kw):
        return []

    def refetch_transcript(self, *_a, **_kw):
        raise AssertionError("this test's selection carries no youtube rows")

    def refetch_one(self, *_a, **_kw):
        raise AssertionError("this test's selection carries no reddit rows")


def _real_config(base_url: str) -> Config:
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, base_url)
    return Config.from_file()


def _build_orchestrator(
    config: Config, storage: Storage, youtube_fetcher=None
) -> DaemonOrchestrator:
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NullFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=youtube_fetcher or _NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
    )


def _youtube_fake_yt_dlp_cmd(tmp_path: Path, video_id: str, transcript: str) -> list[str]:
    """A runnable yt-dlp stand-in: writes a real transcript file for
    `video_id` only, mirroring real yt-dlp exiting cleanly with nothing
    written for a video that truly has no captions."""
    script = tmp_path / "fake_yt_dlp.py"
    script.write_text(
        "import sys\n"
        "args = sys.argv[1:]\n"
        "out = args[args.index('--output') + 1]\n"
        "vid = args[-1].split('v=')[1]\n"
        f"if vid == {video_id!r}:\n"
        "    path = out.replace('%(id)s', vid).replace('%(ext)s', 'en-orig.vtt')\n"
        f"    open(path, 'w').write('WEBVTT\\n\\n00:00:00.000 --> 00:00:01.000\\n{transcript}\\n')\n"
    )
    return [sys.executable, str(script)]


def test_refetch_rss_recovers_readable_items_and_patches_still_unreadable_ones(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str, article_server: _ArticleServer
) -> None:
    """
    SC-2: of two not-readable stored RSS items, the one whose URL now serves
    a real article is re-extracted, re-analysed through the same pipeline
    (SC-1) and stored in place; the one whose URL still 404s is left with its
    stored content untouched and gets title_only: true patched on with no LLM
    call. SC-3: the real run reports both outcomes.
    BREAKS: A refetch that calls analyze_and_store_item for every selected row
    regardless of readability would spend an LLM call patching the
    still-unreadable item's title_only flag, which this proves against by
    checking its content is byte-identical to what was seeded.

    real_cost here is exactly 0.0, not merely proven non-negative: local_
    pipeline_stub's /v1/chat/completions response carries no usage.cost field
    (conftest.py), and llm_client.complete() only requests OpenRouter's cost
    extension for an openrouter.ai host, so every call this test makes really
    does report no cost -- this is what the stub actually guarantees, not a
    placeholder for "some number >= 0". The run_id/status/event filtering
    _real_cost_since applies to arrive at a nonzero sum is proven separately,
    with a known per-call cost, by
    test_refetch_real_run_sums_the_real_cost_of_its_own_llm_calls below.
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage)

    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="recoverable-rss",
            title="Recoverable RSS Item",
            url=f"{article_server.base_url}/recoverable",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="still-unreadable-rss",
            title="Still Unreadable RSS Item",
            url=f"{article_server.base_url}/missing",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )

    report = run_refetch(orchestrator, "rss", limit=10)

    assert report.selected == 2
    assert report.recovered == 1
    assert report.still_title_only == 1
    assert report.failed == 0
    assert report.real_cost == 0.0, (
        "local_pipeline_stub reports no cost_usd for any call -- see the "
        "docstring above for why this is the stub's real guarantee"
    )

    recovered_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("recoverable-rss",)
    ).fetchone()
    recovered = storage.get_content_by_id(recovered_row["id"])
    assert recovered is not None
    assert "genuine article body" in recovered["content"]
    assert recovered["summary"], "a recovered item must be re-analysed, not just re-stored"
    assert recovered["analysis"]["title_only"] is False

    still_unreadable_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("still-unreadable-rss",)
    ).fetchone()
    still_unreadable = storage.get_content_by_id(still_unreadable_row["id"])
    assert still_unreadable is not None
    assert still_unreadable["content"] == RSS_NO_CONTENT_FALLBACK, (
        "a still-unreadable item's stored content must be left alone"
    )
    assert still_unreadable["analysis"]["title_only"] is True
    # title-only-reasons SC-5: the no-LLM patch stores the reason through the one
    # helper, naming the failed fetch and the content rule, beside title_only.
    assert (
        still_unreadable["analysis"]["title_only_reason"]
        == "fetch_failed:HTTP 404; content:empty"
    )
    assert still_unreadable["analysis"]["fetch_outcome"] == {
        "outcome": "fetch_failed",
        "detail": "HTTP 404",
    }
    assert recovered["analysis"]["title_only_reason"] is None
    assert still_unreadable["summary"] is None, (
        "a still-unreadable item must get no LLM call -- no summary is written"
    )


def test_refetch_youtube_recovers_via_real_subprocess_extraction(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    tmp_path: Path,
) -> None:
    """
    SC-2: a stored YouTube item whose video now has a real transcript is
    re-extracted through YouTubeFetcher's own single-item path (a real
    subprocess standing in for yt-dlp, not a mocked method) and re-analysed.
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)

    youtube_fetcher = YouTubeFetcher(config=config)
    youtube_fetcher.yt_dlp_cmd = _youtube_fake_yt_dlp_cmd(
        tmp_path, "recoverable", "a fresh real transcript is now available"
    )
    orchestrator = _build_orchestrator(config, storage, youtube_fetcher=youtube_fetcher)

    source_id = storage.add_source(
        "https://www.youtube.com/@TestChannel", "youtube", "Test Channel"
    )
    video_url = "https://www.youtube.com/watch?v=recoverable"
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=video_url,
            title="Recoverable Video",
            url=video_url,
            content=format_youtube_no_transcript("Recoverable Video"),
        )
    )

    report = run_refetch(orchestrator, "youtube", limit=10)

    assert report.selected == 1
    assert report.recovered == 1

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", (video_url,)
    ).fetchone()
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    assert "a fresh real transcript is now available" in stored["content"]
    assert stored["analysis"]["title_only"] is False


def test_refetch_dry_run_against_real_storage_makes_no_write(
    test_db: Path, isolated_xdg_env: Path, article_server: _ArticleServer
) -> None:
    """
    SC-3: a dry run against a real, seeded Storage counts the selection and
    estimates a cost but leaves every row exactly as it was -- no extraction
    (the poisoned _NullFetcher would raise if this test's rss path were ever
    hit) and no write.
    """
    config = Config.from_file()
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage)

    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="untouched-rss",
            title="Untouched RSS Item",
            url=f"{article_server.base_url}/recoverable",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )

    report = run_refetch(orchestrator, "rss", limit=10, dry_run=True)

    assert report.dry_run is True
    assert report.selected == 1
    assert report.outcomes == []
    assert article_server.hits == {}, (
        "SC-3: a dry run must make no extraction -- the article endpoint "
        "must never be hit at all"
    )

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("untouched-rss",)
    ).fetchone()
    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    assert stored["content"] == RSS_NO_CONTENT_FALLBACK
    assert stored["summary"] is None
    assert stored["analysis"] is None


def _fake_complete_result(cost: float) -> CompleteResult:
    """A CompleteResult carrying a known cost -- stands in for complete()
    itself (the LLM provider boundary), not for prismis's own summarize/
    evaluate logic, which still runs for real against this canned JSON."""
    payload = {
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
    return CompleteResult(
        text=jsonlib.dumps(payload),
        model="stub-model",
        provider="stub",
        tokens=TokenUsage(input=10, output=10),
        finish_reason="stop",
        duration_ms=5,
        cost=cost,
    )


def test_refetch_real_run_sums_the_real_cost_of_its_own_llm_calls(
    test_db: Path, isolated_xdg_env: Path, article_server: _ArticleServer
) -> None:
    """
    SC-3 (review finding F-2-2): a real run's reported cost is the sum of
    THIS run's own llm.call cost_usd events -- proved with a known per-call
    cost (complete(), the one LLM boundary faked here) so the expected total
    is independently computable, unlike local_pipeline_stub's stub (which
    reports no cost at all and so cannot tell correct run_id/status/event
    filtering in _real_cost_since apart from broken filtering -- both give 0).
    BREAKS: a run_id mismatch, a missing status=="success" check, or summing
    every llm.call ever logged instead of just this run's, each produce a
    total other than 2 * per_call_cost here.
    """
    config = Config.from_file()
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage)

    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="recoverable-rss",
            title="Recoverable RSS Item",
            url=f"{article_server.base_url}/recoverable",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )

    per_call_cost = 0.00042
    fake = _fake_complete_result(per_call_cost)
    with patch(_PATCH_COMPLETE, return_value=fake):
        report = run_refetch(orchestrator, "rss", limit=10)

    assert report.recovered == 1
    # summarize_with_analysis + evaluate_content each make exactly one
    # complete() call for the one recovered item.
    assert report.real_cost == pytest.approx(per_call_cost * 2)


def _seed_upvote_with_topic(
    storage: Storage, source_id: str, external_id: str, topic: str
) -> None:
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=external_id,
            title=f"Article {external_id}",
            url=f"https://example.com/{external_id}",
            content="Feedback seed content.",
            analysis={"matched_interests": [topic]},
        )
    )
    assert content_id is not None
    storage.update_content_status(content_id, user_feedback="up")


def test_refetch_sources_learned_preferences_like_run_once_does(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str, article_server: _ArticleServer
) -> None:
    """
    SC-2 (review finding F-2-1): a recovered item is evaluated with the
    user's learned preferences, sourced the same way run_once does --
    analysis.get_learned_preferences(self.storage) -- not with None. Proved
    by the evaluator's own preference_influenced flag, which ContentEvaluator
    sets to True only when a truthy learned_preferences string reached
    evaluate_content, and is stored verbatim by build_llm_analysis.
    BREAKS: _refetch_one calling analyze_and_store_item with no third
    argument (defaulting to None) stores preference_influenced: False here,
    silently diverging from the one analysis path SC-1 established.
    """
    config = _real_config(local_pipeline_stub)
    storage = Storage(test_db)
    orchestrator = _build_orchestrator(config, storage)

    feedback_source_id = storage.add_source(
        "https://feeds.example.com/feedback-source", "rss", "Feedback Source"
    )
    # get_learned_preferences requires >= 5 votes in the last 30 days
    # (analysis.py's own min_votes default) before it returns anything.
    for i in range(5):
        _seed_upvote_with_topic(
            storage, feedback_source_id, f"upvoted-{i}", "security tooling"
        )

    rss_source_id = storage.add_source(
        "https://feeds.example.com/rss", "rss", "Test Feed"
    )
    storage.add_content(
        ContentItem(
            source_id=rss_source_id,
            external_id="recoverable-rss",
            title="Recoverable RSS Item",
            url=f"{article_server.base_url}/recoverable",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )

    report = run_refetch(orchestrator, "rss", limit=10)

    assert report.recovered == 1
    recovered_row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("recoverable-rss",)
    ).fetchone()
    recovered = storage.get_content_by_id(recovered_row["id"])
    assert recovered is not None
    assert recovered["analysis"]["preference_influenced"] is True
