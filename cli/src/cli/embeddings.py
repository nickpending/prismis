"""Embedding management commands."""

import typer
from rich.console import Console

from prismis_daemon.storage import Storage

console = Console()
app = typer.Typer()  # Sub-typer for embeddings commands


@app.command(name="cleanup")
def cleanup() -> None:
    """Clean up orphaned vectors from semantic search index.

    Virtual tables don't support CASCADE, so vectors can remain after
    content deletion. This command removes orphaned vectors.
    """
    try:
        console.print("[dim]Checking for orphaned vectors...[/dim]")

        with Storage() as storage:
            count = storage.cleanup_orphaned_vectors()

        if count == 0:
            console.print("[green]✓ No orphaned vectors found[/green]")
        else:
            console.print(f"[green]✓ Cleaned up {count} orphaned vector(s)[/green]")

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)
