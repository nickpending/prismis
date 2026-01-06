"""Content analysis and repair commands."""

import time

import typer
from prismis_daemon.storage import Storage
from rich.console import Console
from rich.prompt import Confirm

# Heavy imports (litellm) are lazy-loaded in repair() to keep CLI fast

console = Console()
app = typer.Typer()  # Sub-typer for analyze commands


@app.command(name="status")
def status() -> None:
    """Show content analysis status and repair statistics."""
    try:
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
    try:
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
            from prismis_daemon.config import Config
            from prismis_daemon.evaluator import ContentEvaluator
            from prismis_daemon.observability import log as obs_log
            from prismis_daemon.summarizer import ContentSummarizer

            # Initialize analysis components
            config = Config()
            summarizer = ContentSummarizer()
            evaluator = ContentEvaluator(config)

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
                    )

                    # Step 3: Build analysis dict
                    analysis = {
                        "reading_summary": summary_result.reading_summary,
                        "alpha_insights": summary_result.alpha_insights,
                        "patterns": summary_result.patterns,
                        "entities": summary_result.entities,
                        "quotes": summary_result.quotes,
                        "tools": summary_result.tools,
                        "urls": summary_result.urls,
                        "matched_interests": evaluation.matched_interests,
                        "priority_reasoning": evaluation.reasoning,
                        "metadata": summary_result.metadata,
                    }

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
