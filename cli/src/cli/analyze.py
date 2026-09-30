"""Content analysis and repair commands."""

import time
from typing import Optional

import typer
from rich.console import Console
from rich.prompt import Confirm

from .remote import is_remote_mode

# Heavy imports (litellm, Storage) are lazy-loaded to support client-only installs

console = Console()
app = typer.Typer()  # Sub-typer for analyze commands

# gh #83: classifying one item through KindClassifier costs about this much (Jev,
# OpenRouter's decisions model) -- see the work order's measured 3.4k-items/$0.14.
_KIND_COST_PER_ITEM = 0.00004


def _check_local_mode(command: str) -> None:
    """Check if running in remote mode and exit with guidance."""
    if is_remote_mode():
        console.print(
            f"[yellow]'{command}' requires local daemon access.[/yellow]\n"
            "[dim]Run this command on the server where the daemon is installed.[/dim]"
        )
        raise typer.Exit(1)


@app.command(name="status")
def status() -> None:
    """Show content analysis status and repair statistics."""
    _check_local_mode("analyze status")
    try:
        from prismis_daemon.storage import Storage

        with Storage() as storage:
            # Count items needing analysis
            missing = storage.count_content_without_analysis()

            # Count total content
            cursor = storage.conn.execute(
                "SELECT COUNT(*) FROM content WHERE archived_at IS NULL"
            )
            total = cursor.fetchone()[0]

        if total == 0:
            console.print("[dim]No content in database[/dim]")
            return

        percentage = int((missing / total) * 100) if total > 0 else 0

        console.print("\n[bold]Content Analysis Status:[/bold]")
        if missing == 0:
            console.print("  [green]✓ All items analyzed[/green]\n")
        else:
            console.print(
                f"  Missing analysis: [yellow]{missing}/{total}[/yellow] items ({percentage}%)"
            )

            # Cost estimate
            cost_per_item = 0.02
            estimated_cost = missing * cost_per_item
            console.print(
                f"  Estimated repair cost: [cyan]${estimated_cost:.2f}[/cyan] @ ${cost_per_item}/item\n"
            )

            console.print(
                "[dim]Run 'prismis-cli analyze repair' to fix missing analysis[/dim]"
            )

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="repair")
def repair(
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum items to process"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompts"
    ),
) -> None:
    """Repair content items with missing or incomplete analysis.

    Prompts for confirmation before analyzing each item (costs ~$0.02/item).
    Updates summary, priority, and analysis fields using LLM.
    """
    _check_local_mode("analyze repair")
    try:
        from prismis_daemon.storage import Storage

        with Storage() as storage:
            # Get items needing repair
            items = storage.get_content_without_analysis(limit=limit)

            if not items:
                console.print("[green]✓ No items need repair[/green]")
                return

            total = len(items)
            console.print(f"[bold]Found {total} items needing analysis[/bold]")

            if not force:
                cost_estimate = total * 0.02
                console.print(f"[dim]Estimated cost: ${cost_estimate:.2f}[/dim]\n")

            # Lazy import heavy LLM dependencies (only when actually repairing)
            from prismis_daemon.analysis import (
                build_llm_analysis,
                get_learned_preferences,
            )
            from prismis_daemon.config import Config
            from prismis_daemon.evaluator import ContentEvaluator
            from prismis_daemon.observability import log as obs_log
            from prismis_daemon.summarizer import ContentSummarizer

            # Initialize analysis components
            config = Config.from_file()
            summarizer = ContentSummarizer(config.llm_light_service)
            evaluator = ContentEvaluator(config.llm_light_service)

            # Fetch learned preferences for LLM evaluation (003-light-preference-learning),
            # the same helper and threshold the daemon pipeline uses
            # (orchestrator.run_once): only activates with >=5 votes in the last 30 days.
            learned_preferences = None
            try:
                learned_preferences, total_votes = get_learned_preferences(storage)
                if learned_preferences:
                    console.print(
                        f"[dim]🧠 Using learned preferences from {total_votes} votes (last 30 days)[/dim]"
                    )
            except Exception as e:
                obs_log(
                    "cli.repair.feedback_statistics_failed", source="cli", error=str(e)
                )
                # Continue without learned preferences - not critical

            # Track repair operation start
            start_time = time.time()
            obs_log("cli.repair.start", source="cli", items=total, limit=limit)

            processed = 0
            skipped = 0
            failed = 0

            for idx, item in enumerate(items, 1):
                # Show item details
                console.print(f"\n[bold][{idx}/{total}][/bold] {item['title']}")
                console.print(f"  Source: [cyan]{item['source_name']}[/cyan]")

                # Show current state
                status_parts = []
                if not item["priority"]:
                    status_parts.append("no priority")
                if not item["summary"]:
                    status_parts.append("no summary")
                if not item["analysis"]:
                    status_parts.append("no analysis")
                console.print(f"  Current: [yellow]{', '.join(status_parts)}[/yellow]")

                # Confirm before spending money
                if not force:
                    if not Confirm.ask("  Re-analyze this item?", default=False):
                        skipped += 1
                        continue

                try:
                    # Step 1: Summarize content
                    summary_result = summarizer.summarize_with_analysis(
                        content=item["content"],
                        title=item["title"],
                        url=item["url"],
                        source_type=item.get("source_type", "rss"),
                        source_name=item.get("source_name", ""),
                        metadata={},
                    )

                    if not summary_result:
                        console.print("  [red]✗ Summarization failed[/red]")
                        failed += 1
                        continue

                    # Step 2: Evaluate priority
                    evaluation = evaluator.evaluate_content(
                        content=item["content"],
                        title=item["title"],
                        url=item["url"],
                        context=config.context,
                        learned_preferences=learned_preferences,
                    )

                    # Step 3: Build analysis dict (same daemon-side helper the
                    # pipeline uses -- includes preference_influenced, SC-10,
                    # and title_only, SC-3)
                    analysis = build_llm_analysis(
                        summary_result, evaluation, item["content"]
                    )

                    # Merge with existing analysis (preserve any fetcher metrics)
                    if item.get("analysis"):
                        if "metrics" in item["analysis"]:
                            analysis["metrics"] = item["analysis"]["metrics"]

                    # Step 4: Update atomically
                    item_dict = item.copy()
                    item_dict.update(
                        {
                            "summary": summary_result.summary,
                            "analysis": analysis,
                            "priority": evaluation.priority.value
                            if evaluation.priority
                            else None,
                        }
                    )

                    storage.create_or_update_content(item_dict)

                    # Show result
                    priority_str = (
                        evaluation.priority.value if evaluation.priority else "None"
                    )
                    summary_preview = (
                        summary_result.summary[:60] + "..."
                        if len(summary_result.summary) > 60
                        else summary_result.summary
                    )
                    console.print(
                        f"  [green]✓ Analyzed: priority={priority_str.upper()}, summary={summary_preview}[/green]"
                    )

                    processed += 1

                except Exception as e:
                    console.print(f"  [red]✗ Failed: {e}[/red]")
                    failed += 1

            # Track repair operation complete
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "cli.repair.complete",
                source="cli",
                duration_ms=duration_ms,
                processed=processed,
                skipped=skipped,
                failed=failed,
                total=total,
            )

            # Summary
            console.print("\n[bold]Repair Complete[/bold]")
            console.print(f"  ✓ Repaired: [green]{processed}[/green] items")
            if skipped > 0:
                console.print(f"  ⊙ Skipped: [yellow]{skipped}[/yellow] items")
            if failed > 0:
                console.print(f"  ✗ Failed: [red]{failed}[/red] items")

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="kinds")
def kinds(
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum items to classify"),
    since_days: Optional[int] = typer.Option(
        None, "--since-days", help="Only classify items fetched in the last N days"
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Skip confirmation prompt"
    ),
) -> None:
    """Backfill the content kind for already-analysed items that have none yet.

    Selects items with a summary and no kind_confidence in their stored analysis
    (newest first, at most --limit, optionally narrowed to the last --since-days
    days), and classifies each through KindClassifier (~$0.00004/item, gh #83). An
    item that already carries kind_confidence -- classified or unclassified -- is
    never reselected; a later run retries only items a failed call left untouched.
    """
    _check_local_mode("analyze kinds")

    from prismis_daemon.config import Config

    config = Config.from_file()
    if not config.llm_kind_service:
        console.print(
            "[yellow]'analyze kinds' requires a kind_service configured under "
            "[llm] in config.toml.[/yellow]\n"
            "[dim]Set kind_service to a service name from "
            "~/.config/llm-core/services.toml.[/dim]"
        )
        raise typer.Exit(1)

    try:
        from prismis_daemon.kind_classifier import KindClassifier
        from prismis_daemon.observability import log as obs_log
        from prismis_daemon.storage import Storage

        with Storage() as storage:
            items = storage.get_content_needing_kind(limit=limit, since_days=since_days)

            if not items:
                console.print("[green]✓ No items need kind classification[/green]")
                return

            total = len(items)
            console.print(f"[bold]Found {total} items needing kind classification[/bold]")

            if not force:
                cost_estimate = total * _KIND_COST_PER_ITEM
                console.print(f"[dim]Estimated cost: ${cost_estimate:.4f}[/dim]\n")
                if not Confirm.ask(f"Classify {total} items?", default=False):
                    console.print("[yellow]Aborted[/yellow]")
                    return

            classifier = KindClassifier(config.llm_kind_service)

            start_time = time.time()
            obs_log("cli.kinds.start", source="cli", items=total, limit=limit)

            classified = 0
            unclassified = 0
            failed = 0

            for idx, item in enumerate(items, 1):
                console.print(f"\n[bold][{idx}/{total}][/bold] {item['title']}")
                analysis = item.get("analysis") or {}

                try:
                    result = classifier.classify(
                        title=item["title"],
                        source_type=item.get("source_type") or "rss",
                        source_name=item.get("source_name") or "",
                        summary=item.get("summary") or "",
                        reading_summary=analysis.get("reading_summary") or "",
                        raw_content=item.get("content") or "",
                    )
                except Exception as e:
                    console.print(f"  [red]✗ Failed: {e}[/red]")
                    failed += 1
                    continue

                updated_analysis = dict(analysis)
                updated_analysis["kind"] = result.kind
                updated_analysis["kind_confidence"] = result.confidence
                storage.update_analysis(item["id"], updated_analysis)

                if result.kind is not None:
                    console.print(
                        f"  [green]✓ {result.kind} ({result.confidence:.2f})[/green]"
                    )
                    classified += 1
                else:
                    console.print("  [dim]⊙ unclassified[/dim]")
                    unclassified += 1

            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "cli.kinds.complete",
                source="cli",
                duration_ms=duration_ms,
                classified=classified,
                unclassified=unclassified,
                failed=failed,
                total=total,
            )

            console.print("\n[bold]Kind Classification Complete[/bold]")
            console.print(f"  ✓ Classified: [green]{classified}[/green] items")
            console.print(f"  ⊙ Unclassified: [yellow]{unclassified}[/yellow] items")
            if failed > 0:
                console.print(f"  ✗ Failed: [red]{failed}[/red] items")

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e
