"""Get command for retrieving content entries."""

import sys

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .api_client import APIClient

console = Console()


def get(
    entry_id: str = typer.Argument(..., help="UUID of the content entry to retrieve"),
    raw: bool = typer.Option(False, "--raw", help="Output raw content for piping"),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Retrieve a content entry by ID.

    Args:
        entry_id: UUID of the content entry
        raw: If True, output raw content text only (for piping to tools)
        output_json: If True, output raw JSON instead of formatted display
    """
    try:
        client = APIClient()

        if raw and output_json:
            console.print("[red]✗ Error: Cannot use --raw and --json together[/red]")
            raise typer.Exit(1)

        if raw:
            # Raw mode - output plain text to stdout for piping
            content = client.get_entry_raw(entry_id)
            sys.stdout.write(content)
        elif output_json:
            # JSON mode - output full API response
            import json

            entry = client.get_entry(entry_id)
            sys.stdout.write(json.dumps(entry, indent=2) + "\n")
        else:
            # Formatted mode - display entry details with rich
            entry = client.get_entry(entry_id)

            # Create table for entry metadata
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Field", style="bold cyan")
            table.add_column("Value")

            # Add entry fields
            table.add_row("ID", entry.get("id", "N/A"))
            table.add_row("Title", entry.get("title", "N/A"))
            table.add_row("URL", entry.get("url", "N/A"))
            table.add_row("Priority", entry.get("priority", "N/A").upper())
            table.add_row("Source", entry.get("source_name", "N/A"))

            # Format published date
            published = entry.get("published")
            if published:
                table.add_row("Published", published)

            # Display table
            console.print("\n")
            console.print(table)

            # Display summary in panel if available
            summary = entry.get("summary")
            if summary:
                console.print("\n")
                console.print(
                    Panel(
                        summary,
                        title="Summary",
                        border_style="cyan",
                    )
                )

            console.print("\n")

    except RuntimeError as e:
        if not output_json:
            console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e
