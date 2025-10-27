"""Export command for bulk data export in JSON/CSV formats."""

import csv
import json
import sys
from typing import Optional

import typer
from rich.console import Console

from .api_client import APIClient

console = Console(stderr=True)  # Console to stderr to keep stdout clean


def export(
    format: str = typer.Option(
        "json", "--format", "-f", help="Export format (json or csv)"
    ),
    priority: Optional[str] = typer.Option(
        None, "--priority", "-p", help="Filter by priority (high, medium, low)"
    ),
    unread: bool = typer.Option(
        False, "--unread", "-u", help="Export only unread items"
    ),
    limit: int = typer.Option(
        100, "--limit", "-l", help="Maximum number of items (1-1000)"
    ),
) -> None:
    """Export content entries to JSON or CSV format.

    Outputs to stdout for piping to files or other tools.
    Use shell redirection to save: prismis-cli export --format json > output.json

    Args:
        format: Output format (json or csv)
        priority: Filter by priority level (high, medium, low)
        unread: If True, export only unread items
        limit: Maximum number of items to export (1-1000)
    """
    # Validate format
    if format not in ["json", "csv"]:
        console.print(f"[red]✗ Invalid format '{format}'. Use 'json' or 'csv'[/red]")
        raise typer.Exit(1)

    try:
        client = APIClient()

        # Get content from API
        entries = client.get_content(priority=priority, unread_only=unread, limit=limit)

        if not entries:
            # Empty export is valid - output empty structure
            if format == "json":
                sys.stdout.write("[]\n")
            elif format == "csv":
                # CSV with just headers for empty data
                writer = csv.DictWriter(
                    sys.stdout,
                    fieldnames=[
                        "id",
                        "title",
                        "url",
                        "priority",
                        "summary",
                        "published",
                    ],
                )
                writer.writeheader()
            return

        # Export based on format
        if format == "json":
            # Output JSON array
            json_output = json.dumps(entries, indent=2, ensure_ascii=False)
            sys.stdout.write(json_output)
            sys.stdout.write("\n")

        elif format == "csv":
            # Define CSV fields (excluding content field for performance)
            fieldnames = ["id", "title", "url", "priority", "summary", "published"]

            writer = csv.DictWriter(
                sys.stdout, fieldnames=fieldnames, extrasaction="ignore"
            )
            writer.writeheader()

            # Write entries (DictWriter will ignore extra fields like 'content')
            for entry in entries:
                writer.writerow(entry)

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)
