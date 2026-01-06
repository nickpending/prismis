"""Search command for semantic content search."""

import typer
from rich.console import Console
from rich.table import Table

from .api_client import APIClient

console = Console()


def search(
    query: str = typer.Argument(..., help="Search query"),
    limit: int = typer.Option(
        20, "--limit", "-l", help="Maximum number of results (1-50)"
    ),
    source: str = typer.Option(
        None,
        "--source",
        "-s",
        help="Filter by source name (case-insensitive substring)",
    ),
    compact: bool = typer.Option(
        False, "--compact", help="Compact format (excludes content and analysis)"
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Search content using semantic similarity.

    Args:
        query: Search query string
        limit: Maximum number of results to return (1-50)
        source: Filter results to sources containing this substring
        compact: Return compact format for LLM consumption
        output_json: If True, output raw JSON instead of formatted table
    """
    try:
        client = APIClient()

        # Validate limit
        if limit < 1 or limit > 50:
            if not output_json:
                console.print("[red]✗ Error: Limit must be between 1 and 50[/red]")
            raise typer.Exit(1)

        # Search content
        results = client.search(query, limit=limit, compact=compact, source=source)

        if output_json:
            # JSON mode - output raw API response
            import json
            import sys

            sys.stdout.write(json.dumps(results, indent=2) + "\n")
            return

        if not results:
            console.print(f"[yellow]No results found for '{query}'[/yellow]")
            return

        # Create table for results
        table = Table(show_header=True, header_style="bold cyan")
        table.add_column("ID", style="dim", width=10)
        table.add_column("Title", style="bold", width=50)
        table.add_column("Priority", justify="center", width=8)
        table.add_column("Score", justify="right", width=6)
        table.add_column("Published", style="dim", width=19)

        # Add results to table
        for result in results:
            # Truncate ID to first 8 characters
            result_id = result.get("id", "")[:8]

            # Truncate title if too long
            title = result.get("title", "N/A")
            if len(title) > 47:
                title = title[:47] + "..."

            # Format priority with color
            priority_val = (result.get("priority") or "N/A").upper()
            if priority_val == "HIGH":
                priority_display = f"[red]{priority_val}[/red]"
            elif priority_val == "MEDIUM":
                priority_display = f"[yellow]{priority_val}[/yellow]"
            elif priority_val == "LOW":
                priority_display = f"[green]{priority_val}[/green]"
            else:
                priority_display = priority_val

            # Format relevance score
            relevance = result.get("relevance_score", 0.0)
            score_display = f"{relevance:.3f}"

            # Format published date (already formatted from API)
            published = result.get("published_at", "N/A")

            table.add_row(result_id, title, priority_display, score_display, published)

        # Display table
        console.print("\n")
        console.print(table)
        console.print(
            f"\n[dim]Found {len(results)} result{'s' if len(results) != 1 else ''} for '{query}'[/dim]\n"
        )

    except RuntimeError as e:
        if not output_json:
            console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e
