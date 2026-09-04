"""Source management commands for Prismis CLI."""

import re

import typer
from rich.console import Console
from rich.table import Table

from .api_client import APIClient

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


def detect_and_normalize_source_url(url: str) -> tuple[str, str]:
    """Derive a source's type from its URL and expand protocol URLs to real ones.

    The daemon normalizes independently in `normalize_source_url`
    (daemon/src/prismis_daemon/api.py), which is told the type rather than deriving it.
    The two do NOT agree on every input, and nothing checks that they do — measured
    divergences, pinned by the tests below so either side moving becomes visible:

    - `youtube://PL...` — this function treats a `PL` prefix as a channel/playlist id and
      produces `/channel/PL...`; the daemon matches only `UC` and falls through to
      `/@PL...`.
    - trailing slashes and leading whitespace — the daemon strips both; this function
      strips neither, so `reddit://rust/` keeps its slash and ` reddit://rust` is not even
      recognised as a reddit URL.

    Filed as gh #65; which side is right is a product decision, so neither is changed here.

    Args:
        url: The source URL as the user typed it, possibly a `reddit://` or
            `youtube://` protocol URL

    Returns:
        (source_type, url) — the detected type and the URL to send to the API
    """
    # Check for file extensions (.md, .txt)
    if url.endswith((".md", ".txt")):
        return "file", url

    if url.startswith("reddit://"):
        # Convert reddit:// to actual Reddit URL
        subreddit = url.replace("reddit://", "")
        return "reddit", f"https://www.reddit.com/r/{subreddit}"

    if url.startswith("youtube://"):
        # Convert youtube:// to actual YouTube URL (similar to Reddit pattern)
        channel = url.replace("youtube://", "")
        # Handle different channel formats
        if channel.startswith("@"):
            return "youtube", f"https://www.youtube.com/{channel}"
        if channel.startswith("UC") or channel.startswith("PL"):
            # Looks like a channel/playlist ID
            return "youtube", f"https://www.youtube.com/channel/{channel}"
        # Assume it's a handle without @
        return "youtube", f"https://www.youtube.com/@{channel}"

    if "reddit.com" in url:
        # Keep the URL as-is for PRAW to handle
        return "reddit", url.rstrip("/")

    if "youtube.com" in url or "youtu.be" in url:
        return "youtube", url

    return "rss", url


def find_source_by_id(sources: list[dict], source_id: str) -> dict | None:
    """Find a source by its id in a list from the API.

    Returns:
        The matching source, or None when no source has that id — which the caller
        must treat as "not found" and refuse to delete anything for.
    """
    for source in sources:
        if source["id"] == source_id:
            return source
    return None


def format_source_row(source: dict) -> tuple[str, str, str, str, str, str]:
    """Render one API source dict as the six columns of the `source list` table.

    Returns:
        (id, type, name, active, errors, last_fetched) as display strings
    """
    active_str = "✅ Yes" if source.get("active") else "❌ No"
    error_str = str(source.get("error_count", 0)) if source.get("error_count") else "—"

    last_fetched = source.get("last_fetched") or "Never"
    if last_fetched != "Never":
        # Truncate timestamp for readability
        last_fetched = str(last_fetched)[:19]

    # Truncate name if too long
    name = source["name"] or "Unnamed"
    if len(name) > 25:
        name = name[:22] + "..."

    return (
        source["id"],
        source["type"],
        name,
        active_str,
        error_str,
        str(last_fetched),
    )


@app.command()
def add(
    url: str = typer.Argument(
        ..., help="URL of the content source (RSS, reddit://, youtube://)"
    ),
    name: str | None = typer.Option(
        None, "--name", "-n", help="Custom name for the source"
    ),
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """Add a new content source to Prismis."""
    try:
        # Detect source type from URL
        source_type, url = detect_and_normalize_source_url(url)

        # Auto-generate name if not provided
        if not name:
            name = extract_name_from_url(url)

        # Use API to add source (includes validation)
        if not output_json:
            console.print(f"[yellow]Adding {source_type} source...[/yellow]")

        try:
            api_client = APIClient()
            result = api_client.add_source(url, source_type, name)

            if output_json:
                # JSON mode - output raw API response
                import json
                import sys

                sys.stdout.write(json.dumps(result, indent=2) + "\n")
                return

            source_id = result.get("id", "unknown")
        except RuntimeError as e:
            # Check if it's a validation error
            error_msg = str(e)
            if not output_json:
                if "validation failed" in error_msg.lower():
                    console.print(f"[red]❌ Validation failed:[/red] {error_msg}")
                else:
                    console.print(f"[red]❌ API error:[/red] {error_msg}")
            raise typer.Exit(1) from e

        # Use the name from the API response if available
        response_name = result.get("name", name)
        console.print(f"[green]✅ Added {source_type} source:[/green] {response_name}")
        console.print(f"[dim]URL: {url}[/dim]")
        console.print(f"[dim]ID: {source_id}[/dim]")

    except Exception as e:
        if not output_json:
            console.print(f"[red]❌ Failed to add source:[/red] {str(e)}")
        raise typer.Exit(1) from e


@app.command("list")
def list_sources(
    output_json: bool = typer.Option(False, "--json", help="Output as JSON"),
) -> None:
    """List all configured content sources."""
    try:
        api_client = APIClient()
        sources = api_client.get_sources()

        if output_json:
            # JSON mode - output raw source list
            import json
            import sys

            sys.stdout.write(json.dumps(sources, indent=2) + "\n")
            return

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
            table.add_row(*format_source_row(source))

        console.print(table)
        console.print(f"\n[dim]Total sources: {len(sources)}[/dim]")

    except Exception as e:
        if not output_json:
            console.print(f"[red]❌ Failed to list sources:[/red] {str(e)}")
        raise typer.Exit(1) from e


@app.command()
def remove(
    source_id: str = typer.Argument(..., help="UUID of the source to remove"),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
) -> None:
    """Remove a content source from Prismis."""
    try:
        api_client = APIClient()

        # First, try to get the source to show what we're removing
        sources = api_client.get_sources()
        source_to_remove = find_source_by_id(sources, source_id)

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
            api_client.remove_source(source_id)
            console.print(
                f"[green]✅ Removed source:[/green] {source_to_remove['name'] or 'Unnamed'}"
            )
        except RuntimeError as e:
            console.print(f"[red]❌ Failed to remove source:[/red] {str(e)}")
            raise typer.Exit(1) from e

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[red]❌ Failed to remove source:[/red] {str(e)}")
        raise typer.Exit(1) from e


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
        raise typer.Exit(1) from e


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
        raise typer.Exit(1) from e


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
        raise typer.Exit(1) from e


if __name__ == "__main__":
    app()
