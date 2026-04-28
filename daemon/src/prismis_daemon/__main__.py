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
from .context_auto_updater import run_context_update
from .defaults import ensure_config
from .evaluator import ContentEvaluator
from .fetchers.file import FileFetcher
from .fetchers.reddit import RedditFetcher
from .fetchers.rss import RSSFetcher
from .fetchers.youtube import YouTubeFetcher
from .locking import acquire_daemon_lock
from .notifier import Notifier
from .observability import get_logger as get_obs_logger
from .orchestrator import DaemonOrchestrator
from .storage import Storage
from .summarizer import ContentSummarizer

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
        file_fetcher = FileFetcher(config=config, storage=storage)

        notification_config = {
            "high_priority_only": config.high_priority_only,
            "command": config.notification_command,
        }

        summarizer = ContentSummarizer(config.llm_light_service)
        evaluator = ContentEvaluator(config.llm_light_service)
        notifier = Notifier(notification_config)

        # Optional deep extractor — only when llm_deep_service is configured.
        deep_extractor = None
        if config.llm_deep_service:
            from .deep_extractor import ContentDeepExtractor

            deep_extractor = ContentDeepExtractor(config.llm_deep_service)

        # Create orchestrator with all dependencies
        orchestrator = DaemonOrchestrator(
            storage=storage,
            rss_fetcher=rss_fetcher,
            reddit_fetcher=reddit_fetcher,
            youtube_fetcher=youtube_fetcher,
            file_fetcher=file_fetcher,
            summarizer=summarizer,
            evaluator=evaluator,
            notifier=notifier,
            config=config,
            console=console,
            deep_extractor=deep_extractor,
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

        # Add embedding backfill job (runs every 6 hours to catch stragglers)
        scheduler.add_job(
            func=run_embedding_backfill_sync,
            args=(orchestrator,),
            trigger=IntervalTrigger(hours=6),
            id="embedding_backfill",
            name="Backfill missing embeddings",
            replace_existing=True,
            max_instances=1,
        )

        # Add observability cleanup job (runs daily to remove old JSONL files)
        scheduler.add_job(
            func=run_observability_cleanup_sync,
            trigger=IntervalTrigger(days=1),
            id="observability_cleanup",
            name="Cleanup old observability logs",
            replace_existing=True,
            max_instances=1,
        )

        # Add context auto-update job (runs daily, checks internally if update is needed)
        if config.context_auto_update_enabled:
            scheduler.add_job(
                func=run_context_update_sync,
                args=(config, storage),
                trigger=IntervalTrigger(
                    days=1
                ),  # Check daily, actual update based on config interval
                id="context_auto_update",
                name="Auto-update context.md from feedback",
                replace_existing=True,
                max_instances=1,
            )
            console.print(
                f"[dim]Context auto-update enabled (every {config.context_auto_update_interval_days} days, min {config.context_auto_update_min_votes} votes)[/dim]"
            )

        # Setup async-aware signal handlers for graceful shutdown
        shutdown_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def request_shutdown():
            if not shutdown_event.is_set():
                console.print(
                    "\n[yellow]Received shutdown signal, stopping gracefully...[/yellow]"
                )
                shutdown_event.set()

        loop.add_signal_handler(signal.SIGINT, request_shutdown)
        loop.add_signal_handler(signal.SIGTERM, request_shutdown)

        # Start scheduler
        scheduler.start()
        console.print(
            f"[green]✅ Scheduler started - will fetch content every {interval_msg}[/green]"
        )

        # Start API server
        console.print("[yellow]🌐 Starting API server on port 8989...[/yellow]")
        from .api import app

        # Expose deep extractor to API endpoints (None when not configured;
        # the endpoint returns 503 in that case).
        app.state.deep_extractor = deep_extractor

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

        # Wait for shutdown signal
        await shutdown_event.wait()

        # Graceful shutdown sequence
        console.print("[yellow]Stopping scheduler...[/yellow]")
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=True)
        console.print("[yellow]Stopping API server...[/yellow]")
        api_server.should_exit = True
        # Give API server a moment to finish in-flight requests
        await asyncio.sleep(0.5)
        console.print("[green]✅ Shutdown complete[/green]")

    except Exception as e:
        console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
        sys.exit(1)
    finally:
        # Ensure scheduler is stopped even on error path
        if scheduler and scheduler.running:
            scheduler.shutdown(wait=False)


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


def run_embedding_backfill_sync(orchestrator: DaemonOrchestrator) -> None:
    """Synchronous wrapper for embedding backfill job."""
    from datetime import datetime

    console.print(
        f"\n[blue]🔗 Running embedding backfill at {datetime.now().strftime('%H:%M:%S')}[/blue]"
    )
    stats = orchestrator.backfill_embeddings(limit=50)
    if stats["processed"] > 0:
        console.print(
            f"[green]Indexed {stats['processed']} stragglers ({stats['failed']} failed)[/green]\n"
        )


def run_observability_cleanup_sync() -> None:
    """Synchronous wrapper for observability cleanup job."""
    from datetime import datetime

    console.print(
        f"\n[blue]🗑️  Running observability cleanup at {datetime.now().strftime('%H:%M:%S')}[/blue]"
    )
    obs_logger = get_obs_logger()
    removed = obs_logger.cleanup_old_files(retention_days=30)
    if removed > 0:
        console.print(f"[green]Removed {removed} old observability files[/green]\n")
    else:
        console.print("[dim]No old files to remove[/dim]\n")


def run_context_update_sync(config: Config, storage: Storage) -> None:
    """Synchronous wrapper for context auto-update job."""
    from datetime import datetime

    console.print(
        f"\n[blue]📝 Checking context auto-update at {datetime.now().strftime('%H:%M:%S')}[/blue]"
    )

    run_context_update(config, storage)


app = typer.Typer(invoke_without_command=True)


def validate_llm_config(config: Config) -> None:
    """Validate LLM configuration at startup.

    Args:
        config: Loaded configuration

    Raises:
        SystemExit: If LLM configuration is invalid
    """
    console.print("🔌 Validating LLM configuration...")
    from .llm_validator import validate_llm_services as _validate_llm_services

    try:
        console.print("🧪 Testing LLM service connections...")
        result = _validate_llm_services(
            config.llm_light_service, config.llm_deep_service
        )
        console.print(
            f"[green]✅ Light service: {config.llm_light_service} ({result['light']})[/green]"
        )
        if result["deep"] == "ok":
            console.print(
                f"[green]✅ Deep service: {config.llm_deep_service} ({result['deep']})[/green]"
            )
        elif result["deep"] == "unreachable":
            console.print(
                f"[yellow]⚠️  Deep service: {config.llm_deep_service} unreachable — deep extraction disabled[/yellow]"
            )
        else:
            console.print("[dim]Deep service: not configured[/dim]")
    except ValueError as e:
        console.print(f"[bold red]❌ LLM configuration error: {e}[/bold red]")
        sys.exit(1)
    except Exception as e:
        console.print(f"[bold red]❌ LLM connection failed: {e}[/bold red]")
        console.print(
            "[yellow]💡 Check your service configuration in ~/.config/llm-core/services.toml[/yellow]"
        )
        sys.exit(1)


@app.callback()
def main(
    ctx: typer.Context,
    once: bool = typer.Option(False, "--once", help="Run once and exit"),
    test_mode: bool = typer.Option(
        False, "--test", help="Test mode with 5 second intervals"
    ),
) -> None:
    """Prismis content daemon."""
    # Admin subcommands (e.g., migrate-config) must run on stale configs.
    # Skip the daemon lock + config validation when a subcommand is dispatched;
    # typer will invoke the subcommand next.
    if ctx.invoked_subcommand is not None:
        return

    # Acquire daemon lock first - fail fast if another instance running
    with acquire_daemon_lock():
        # Common setup for both modes
        try:
            # Ensure config files exist - exits if new config created
            if not ensure_config():
                sys.exit(0)

            # Load configuration
            console.print("📂 Loading configuration...")
            config = Config.from_file()

            # Validate LLM configuration at startup (before any mode-specific code)
            validate_llm_config(config)
        except Exception as e:
            console.print(f"[bold red]❌ Fatal error: {e}[/bold red]")
            sys.exit(1)
        if once:
            console.print(
                "[bold blue]Starting Prismis daemon (--once mode)[/bold blue]"
            )

            try:
                # Initialize components
                console.print("🔧 Initializing components...")
                storage = Storage()

                # Create all fetchers with config
                rss_fetcher = RSSFetcher(config=config)
                reddit_fetcher = RedditFetcher(config=config)
                youtube_fetcher = YouTubeFetcher(config=config)
                file_fetcher = FileFetcher(config=config, storage=storage)

                notification_config = {
                    "high_priority_only": config.high_priority_only,
                    "command": config.notification_command,
                }

                summarizer = ContentSummarizer(config.llm_light_service)
                evaluator = ContentEvaluator(config.llm_light_service)
                notifier = Notifier(notification_config)

                # Optional deep extractor — only when llm_deep_service is configured.
                deep_extractor = None
                if config.llm_deep_service:
                    from .deep_extractor import ContentDeepExtractor

                    deep_extractor = ContentDeepExtractor(config.llm_deep_service)

                # Create and run orchestrator with all dependencies
                orchestrator = DaemonOrchestrator(
                    storage=storage,
                    rss_fetcher=rss_fetcher,
                    reddit_fetcher=reddit_fetcher,
                    youtube_fetcher=youtube_fetcher,
                    file_fetcher=file_fetcher,
                    summarizer=summarizer,
                    evaluator=evaluator,
                    notifier=notifier,
                    config=config,
                    console=console,
                    deep_extractor=deep_extractor,
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


@app.command()
def migrate_config() -> None:
    """Migrate existing Prismis config to use llm-core/apiconf stack."""
    import re
    import tomllib

    config_home = os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    prismis_config_path = Path(config_home) / "prismis" / "config.toml"

    # Step 1: Read existing prismis config
    if not prismis_config_path.exists():
        console.print(
            f"[bold red]Config file not found: {prismis_config_path}[/bold red]"
        )
        sys.exit(1)

    config_text = prismis_config_path.read_text()
    with open(prismis_config_path, "rb") as f:
        config_dict = tomllib.load(f)

    llm = config_dict.get("llm", {})

    # Already fully migrated to dual-service format
    if "light_service" in llm:
        console.print(
            "[green]Config already migrated to dual-service format. Nothing to do.[/green]"
        )
        return

    # Post-llm-core install: has 'service' but not yet renamed to 'light_service'
    if "service" in llm and "provider" not in llm:
        # Rename `service` → `light_service` within [llm] block only.
        # Extract [llm] section, substitute inside it, reassemble — mirrors the
        # existing [llm]-section rewrite pattern further below.
        llm_section_pattern = r"(\[llm\].*?)(?=\n\[|\Z)"
        match = re.search(llm_section_pattern, config_text, flags=re.DOTALL)
        if not match:
            console.print(
                "[bold red]Could not locate [llm] section in config.toml[/bold red]"
            )
            sys.exit(1)
        llm_block = match.group(1)
        new_llm_block = re.sub(
            r"(?m)^service(\s*=)",
            r"light_service\1",
            llm_block,
        )
        new_config_text = (
            config_text[: match.start(1)] + new_llm_block + config_text[match.end(1) :]
        )

        # Append [services.prismis-openai-deep] to services.toml (idempotent)
        services_path = Path(config_home) / "llm-core" / "services.toml"
        if services_path.exists():
            services_text = services_path.read_text()
            if "[services.prismis-openai-deep]" in services_text:
                console.print(
                    "[dim]Skipping prismis-openai-deep entry (already exists)[/dim]"
                )
            else:
                if not services_text.endswith("\n"):
                    services_text += "\n"
                services_text += (
                    "\n[services.prismis-openai-deep]\n"
                    'adapter = "openai"\n'
                    'key = "sable-openai"\n'
                    'base_url = "https://api.openai.com/v1"\n'
                    'default_model = "gpt-5-mini"\n'
                )
                services_path.write_text(services_text)
                console.print(
                    f"[green]Added [services.prismis-openai-deep] to {services_path}[/green]"
                )
        else:
            console.print(
                f"[yellow]services.toml not found at {services_path}. "
                f"Run migrate-config from a fresh install first.[/yellow]"
            )

        # Atomic write for config.toml rename
        tmp_path = prismis_config_path.with_suffix(".toml.tmp")
        tmp_path.write_text(new_config_text)
        tmp_path.rename(prismis_config_path)
        console.print(
            f"[green]Updated {prismis_config_path}: service → light_service[/green]"
        )
        console.print(
            "\n[bold green]Migration complete. Run 'prismis-daemon' to start.[/bold green]"
        )
        return

    # Pre-llm-core install: has 'provider' field (existing path below)
    if "provider" not in llm:
        console.print(
            "[yellow]No [llm] provider field found. Cannot determine migration path.[/yellow]"
        )
        sys.exit(1)

    old_provider = llm.get("provider", "openai")
    old_model = llm.get("model", "gpt-4.1-mini")
    old_api_key = llm.get("api_key", "")

    # Map old provider names to adapter + base_url
    provider_map = {
        "openai": ("openai", "openai", "https://api.openai.com/v1"),
        "anthropic": ("anthropic", "anthropic", "https://api.anthropic.com"),
        "ollama": ("ollama", "ollama", "http://localhost:11434"),
    }
    adapter, key_name, base_url = provider_map.get(
        old_provider, ("openai", "openai", "https://api.openai.com/v1")
    )
    service_name = f"prismis-{old_provider}"

    console.print(f"Found old config format: provider={old_provider}")

    # Step 2: Create ~/.config/llm-core/services.toml
    llm_core_dir = Path(config_home) / "llm-core"
    llm_core_dir.mkdir(parents=True, exist_ok=True)

    services_path = llm_core_dir / "services.toml"
    if services_path.exists():
        console.print(f"[dim]Skipping {services_path} (already exists)[/dim]")
    else:
        services_content = f"""\
default_service = "{service_name}"

[services.{service_name}]
adapter = "{adapter}"
key = "{key_name}"
base_url = "{base_url}"
default_model = "{old_model}"
"""
        services_path.write_text(services_content)
        console.print(f"[green]Created {services_path}[/green]")

    # Step 3: Resolve API key and write to apiconf
    apiconf_dir = Path(config_home) / "apiconf"
    apiconf_dir.mkdir(parents=True, exist_ok=True)
    apiconf_path = apiconf_dir / "config.toml"

    resolved_key = old_api_key
    key_warning = None
    if old_api_key.startswith("env:"):
        env_var = old_api_key[4:]
        resolved_key = os.environ.get(env_var, "")
        if not resolved_key:
            key_warning = f"Environment variable {env_var} not set. You will need to manually set [keys.{key_name}] value in {apiconf_path}"
            resolved_key = old_api_key  # Write the unexpanded string

    if apiconf_path.exists():
        # Check if [keys.openai] already exists
        with open(apiconf_path, "rb") as f:
            apiconf_dict = tomllib.load(f)

        if "keys" in apiconf_dict and key_name in apiconf_dict["keys"]:
            console.print(
                f"[dim]Skipping {apiconf_path} ([keys.{key_name}] already exists)[/dim]"
            )
        else:
            # Append [keys.{key_name}] section
            apiconf_text = apiconf_path.read_text()
            if not apiconf_text.endswith("\n"):
                apiconf_text += "\n"
            apiconf_text += f'\n[keys.{key_name}]\nvalue = "{resolved_key}"\n'
            apiconf_path.write_text(apiconf_text)
            console.print(f"[green]Added [keys.{key_name}] to {apiconf_path}[/green]")
    else:
        apiconf_content = f"""\
[keys.{key_name}]
value = "{resolved_key}"
"""
        apiconf_path.write_text(apiconf_content)
        console.print(f"[green]Created {apiconf_path}[/green]")

    if key_warning:
        console.print(f"[yellow]Warning: {key_warning}[/yellow]")

    # Step 4: Write pricing.toml if not exists
    pricing_path = llm_core_dir / "pricing.toml"
    if pricing_path.exists():
        console.print(f"[dim]Skipping {pricing_path} (already exists)[/dim]")
    else:
        try:
            from llm_core import update_pricing

            count = update_pricing()
            console.print(f"[green]Created {pricing_path} ({count} models)[/green]")
        except Exception as e:
            console.print(
                f"[yellow]Warning: Could not fetch pricing data: {e}[/yellow]"
            )
            console.print(
                "[yellow]Run 'python -c \"from llm_core import update_pricing; update_pricing()\"' later to populate pricing.[/yellow]"
            )

    # Step 4b: Append [services.prismis-openai-deep] to services.toml (idempotent).
    # Mirrors the post-llm-core branch's check-before-append pattern (lines ~479-505)
    # so a single run of migrate-config on a pre-llm-core config converges to the
    # full dual-service shape — no intermediate unloadable state between runs.
    services_text = services_path.read_text()
    if "[services.prismis-openai-deep]" in services_text:
        console.print("[dim]Skipping prismis-openai-deep entry (already exists)[/dim]")
    else:
        if not services_text.endswith("\n"):
            services_text += "\n"
        services_text += (
            "\n[services.prismis-openai-deep]\n"
            'adapter = "openai"\n'
            'key = "sable-openai"\n'
            'base_url = "https://api.openai.com/v1"\n'
            'default_model = "gpt-5-mini"\n'
        )
        services_path.write_text(services_text)
        console.print(
            f"[green]Added [services.prismis-openai-deep] to {services_path}[/green]"
        )

    # Step 5: Update prismis config.toml [llm] section
    # Converge to the dual-service shape in a single run: write light_service,
    # not service, so Config.from_file() can load the result immediately.
    new_llm_section = f'[llm]\nlight_service = "{service_name}"'

    # Match the [llm] section up to the next section header or end of file
    pattern = r"\[llm\].*?(?=\n\[|\Z)"
    new_config_text = re.sub(pattern, new_llm_section, config_text, flags=re.DOTALL)

    # Write atomically via temp file
    tmp_path = prismis_config_path.with_suffix(".toml.tmp")
    tmp_path.write_text(new_config_text)
    tmp_path.rename(prismis_config_path)
    console.print(f"[green]Updated {prismis_config_path} [llm] section[/green]")

    console.print(
        "\n[bold green]Migration complete. Run 'prismis-daemon' to start.[/bold green]"
    )


if __name__ == "__main__":
    app()
