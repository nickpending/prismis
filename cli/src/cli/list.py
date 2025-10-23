"""List command for displaying content entries."""

from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from .api_client import APIClient

app = typer.Typer()
console = Console()


@app.command()
def list(
    priority: Optional[str] = typer.Option(
        None, "--priority", "-p", help="Filter by priority (high, medium, low)"
    ),
    unread: bool = typer.Option(False, "--unread", "-u", help="Show only unread items"),
    limit: int = typer.Option(
        50, "--limit", "-l", help="Maximum number of items (1-100)"
    ),
) -> None:
    """List content entries with optional filtering.

    Args:
        priority: Filter by priority level (high, medium, low)
        unread: If True, show only unread items
        limit: Maximum number of items to display (1-100)
    """
    try:
        client = APIClient()

        # Get content from API
        entries = client.get_content(priority=priority, unread_only=unread, limit=limit)

        if not entries:
            console.print("[yellow]No entries found[/yellow]")
            return

        # Create table for entries
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim", width=10)
        table.add_column("Title", style="bold", width=60)
        table.add_column("Priority", justify="center", width=8)
        table.add_column("Published", style="dim", width=19)

        # Add entries to table
        for entry in entries:
            # Truncate ID to first 8 characters
            entry_id = entry.get("id", "")[:8]

            # Truncate title if too long
            title = entry.get("title", "N/A")
            if len(title) > 57:
                title = title[:57] + "..."

            # Format priority with color
            priority_val = entry.get("priority", "N/A").upper()
            if priority_val == "HIGH":
                priority_display = f"[red]{priority_val}[/red]"
            elif priority_val == "MEDIUM":
                priority_display = f"[yellow]{priority_val}[/yellow]"
            elif priority_val == "LOW":
                priority_display = f"[green]{priority_val}[/green]"
            else:
                priority_display = priority_val

            # Format published date (already formatted from API)
            published = entry.get("published", "N/A")

            table.add_row(entry_id, title, priority_display, published)

        # Display table
        console.print("\n")
        console.print(table)
        console.print(f"\n[dim]Showing {len(entries)} entries[/dim]\n")

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)
