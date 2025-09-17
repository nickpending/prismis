"""Source management commands for Prismis CLI."""

import re
from typing import Optional
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

# Import API client for write operations
from .api_client import APIClient

# For read operations only - direct database access is OK
from prismis_daemon.storage import Storage
from prismis_daemon.database import init_db

app = typer.Typer(help="Manage content sources")
console = Console()


def extract_name_from_url(url: str) -> str:
    """Extract a human-readable name from a URL.

    Args:
        url: The source URL

    Returns:
        A reasonable name extracted from the URL
    """
    # Remove protocol
    url = re.sub(r"^https?://", "", url)
    url = re.sub(r"^reddit://", "", url)
    url = re.sub(r"^youtube://", "", url)

    # Remove www.
    url = re.sub(r"^www\.", "", url)

    # Remove paths and query strings for domain extraction
    domain = url.split("/")[0].split("?")[0]

    # For reddit subreddits
    if "reddit.com/r/" in url or url.startswith("r/"):
        match = re.search(r"/r/([^/\?]+)", url)
        if match:
            return f"r/{match.group(1)}"
        # For reddit:// URLs
        parts = url.split("/")
        if parts:
            return f"r/{parts[-1]}"

    # For YouTube channels
    if "youtube.com" in url or "youtu.be" in url:
        # Try to extract channel name (matching API behavior)
        if "@" in url:
            match = re.search(r"@([^/\?]+)", url)
            if match:
                return f"@{match.group(1)}"
        elif "channel/" in url:
            match = re.search(r"channel/([^/\?]+)", url)
            if match:
                return match.group(1)[:20]
        return "YouTube Channel"

    # For regular domains, use the domain name
    return domain.split(".")[0].title() if "." in domain else domain


@app.command()
def add(
    url: str = typer.Argument(
        ..., help="URL of the content source (RSS, reddit://, youtube://)"
    ),
    name: Optional[str] = typer.Option(
        None, "--name", "-n", help="Custom name for the source"
    ),
) -> None:
    """Add a new content source to Prismis."""
    try:
        # Ensure database exists
        db_path = Path.home() / ".config" / "prismis" / "prismis.db"
        if not db_path.exists():
            console.print("[yellow]Database not found, initializing...[/yellow]")
            init_db()

        # Detect source type from URL
        source_type = "rss"  # Default

        if url.startswith("reddit://"):
            source_type = "reddit"
            # Convert reddit:// to actual Reddit URL
            subreddit = url.replace("reddit://", "")
            url = f"https://www.reddit.com/r/{subreddit}"
        elif url.startswith("youtube://"):
            source_type = "youtube"
            # Convert youtube:// to actual YouTube URL (similar to Reddit pattern)
            channel = url.replace("youtube://", "")
            # Handle different channel formats
            if channel.startswith("@"):
                url = f"https://www.youtube.com/{channel}"
            elif channel.startswith("UC") or channel.startswith("PL"):
                # Looks like a channel/playlist ID
                url = f"https://www.youtube.com/channel/{channel}"
            else:
                # Assume it's a handle without @
                url = f"https://www.youtube.com/@{channel}"
        elif "reddit.com" in url:
            source_type = "reddit"
            # Keep the URL as-is for PRAW to handle
            url = url.rstrip("/")
        elif "youtube.com" in url or "youtu.be" in url:
            source_type = "youtube"

        # Auto-generate name if not provided
        if not name:
            name = extract_name_from_url(url)

        # Use API to add source (includes validation)
        console.print(f"[yellow]Adding {source_type} source...[/yellow]")

        try:
            api_client = APIClient()
            result = api_client.add_source(url, source_type, name)
            source_id = result.get("id", "unknown")
        except RuntimeError as e:
            # Check if it's a validation error
            error_msg = str(e)
            if "validation failed" in error_msg.lower():
                console.print(f"[red]❌ Validation failed:[/red] {error_msg}")
            else:
                console.print(f"[red]❌ API error:[/red] {error_msg}")
            raise typer.Exit(1)

        # Use the name from the API response if available
        response_name = result.get("name", name)
        console.print(f"[green]✅ Added {source_type} source:[/green] {response_name}")
        console.print(f"[dim]URL: {url}[/dim]")
        console.print(f"[dim]ID: {source_id}[/dim]")

    except Exception as e:
        console.print(f"[red]❌ Failed to add source:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command("list")
def list_sources() -> None:
    """List all configured content sources."""
    try:
        # Ensure database exists
        db_path = Path.home() / ".config" / "prismis" / "prismis.db"
        if not db_path.exists():
            console.print(
                "[yellow]No database found. Run 'source add' to create one.[/yellow]"
            )
            return

        storage = Storage()
        sources = storage.get_all_sources()

        if not sources:
            console.print(
                "[yellow]No sources configured. Use 'source add' to add one.[/yellow]"
            )
            return

        # Create Rich table
        table = Table(title="Content Sources", show_lines=True)
        table.add_column("ID", style="cyan", no_wrap=True)
        table.add_column("Type", style="magenta")
        table.add_column("Name", style="white")
        table.add_column("Active", style="green")
        table.add_column("Errors", style="red")
        table.add_column("Last Fetched", style="dim")

        for source in sources:
            # Format values
            active_str = "✅ Yes" if source["active"] else "❌ No"
            error_str = str(source["error_count"]) if source["error_count"] else "—"
            last_fetched = source["last_fetched_at"] or "Never"
            if last_fetched != "Never":
                # Truncate timestamp for readability
                last_fetched = last_fetched[:19]

            # Truncate name if too long
            name = source["name"] or "Unnamed"
            if len(name) > 25:
                name = name[:22] + "..."

            table.add_row(
                source["id"], source["type"], name, active_str, error_str, last_fetched
            )

        console.print(table)
        console.print(f"\n[dim]Total sources: {len(sources)}[/dim]")

    except Exception as e:
        console.print(f"[red]❌ Failed to list sources:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def remove(
    source_id: str = typer.Argument(..., help="UUID of the source to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a content source from Prismis."""
    try:
        # Ensure database exists
        db_path = Path.home() / ".config" / "prismis" / "prismis.db"
        if not db_path.exists():
            console.print("[red]No database found.[/red]")
            raise typer.Exit(1)

        storage = Storage()

        # First, try to get the source to show what we're removing
        sources = storage.get_all_sources()
        source_to_remove = None
        for source in sources:
            if source["id"] == source_id:
                source_to_remove = source
                break

        if not source_to_remove:
            console.print(f"[red]❌ Source not found:[/red] {source_id}")
            raise typer.Exit(1)

        # Show what we're about to remove
        console.print("[yellow]Source to remove:[/yellow]")
        console.print(f"  Name: {source_to_remove['name'] or 'Unnamed'}")
        console.print(f"  Type: {source_to_remove['type']}")
        console.print(f"  URL: {source_to_remove['url']}")
        console.print(
            "[dim]  Note: This will also delete all content from this source[/dim]"
        )

        # Confirm unless --force
        if not force:
            confirm = typer.confirm(
                "Are you sure you want to remove this source and all its content?"
            )
            if not confirm:
                console.print("[dim]Cancelled.[/dim]")
                raise typer.Exit(0)

        # Remove the source via API
        try:
            api_client = APIClient()
            api_client.remove_source(source_id)
            console.print(
                f"[green]✅ Removed source:[/green] {source_to_remove['name'] or 'Unnamed'}"
            )
        except RuntimeError as e:
            console.print(f"[red]❌ Failed to remove source:[/red] {str(e)}")
            raise typer.Exit(1)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]❌ Failed to remove source:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def pause(
    source_id: str = typer.Argument(..., help="UUID of the source to pause"),
) -> None:
    """Pause a content source (set inactive)."""
    try:
        api_client = APIClient()
        api_client.pause_source(source_id)
        console.print(f"[green]✅ Paused source:[/green] {source_id}")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]❌ Failed to pause source:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def resume(
    source_id: str = typer.Argument(..., help="UUID of the source to resume"),
) -> None:
    """Resume a paused content source (set active)."""
    try:
        api_client = APIClient()
        api_client.resume_source(source_id)
        console.print(f"[green]✅ Resumed source:[/green] {source_id}")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]❌ Failed to resume source:[/red] {str(e)}")
        raise typer.Exit(1)


@app.command()
def edit(
    source_id: str = typer.Argument(..., help="UUID of the source to edit"),
    name: str = typer.Argument(..., help="New name for the source"),
) -> None:
    """Edit a source's name."""
    try:
        api_client = APIClient()
        api_client.edit_source(source_id, name)
        console.print(f"[green]✅ Updated source name to:[/green] {name}")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]❌ Failed to edit source:[/red] {str(e)}")
        raise typer.Exit(1)


if __name__ == "__main__":
    app()
