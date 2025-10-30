"""Archive management commands."""

import typer
from rich.console import Console

from .api_client import APIClient

console = Console()
app = typer.Typer()  # Sub-typer for archive commands


@app.command(name="status")
def status() -> None:
    """Show archival status and configuration."""
    try:
        client = APIClient()
        data = client.get_archive_status()

        enabled_status = (
            "[green]Enabled[/green]" if data["enabled"] else "[red]Disabled[/red]"
        )
        console.print(f"\n[bold]Archival Status:[/bold] {enabled_status}")
        console.print(f"Total items: {data['total_items']}")
        console.print(f"  Active: [green]{data['active_items']}[/green]")
        console.print(f"  Archived: [dim]{data['archived_items']}[/dim]")

        console.print("\n[bold]Archival Windows:[/bold]")
        windows = data["windows"]
        console.print(f"  HIGH read: {windows['high_read']} days")
        console.print(f"  MEDIUM unread: {windows['medium_unread']} days")
        console.print(f"  MEDIUM read: {windows['medium_read']} days")
        console.print(f"  LOW unread: {windows['low_unread']} days")
        console.print(f"  LOW read: {windows['low_read']} days\n")

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)
