"""Main entry point for Prismis daemon - just wiring, no logic."""

import asyncio
import os
import signal
import sys
from pathlib import Path

import typer
import uvicorn
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv
from rich.console import Console

from .config import Config
from .storage import Storage
from .fetchers.rss import RSSFetcher
from .fetchers.reddit import RedditFetcher
from .fetchers.youtube import YouTubeFetcher
from .summarizer import ContentSummarizer
from .evaluator import ContentEvaluator
from .notifier import Notifier
from .defaults import ensure_config
from .orchestrator import DaemonOrchestrator

# Load environment variables from ~/.config/prismis/.env
config_home = os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
dotenv_path = Path(config_home) / "prismis" / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)

console = Console()
scheduler = None  # Global for signal handler
api_server = None  # Global for API server


async def run_scheduler(config: Config, test_mode: bool = False) -> None:
    """Run the daemon with APScheduler for periodic fetching.

    Args:
        config: Already loaded and validated configuration
        test_mode: Whether to run in test mode with 5 second intervals
    """
    global scheduler

    try:
        # Initialize components
        console.print("🔧 Initializing components...")
        storage = Storage()

        # Create all fetchers with config
        rss_fetcher = RSSFetcher(config=config)
        reddit_fetcher = RedditFetcher(config=config)
        youtube_fetcher = YouTubeFetcher(config=config)

        # Create LLM config dict for summarizer and evaluator
        llm_config = {
            "provider": config.llm_provider,
            "model": config.llm_model,
            "api_key": config.llm_api_key,
        }
        # Add api_base if configured (for Ollama)
        if config.llm_api_base:
            llm_config["api_base"] = config.llm_api_base
        notification_config = {
            "high_priority_only": config.high_priority_only,
            "command": config.notification_command,
        }

        summarizer = ContentSummarizer(llm_config)
        evaluator = ContentEvaluator(llm_config)
        notifier = Notifier(notification_config)

        # Create orchestrator with all dependencies
        orchestrator = DaemonOrchestrator(
            storage=storage,
            rss_fetcher=rss_fetcher,
            reddit_fetcher=reddit_fetcher,
            youtube_fetcher=youtube_fetcher,
            summarizer=summarizer,
            evaluator=evaluator,
            notifier=notifier,
            config=config,
            console=console,
        )

        # Create scheduler
        scheduler = AsyncIOScheduler()

        # Determine interval based on mode
        if test_mode:
            interval_trigger = IntervalTrigger(seconds=5)
            interval_msg = "5 seconds"
        else:
            interval_trigger = IntervalTrigger(minutes=30)
            interval_msg = "30 minutes"

        # Add job to run periodically
        scheduler.add_job(
            func=run_orchestrator_sync,
            args=(orchestrator,),
            trigger=interval_trigger,
            id="fetch_and_analyze",
            name="Fetch and analyze content",
            replace_existing=True,
            max_instances=1,  # Prevent overlapping runs
        )

        # Also run immediately on startup
        scheduler.add_job(
            func=run_orchestrator_sync,
            args=(orchestrator,),
            trigger="date",  # Run once immediately
            id="initial_run",
            name="Initial fetch on startup",
        )

        # Add archival policy job (runs every 6 hours)
        scheduler.add_job(
            func=run_archival_job_sync,
            args=(orchestrator,),
            trigger=IntervalTrigger(hours=6),
            id="archival_policy",
            name="Archive old content",
            replace_existing=True,
            max_instances=1,
        )

        # Setup signal handlers for graceful shutdown
        def signal_handler(sig, frame) -> None:
            console.print(
                "\n[yellow]Received shutdown signal, stopping scheduler...[/yellow]"
            )
            if scheduler and scheduler.running:
                scheduler.shutdown(wait=False)
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Start scheduler
        scheduler.start()
        console.print(
            f"[green]✅ Scheduler started - will fetch content every {interval_msg}[/green]"
        )

        # Start API server
        console.print("[yellow]🌐 Starting API server on port 8989...[/yellow]")
        from .api import app

        # Create API server config
        api_config = uvicorn.Config(
            app,
            host=config.api_host,
            port=8989,
            log_level="warning",  # Reduce noise
            access_log=False,  # Disable access logs for cleaner output
        )
        api_server = uvicorn.Server(api_config)

        # Run API server in background
        asyncio.create_task(api_server.serve())
        console.print(
            f"[green]✅ API server running on http://{config.api_host}:8989[/green]"
        )
        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        # Keep the event loop running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            pass

    except Exception as e:
        console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
        sys.exit(1)
    finally:
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=True)


def run_orchestrator_sync(orchestrator: DaemonOrchestrator) -> None:
    """Synchronous wrapper to run orchestrator for scheduler."""
    from datetime import datetime

    console.print(
        f"\n[blue]⏰ Running scheduled fetch at {datetime.now().strftime('%H:%M:%S')}[/blue]"
    )
    stats = orchestrator.run_once()
    if stats["total_items"] > 0:
        console.print(
            f"[green]Completed: {stats['total_analyzed']} items analyzed[/green]\n"
        )
    else:
        console.print("[dim]No new items found[/dim]\n")


def run_archival_job_sync(orchestrator: DaemonOrchestrator) -> None:
    """Synchronous wrapper for archival job."""
    from datetime import datetime

    console.print(
        f"\n[blue]📦 Running archival policy at {datetime.now().strftime('%H:%M:%S')}[/blue]"
    )
    stats = orchestrator.run_archival_policy()
    if stats["archived_count"] > 0:
        console.print(f"[green]Archived {stats['archived_count']} items[/green]\n")


app = typer.Typer()


def validate_llm_config(config: Config) -> None:
    """Validate LLM configuration at startup.

    Args:
        config: Loaded configuration

    Raises:
        SystemExit: If LLM configuration is invalid
    """
    console.print("🔌 Validating LLM configuration...")
    from .llm_validator import validate_llm_config

    test_config = {
        "provider": config.llm_provider,
        "model": config.llm_model,
        "api_key": config.llm_api_key,
    }
    if config.llm_api_base:
        test_config["api_base"] = config.llm_api_base

    try:
        # Validate config and test connection
        console.print("🧪 Testing LLM connection...")
        validate_llm_config(test_config)

        console.print(
            f"[green]✅ LLM connection successful: {config.llm_provider} / {config.llm_model}[/green]"
        )
    except ValueError as e:
        console.print(f"[bold red]❌ LLM configuration error: {e}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]❌ LLM connection failed: {e}[/bold red]")
        console.print(
            "[yellow]💡 Check your model name format and server availability[/yellow]"
        )
        sys.exit(1)


@app.command()
def main(
    once: bool = typer.Option(False, "--once", help="Run once and exit"),
    test_mode: bool = typer.Option(
        False, "--test", help="Test mode with 5 second intervals"
    ),
) -> None:
    """Main entry point for Prismis daemon."""
    # Common setup for both modes
    try:
        # Ensure config files exist
        ensure_config()

        # Load configuration
        console.print("📂 Loading configuration...")
        config = Config.from_file()

        # Validate LLM configuration at startup (before any mode-specific code)
        validate_llm_config(config)
    except Exception as e:
        console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
        sys.exit(1)

    if once:
        console.print("[bold blue]Starting Prismis daemon (--once mode)[/bold blue]")

        try:
            # Initialize components
            console.print("🔧 Initializing components...")
            storage = Storage()

            # Create all fetchers with config
            rss_fetcher = RSSFetcher(config=config)
            reddit_fetcher = RedditFetcher(config=config)
            youtube_fetcher = YouTubeFetcher(config=config)

            # Create LLM config dict for summarizer and evaluator
            llm_config = {
                "provider": config.llm_provider,
                "model": config.llm_model,
                "api_key": config.llm_api_key,
            }
            # Add api_base if configured (for Ollama)
            if config.llm_api_base:
                llm_config["api_base"] = config.llm_api_base
            notification_config = {
                "high_priority_only": config.high_priority_only,
                "command": config.notification_command,
            }

            summarizer = ContentSummarizer(llm_config)
            evaluator = ContentEvaluator(llm_config)
            notifier = Notifier(notification_config)

            # Create and run orchestrator with all dependencies
            orchestrator = DaemonOrchestrator(
                storage=storage,
                rss_fetcher=rss_fetcher,
                reddit_fetcher=reddit_fetcher,
                youtube_fetcher=youtube_fetcher,
                summarizer=summarizer,
                evaluator=evaluator,
                notifier=notifier,
                config=config,
                console=console,
            )

            stats = orchestrator.run_once()

            # Exit with error if there were problems
            if stats["errors"] and stats["total_analyzed"] == 0:
                sys.exit(1)

        except Exception as e:
            console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
            sys.exit(1)
    else:
        mode = "test mode (5 second intervals)" if test_mode else "scheduler mode"
        console.print(f"[bold blue]Starting Prismis daemon ({mode})[/bold blue]")

        # Run the scheduler with already validated config
        asyncio.run(run_scheduler(config, test_mode=test_mode))


if __name__ == "__main__":
    app()
