"""Batch deep extraction CLI command."""

import typer
from rich.console import Console

from .api_client import APIClient

console = Console()


def extract(
    priority: str = typer.Option(
        "high",
        "--priority",
        "-p",
        help="Priority filter: high, medium, low, all (default: high)",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-l",
        help="Maximum items to process (default: 10, max: 3333)",
    ),
) -> None:
    """Backfill deep extractions for existing content.

    Calls POST /api/entries/{id}/extract per matching item. Idempotent:
    items already with analysis.deep_extraction are skipped client-side
    (the server endpoint also enforces idempotency via INV-004).
    """
    if priority not in ("high", "medium", "low", "all"):
        console.print(
            f"[red]Invalid --priority '{priority}' (expected high/medium/low/all)[/red]"
        )
        raise typer.Exit(1)

    if limit < 1:
        console.print(
            f"[green]No items need extraction for priority={priority}[/green]"
        )
        return

    if limit > 3333:
        console.print(
            f"[red]--limit {limit} exceeds maximum (3333). "
            "The server caps /api/entries at 10000 items and the CLI fetches "
            "3x --limit as candidates, so the effective ceiling is 10000 / 3 = 3333.[/red]"
        )
        raise typer.Exit(1)

    client = APIClient()

    try:
        candidates = client.get_content(
            priority=None if priority == "all" else priority,
            limit=limit * 3,
        )
    except RuntimeError as e:
        console.print(f"[red]Failed to list entries: {e}[/red]")
        raise typer.Exit(1) from e

    pending = [
        item
        for item in candidates
        if not (item.get("analysis") or {}).get("deep_extraction")
    ][:limit]

    if not pending:
        console.print(
            f"[green]No items need extraction for priority={priority}[/green]"
        )
        return

    console.print(
        f"[bold]Extracting {len(pending)} items (priority={priority})...[/bold]\n"
    )

    succeeded = 0
    failed = 0
    for idx, item in enumerate(pending, 1):
        title = item.get("title", "")[:60]
        console.print(f"[{idx}/{len(pending)}] {title}")
        try:
            client.extract_entry(item.get("id", ""))
            succeeded += 1
            console.print("  [green]ok[/green]")
        except RuntimeError as e:
            failed += 1
            console.print(f"  [red]failed: {e}[/red]")

    console.print(f"\n[bold]Done: {succeeded} extracted, {failed} failed[/bold]")


if __name__ == "__main__":
    typer.run(extract)
