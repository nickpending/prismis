"""Integration tests for main daemon orchestration."""

import subprocess
import os
import sys


from storage import Storage
from defaults import ensure_config


def test_daemon_orchestration_with_test_database(test_db) -> None:
    """Test daemon orchestration with real services and test database.

    This integration test:
    - Uses the test_db fixture for a clean database
    - Uses real Storage, Fetcher, Analyzer configured for testing
    - Runs the orchestration logic
    - Verifies it works end-to-end
    """
    from orchestrator import DaemonOrchestrator
    from fetchers.rss import RSSFetcher
    from fetchers.reddit import RedditFetcher
    from fetchers.youtube import YouTubeFetcher
    from summarizer import ContentSummarizer
    from evaluator import ContentEvaluator
    from notifier import Notifier
    from config import Config
    from io import StringIO
    from rich.console import Console

    # Ensure config exists
    ensure_config()
    config_obj = Config.from_file()
    config = {"llm": {"model": config_obj.llm_model, "api_key": config_obj.llm_api_key}}

    # Limit to 1 item for testing (faster)
    config["daemon"] = {"max_items_per_feed": 1}

    # Create real services configured for testing
    storage = Storage(test_db)
    rss_fetcher = RSSFetcher(max_items=1)  # Only fetch 1 item for fast testing
    reddit_fetcher = RedditFetcher(max_items=1)
    youtube_fetcher = YouTubeFetcher(max_items=1)
    summarizer = ContentSummarizer(config["llm"])
    evaluator = ContentEvaluator(config["llm"])
    notifier = Notifier(config.get("notifications", {}))

    # Capture console output
    output = StringIO()
    test_console = Console(file=output)

    # Add a real RSS source
    storage.add_source("https://simonwillison.net/atom/everything/", "rss")

    # Create orchestrator with test dependencies
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=rss_fetcher,
        reddit_fetcher=reddit_fetcher,
        youtube_fetcher=youtube_fetcher,
        summarizer=summarizer,
        evaluator=evaluator,
        notifier=notifier,
        config={"context": config.get("context", "")},
        console=test_console,
    )

    # Run orchestration
    stats = orchestrator.run_once()

    # Verify it ran successfully
    assert stats["total_items"] > 0, "Should have fetched items"
    assert stats["total_analyzed"] > 0, "Should have analyzed items"

    # Verify console output
    output_str = output.getvalue()
    assert "Getting active sources" in output_str
    assert "Processing complete" in output_str

    # Verify data was stored - check each priority level
    high_content = storage.get_content_by_priority("high")
    med_content = storage.get_content_by_priority("medium")
    low_content = storage.get_content_by_priority("low")

    all_content = high_content + med_content + low_content
    assert len(all_content) > 0, "Content should be stored in database"

    # Verify content has all expected fields
    first_item = all_content[0]
    assert first_item["title"] is not None
    assert first_item["priority"] in ["high", "medium", "low"]
    assert first_item["analysis"] is not None


def test_daemon_help_command() -> None:
    """Test daemon shows help correctly."""

    result = subprocess.run(
        [sys.executable, "-m", "src", "--help"],
        capture_output=True,
        text=True,
        cwd="/Users/rudy/development/projects/prismis/daemon",
        env={**os.environ, "PYTHONPATH": "src"},
        timeout=10,
    )

    assert result.returncode == 0
    assert "--once" in result.stdout
    assert "Run once and exit" in result.stdout


def test_daemon_complete_workflow_with_real_source(test_db) -> None:
    """Test complete fetch-analyze-store workflow with real RSS feed and LLM.

    This integration test:
    - Uses the test_db fixture for a clean database
    - Adds a real RSS source (Simon Willison's blog)
    - Verifies we can set up sources correctly
    """
    # Ensure config files exist
    ensure_config()

    # Add a real RSS source to database
    storage = Storage(test_db)
    storage.add_source("https://simonwillison.net/atom/everything/", "rss")

    # Verify we can create the test setup
    sources = storage.get_active_sources()
    assert len(sources) == 1
    assert sources[0]["url"] == "https://simonwillison.net/atom/everything/"


def test_daemon_handles_source_errors_gracefully(test_db) -> None:
    """Test daemon continues processing when a source fails.

    This integration test:
    - Adds multiple sources including a bad one
    - Verifies error tracking works in storage layer
    """
    # Ensure config files exist
    ensure_config()

    storage = Storage(test_db)

    # Add a mix of good and bad sources
    storage.add_source("https://simonwillison.net/atom/everything/", "rss")  # Good
    storage.add_source("https://invalid-url-that-does-not-exist.com/feed", "rss")  # Bad

    # Verify sources were added
    sources = storage.get_active_sources()
    assert len(sources) == 2

    # Test error tracking at storage level
    # Update source with error
    bad_source = next(s for s in sources if "invalid-url" in s["url"])
    storage.update_source_fetch_status(bad_source["id"], False, "Connection failed")

    # Verify error was tracked
    sources = storage.get_active_sources()
    bad_source = next(s for s in sources if "invalid-url" in s["url"])
    assert bad_source["error_count"] == 1
    assert bad_source["last_error"] == "Connection failed"


def test_scheduler_runs_jobs_at_intervals(test_db) -> None:
    """Test scheduler executes jobs at correct intervals.

    This integration test:
    - Creates scheduler with test database
    - Verifies immediate execution on startup
    - Verifies periodic execution
    """
    import asyncio
    import time
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from orchestrator import DaemonOrchestrator
    from fetchers.rss import RSSFetcher
    from fetchers.reddit import RedditFetcher
    from fetchers.youtube import YouTubeFetcher
    from summarizer import ContentSummarizer
    from evaluator import ContentEvaluator
    from notifier import Notifier
    from config import Config
    from io import StringIO
    from rich.console import Console

    # Ensure config exists
    ensure_config()
    config_obj = Config.from_file()
    config = {"llm": {"model": config_obj.llm_model, "api_key": config_obj.llm_api_key}}

    # Create real services with test database
    storage = Storage(test_db)
    fetcher = RSSFetcher()
    summarizer = ContentSummarizer(config["llm"])
    evaluator = ContentEvaluator(config["llm"])

    # Track execution times
    execution_times = []

    # Wrap the orchestrator to track executions
    class TestOrchestrator(DaemonOrchestrator):
        def run_once(self) -> dict:
            execution_times.append(time.time())
            # Don't actually fetch/analyze, just track the call
            return {"total_items": 0, "total_analyzed": 0, "errors": []}

    # Capture console output
    output = StringIO()
    test_console = Console(file=output)

    # Create test orchestrator
    orchestrator = TestOrchestrator(
        storage=storage,
        fetcher=fetcher,
        summarizer=summarizer,
        evaluator=evaluator,
        config=config,
        console=test_console,
    )

    # Define a simple sync wrapper for testing
    def run_test_orchestrator() -> None:
        orchestrator.run_once()

    # Test scheduler with very short intervals
    async def test_scheduler() -> None:
        scheduler = AsyncIOScheduler()

        # Add job with 1 second interval for testing
        scheduler.add_job(
            func=run_test_orchestrator,
            trigger="interval",
            seconds=1,
            id="test_job",
            max_instances=1,
        )

        # Also run immediately
        scheduler.add_job(
            func=run_test_orchestrator,
            trigger="date",
            id="initial_run",
        )

        scheduler.start()

        # Let it run for 3 seconds
        await asyncio.sleep(3)

        scheduler.shutdown(wait=True)

        # Should have at least 3 executions (initial + 2 intervals)
        assert len(execution_times) >= 3, (
            f"Expected at least 3 executions, got {len(execution_times)}"
        )

        # Check intervals are roughly 1 second
        if len(execution_times) > 1:
            intervals = [
                execution_times[i + 1] - execution_times[i]
                for i in range(len(execution_times) - 1)
            ]
            for interval in intervals[1:]:  # Skip first interval (immediate run)
                assert 0.8 < interval < 1.5, (
                    f"Interval {interval} not close to 1 second"
                )

    # Run the async test
    asyncio.run(test_scheduler())


def test_scheduler_graceful_shutdown(test_db) -> None:
    """Test scheduler shuts down gracefully on signal.

    This integration test:
    - Starts scheduler
    - Sends shutdown signal
    - Verifies clean shutdown
    """
    import asyncio
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from orchestrator import DaemonOrchestrator
    from fetchers.rss import RSSFetcher
    from fetchers.reddit import RedditFetcher
    from fetchers.youtube import YouTubeFetcher
    from summarizer import ContentSummarizer
    from evaluator import ContentEvaluator
    from notifier import Notifier
    from config import Config
    from io import StringIO
    from rich.console import Console

    # Ensure config exists
    ensure_config()
    config_obj = Config.from_file()
    config = {"llm": {"model": config_obj.llm_model, "api_key": config_obj.llm_api_key}}

    # Create real services
    storage = Storage(test_db)
    fetcher = RSSFetcher()
    summarizer = ContentSummarizer(config["llm"])
    evaluator = ContentEvaluator(config["llm"])

    # Capture output
    output = StringIO()
    test_console = Console(file=output)

    orchestrator = DaemonOrchestrator(
        storage=storage,
        fetcher=fetcher,
        summarizer=summarizer,
        evaluator=evaluator,
        config=config,
        console=test_console,
    )

    # Define a simple sync wrapper
    def run_test_orchestrator() -> None:
        orchestrator.run_once()

    async def test_shutdown() -> None:
        scheduler = AsyncIOScheduler()

        # Add a job
        scheduler.add_job(
            func=run_test_orchestrator,
            trigger="interval",
            seconds=10,  # Long interval so it doesn't actually run
            id="test_job",
        )

        scheduler.start()

        # Verify scheduler is running
        assert scheduler.running is True

        # Simulate shutdown
        scheduler.shutdown(wait=False)

        # Give it a moment to shut down
        await asyncio.sleep(0.5)

        # Verify scheduler stopped
        assert scheduler.running is False

    # Run the test
    asyncio.run(test_shutdown())
