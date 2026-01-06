"""Embedding management commands."""

import typer
from prismis_daemon.storage import Storage
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

# Heavy import (sentence-transformers) is lazy-loaded in generate() to keep CLI fast

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
        raise typer.Exit(1) from e


@app.command(name="status")
def status() -> None:
    """Show semantic search indexing status."""
    try:
        with Storage() as storage:
            # Count total embeddings
            cursor = storage.conn.execute("SELECT COUNT(*) FROM embeddings")
            indexed = cursor.fetchone()[0]

            # Count total content
            cursor = storage.conn.execute("SELECT COUNT(*) FROM content")
            total = cursor.fetchone()[0]

        if total == 0:
            console.print("[dim]No content in database[/dim]")
            return

        percentage = int((indexed / total) * 100) if total > 0 else 0
        console.print("\n[bold]Semantic Search Index Status:[/bold]")
        console.print(
            f"  Indexed: [green]{indexed}/{total}[/green] items ({percentage}%)"
        )

        if indexed < total:
            missing = total - indexed
            console.print(
                f"  Missing: [yellow]{missing}[/yellow] items need embeddings\n"
            )
            console.print(
                "[dim]Run 'prismis-cli embeddings generate' to index remaining items[/dim]"
            )
        else:
            console.print("  [green]✓ All items indexed[/green]\n")

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command(name="generate")
def generate() -> None:
    """Generate embeddings for content without them.

    Processes items in batches of 100, committing after each batch.
    Shows progress and handles failures gracefully.
    """
    try:
        with Storage() as storage:
            # Count items needing embeddings
            total_missing = storage.count_content_without_embeddings()

            if total_missing == 0:
                console.print("[green]✓ All items already have embeddings[/green]")
                return

            console.print(
                f"[bold]Generating embeddings for {total_missing} items...[/bold]"
            )
            console.print("[dim]This may take 10-30 minutes for large datasets[/dim]\n")

            # Lazy import heavy embedding dependencies (only when actually generating)
            from prismis_daemon.embeddings import Embedder

            # Initialize embedder
            embedder = Embedder()

            processed = 0
            failed = 0
            batch_size = 100

            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                console=console,
            ) as progress:
                task = progress.add_task("Processing...", total=total_missing)

                while True:
                    # Get next batch
                    batch = storage.get_content_without_embeddings(limit=batch_size)
                    if not batch:
                        break

                    for item in batch:
                        try:
                            # Generate embedding from summary or content
                            text = item["summary"] or item["content"] or item["title"]
                            embedding = embedder.generate_embedding(
                                text=text, title=item["title"]
                            )

                            if embedding:
                                # Store embedding
                                storage.add_embedding(
                                    content_id=item["id"], embedding=embedding
                                )
                                processed += 1
                            else:
                                failed += 1

                        except Exception as e:
                            console.print(
                                f"[yellow]⚠ Failed to generate embedding for '{item['title']}': {e}[/yellow]"
                            )
                            failed += 1

                        # Update progress
                        progress.update(
                            task,
                            advance=1,
                            description=f"Processed {processed}/{total_missing} ({failed} failed)",
                        )

            # Summary
            console.print("\n[bold]Generation Complete[/bold]")
            console.print(f"  ✓ Generated: [green]{processed}[/green] embeddings")
            if failed > 0:
                console.print(f"  ✗ Failed: [red]{failed}[/red] items")

    except Exception as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e
