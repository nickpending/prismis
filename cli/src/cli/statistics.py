"""Statistics command for system-wide metrics."""

import sys

import typer
from rich.console import Console
from rich.table import Table

from .api_client import APIClient

console = Console()


def statistics(
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Display system-wide statistics.

    Shows counts for content items (by priority, read status, archive status)
    and configured sources (active/paused).

    Args:
        output_json: If True, output raw JSON instead of formatted display
    """
    try:
        client = APIClient()

        # Get statistics from API
        stats = client.get_statistics()

        if output_json:
            # JSON mode - output raw API response
            import json

            sys.stdout.write(json.dumps(stats, indent=2) + "\n")
            return

        # Format output with Rich tables
        content_stats = stats.get("content", {})
        source_stats = stats.get("sources", {})
        priority_counts = content_stats.get("by_priority", {})
        read_counts = content_stats.get("by_read_status", {})

        # Create main statistics table
        table = Table(title="System Statistics", show_lines=True)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Count", justify="right", style="white")

        # Content overview
        table.add_row("Total Content", str(content_stats.get("total", 0)))
        table.add_row("  Active", str(content_stats.get("active", 0)))
        table.add_row("  Archived", str(content_stats.get("archived", 0)))

        # Priority breakdown
        table.add_row("Priority: High", f"[red]{priority_counts.get('high', 0)}[/red]")
        table.add_row(
            "Priority: Medium", f"[yellow]{priority_counts.get('medium', 0)}[/yellow]"
        )
        table.add_row(
            "Priority: Low", f"[green]{priority_counts.get('low', 0)}[/green]"
        )
        table.add_row(
            "Priority: Unprioritized",
            f"[dim]{priority_counts.get('unprioritized', 0)}[/dim]",
        )

        # Read status
        table.add_row("Unread", str(read_counts.get("unread", 0)))
        table.add_row("Read", str(read_counts.get("read", 0)))

        # Sources
        table.add_row("Total Sources", str(source_stats.get("total", 0)))
        table.add_row("  Active Sources", str(source_stats.get("active", 0)))
        table.add_row("  Paused Sources", str(source_stats.get("paused", 0)))

        console.print("\n")
        console.print(table)
        console.print("\n")

    except RuntimeError as e:
        if not output_json:
            console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e
