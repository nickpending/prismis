"""Content analysis and repair commands."""

import logging
import time
from typing import Optional

import typer
from rich.console import Console
from rich.markup import escape
from rich.prompt import Confirm

from .remote import is_remote_mode

# Heavy imports (litellm, Storage) are lazy-loaded to support client-only installs

logger = logging.getLogger(__name__)
console = Console()
app = typer.Typer()  # Sub-typer for analyze commands

# gh #83: classifying one item through KindClassifier costs about this much (Jev,
# OpenRouter's decisions model) -- see the work order's measured 3.4k-items/$0.14.
_KIND_COST_PER_ITEM = 0.00004


def _check_local_mode(command: str) -> None:
    """Check if running in remote mode and exit with guidance."""
    if is_remote_mode():
        console.print(
            f"[yellow]'{escape(str(command))}' requires local daemon access.[/yellow]\n"
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
                f"  Missing analysis: [yellow]{escape(str(missing))}/{escape(str(total))}[/yellow] items ({escape(str(percentage))}%)"
            )

            # Cost estimate
            cost_per_item = 0.02
            estimated_cost = missing * cost_per_item
            console.print(
                f"  Estimated repair cost: [cyan]${escape(f'{estimated_cost:.2f}')}[/cyan] @ ${escape(str(cost_per_item))}/item\n"
            )

            console.print(
                "[dim]Run 'prismis-cli analyze repair' to fix missing analysis[/dim]"
            )

    except Exception as e:
        console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
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
            console.print(
                f"[bold]Found {escape(str(total))} items needing analysis[/bold]"
            )

            if not force:
                cost_estimate = total * 0.02
                console.print(
                    f"[dim]Estimated cost: ${escape(f'{cost_estimate:.2f}')}[/dim]\n"
                )

            # Lazy import heavy LLM dependencies (only when actually repairing)
            from prismis_daemon.analysis import build_llm_analysis, content_basis
            from prismis_daemon.config import Config
            from prismis_daemon.evaluator import ContentEvaluator
            from prismis_daemon.observability import log as obs_log
            from prismis_daemon.summarizer import ContentSummarizer

            # Initialize analysis components
            config = Config.from_file()
            summarizer = ContentSummarizer(config.llm_light_service)
            evaluator = ContentEvaluator(config.llm_light_service)

            # Track repair operation start
            start_time = time.time()
            obs_log("cli.repair.start", source="cli", items=total, limit=limit)

            processed = 0
            skipped = 0
            failed = 0

            for idx, item in enumerate(items, 1):
                # Show item details
                console.print(
                    f"\n[bold][{escape(str(idx))}/{escape(str(total))}][/bold] {escape(str(item['title']))}"
                )
                console.print(
                    f"  Source: [cyan]{escape(str(item['source_name']))}[/cyan]"
                )

                # Show current state
                status_parts = []
                if not item["priority"]:
                    status_parts.append("no priority")
                if not item["summary"]:
                    status_parts.append("no summary")
                if not item["analysis"]:
                    status_parts.append("no analysis")
                console.print(
                    f"  Current: [yellow]{escape(str(', '.join(status_parts)))}[/yellow]"
                )

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
                        content_basis=content_basis(item["content"]),
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

                    # Step 3: Build analysis dict (same daemon-side helper the
                    # pipeline uses -- includes title_only, SC-3)
                    stored_fetch_outcome = (item.get("analysis") or {}).get(
                        "fetch_outcome"
                    )
                    analysis = build_llm_analysis(
                        summary_result,
                        evaluation,
                        item["content"],
                        stored_fetch_outcome,
                    )

                    # Merge with existing analysis (preserve any fetcher metrics)
                    if item.get("analysis"):
                        if "metrics" in item["analysis"]:
                            analysis["metrics"] = item["analysis"]["metrics"]
                        if stored_fetch_outcome:
                            analysis["fetch_outcome"] = stored_fetch_outcome

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
                        f"  [green]✓ Analyzed: priority={escape(str(priority_str.upper()))}, summary={escape(str(summary_preview))}[/green]"
                    )

                    processed += 1

                except Exception as e:
                    # Per-item boundary: one item's failure never stops the repair.
                    logger.warning("Repair failed for %s", item["title"], exc_info=True)
                    console.print(f"  [red]✗ Failed: {escape(str(e))}[/red]")
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
            console.print(
                f"  ✓ Repaired: [green]{escape(str(processed))}[/green] items"
            )
            if skipped > 0:
                console.print(
                    f"  ⊙ Skipped: [yellow]{escape(str(skipped))}[/yellow] items"
                )
            if failed > 0:
                console.print(f"  ✗ Failed: [red]{escape(str(failed))}[/red] items")

    except Exception as e:
        console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
        raise typer.Exit(1) from e


@app.command(name="kinds")
def kinds(
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum items to classify"),
    since_days: Optional[int] = typer.Option(
        None, "--since-days", help="Only classify items fetched in the last N days"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
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
            "[dim]Set kind_service to a service defined in the services tables of "
            "config.toml.[/dim]"
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
            console.print(
                f"[bold]Found {escape(str(total))} items needing kind classification[/bold]"
            )

            if not force:
                cost_estimate = total * _KIND_COST_PER_ITEM
                console.print(
                    f"[dim]Estimated cost: ${escape(f'{cost_estimate:.4f}')}[/dim]\n"
                )
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
                console.print(
                    f"\n[bold][{escape(str(idx))}/{escape(str(total))}][/bold] {escape(str(item['title']))}"
                )
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
                    # Per-item boundary: one item's failure never stops the run.
                    logger.warning(
                        "Kind classification failed for %s",
                        item["title"],
                        exc_info=True,
                    )
                    console.print(f"  [red]✗ Failed: {escape(str(e))}[/red]")
                    failed += 1
                    continue

                updated_analysis = dict(analysis)
                updated_analysis["kind"] = result.kind
                updated_analysis["kind_confidence"] = result.confidence
                storage.update_analysis(item["id"], updated_analysis)

                if result.kind is not None:
                    console.print(
                        f"  [green]✓ {escape(str(result.kind))} ({escape(f'{result.confidence:.2f}')})[/green]"
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
            console.print(
                f"  ✓ Classified: [green]{escape(str(classified))}[/green] items"
            )
            console.print(
                f"  ⊙ Unclassified: [yellow]{escape(str(unclassified))}[/yellow] items"
            )
            if failed > 0:
                console.print(f"  ✗ Failed: [red]{escape(str(failed))}[/red] items")

    except Exception as e:
        console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
        raise typer.Exit(1) from e


@app.command(name="priority")
def priority(
    since_days: int = typer.Option(
        ..., "--since-days", help="Re-score items fetched in the last N days"
    ),
    limit: int = typer.Option(100, "--limit", "-n", help="Maximum items to re-score"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Count the items and estimate the cost; change nothing"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Re-evaluate the priority of already-analysed items against the current context.

    For when scoring itself went wrong (a bad prompt, gh #96) or context.md changed:
    each unarchived item with a summary fetched in the last --since-days days (newest
    first, at most --limit) is evaluated again, and its priority, matched_interests
    and priority_reasoning are replaced. The summary, kind and every other field are
    kept, and so are read, favorited and votes.
    """
    _check_local_mode("analyze priority")
    failed = 0
    try:
        from prismis_daemon.analysis import evaluation_fields
        from prismis_daemon.config import Config
        from prismis_daemon.evaluator import ContentEvaluator
        from prismis_daemon.observability import get_logger as get_obs_logger
        from prismis_daemon.observability import log as obs_log
        from prismis_daemon.refetch import upper_bound_cost_estimate
        from prismis_daemon.storage import Storage

        config = Config.from_file()
        with Storage() as storage:
            items = storage.get_content_for_rescore(limit=limit, since_days=since_days)
            if not items:
                console.print(
                    f"[green]✓ No analysed items fetched in the last {escape(str(since_days))} days[/green]"
                )
                return

            total = len(items)
            per_item = upper_bound_cost_estimate(
                get_obs_logger().base_dir, actions=("evaluate",)
            )
            console.print(
                f"[bold]{escape(str(total))} items to re-score[/bold] (fetched in the last {escape(str(since_days))} days)"
            )
            console.print(
                f"[dim]Estimated cost: at most ${escape(f'{per_item * total:.2f}')} "
                f"(99th-percentile evaluate cost ${escape(f'{per_item:.5f}')} per item)[/dim]"
            )
            if dry_run:
                return
            if not force and not Confirm.ask(f"Re-score {total} items?", default=False):
                console.print("[yellow]Aborted[/yellow]")
                return

            evaluator = ContentEvaluator(config.llm_light_service)
            start_time = time.time()
            obs_log(
                "cli.priority.start", source="cli", items=total, since_days=since_days
            )

            transitions: dict[tuple[str, str], int] = {}
            for idx, item in enumerate(items, 1):
                old = item.get("priority") or "none"
                try:
                    evaluation = evaluator.evaluate_content(
                        content=item.get("content") or "",
                        title=item["title"],
                        url=item["url"],
                        context=config.context,
                    )
                except Exception as e:
                    # Per-item boundary: a failed call keeps the item's old score.
                    logger.warning(
                        "Re-score failed for %s", item["title"], exc_info=True
                    )
                    console.print(
                        f"[{escape(str(idx))}/{escape(str(total))}] [red]✗ Failed: {escape(str(e))}[/red]"
                    )
                    failed += 1
                    continue

                new_priority = (
                    evaluation.priority.value if evaluation.priority else None
                )
                new = new_priority or "none"
                analysis = dict(item.get("analysis") or {})
                analysis.update(evaluation_fields(evaluation))
                # Retired by the removal of vote learning: nothing writes it any more.
                analysis.pop("preference_influenced", None)
                storage.update_priority(item["id"], new_priority, analysis)
                obs_log(
                    "cli.priority.item",
                    source="cli",
                    content_id=item["id"],
                    old=old,
                    new=new,
                )
                transitions[(old, new)] = transitions.get((old, new), 0) + 1
                if old != new:
                    console.print(
                        f"[{idx}/{total}] {old} → {new}: {escape(item['title'])}"
                    )

            changed = sum(n for (o, n2), n in transitions.items() if o != n2)
            obs_log(
                "cli.priority.complete",
                source="cli",
                duration_ms=int((time.time() - start_time) * 1000),
                rescored=total - failed,
                changed=changed,
                failed=failed,
            )

            console.print("\n[bold]Re-score Complete[/bold]")
            console.print(
                f"  ✓ Re-scored: [green]{escape(str(total - failed))}[/green] items"
            )
            console.print(f"  Changed: [cyan]{escape(str(changed))}[/cyan]")
            for (o, n2), n in sorted(transitions.items(), key=lambda kv: -kv[1]):
                if o != n2:
                    console.print(f"    {o} → {n2}: {n}")
            if failed > 0:
                console.print(
                    f"  ✗ Failed: [red]{escape(str(failed))}[/red] items (scores kept)"
                )
            if config.llm_deep_service and any(
                n2 == "high" and o != "high" for o, n2 in transitions
            ):
                console.print(
                    "[dim]Newly HIGH items have no deep extraction yet: "
                    "run 'prismis-cli extract --priority high'[/dim]"
                )

    except Exception as e:
        console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
        raise typer.Exit(1) from e
    # Outside the try: typer.Exit subclasses RuntimeError, and the except above
    # would report it as a second error.
    if failed:
        raise typer.Exit(1)
