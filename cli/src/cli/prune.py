"""Prune commands for removing unprioritized content."""

import typer
from rich.console import Console
from rich.prompt import Confirm
from typing import Optional
import re
from .api_client import APIClient

app = typer.Typer()
console = Console()


def parse_age(age_str: str) -> int:
    """Parse age strings like '7d', '2w', '1m' to days.

    Args:
        age_str: Age string with format like '7d', '2w', '1m'

    Returns:
        Number of days

    Raises:
        ValueError: If format is invalid
    """
    pattern = r"^(\d+)([dwm])$"
    match = re.match(pattern, age_str.lower())

    if not match:
        raise ValueError(
            f"Invalid age format: {age_str}. Use format like '7d', '2w', '1m'"
        )

    value = int(match.group(1))
    unit = match.group(2)

    if unit == "d":
        return value
    elif unit == "w":
        return value * 7
    elif unit == "m":
        return value * 30
    else:
        raise ValueError(f"Invalid age unit: {unit}")


@app.command()
def count(
    age: Optional[str] = typer.Argument(
        None, help="Age filter (e.g., '7d', '2w', '1m')"
    ),
) -> None:
    """Count unprioritized content items.

    Args:
        age: Optional age filter - only count items older than this
    """
    try:
        client = APIClient()

        # Parse age if provided
        days = None
        if age:
            try:
                days = parse_age(age)
                console.print(
                    f"🔍 Counting unprioritized items older than {days} days..."
                )
            except ValueError as e:
                console.print(f"[red]✗ {e}[/red]")
                raise typer.Exit(1)
        else:
            console.print("🔍 Counting all unprioritized items...")

        # Get count from API
        count = client.count_unprioritized(days)

        if count == 0:
            console.print("✨ No unprioritized items found")
        else:
            age_text = f" older than {days} days" if days else ""
            console.print(
                f"📊 Found [bold yellow]{count}[/bold yellow] unprioritized items{age_text}"
            )

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def delete(
    age: Optional[str] = typer.Argument(
        None, help="Age filter (e.g., '7d', '2w', '1m')"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Delete unprioritized content items.

    Args:
        age: Optional age filter - only delete items older than this
        force: Skip confirmation prompt
    """
    try:
        client = APIClient()

        # Parse age if provided
        days = None
        if age:
            try:
                days = parse_age(age)
            except ValueError as e:
                console.print(f"[red]✗ {e}[/red]")
                raise typer.Exit(1)

        # First get count
        count = client.count_unprioritized(days)

        if count == 0:
            console.print("✨ No unprioritized items to delete")
            return

        # Show what will be deleted
        age_text = f" older than {days} days" if days else ""
        console.print(
            f"⚠️  Found [bold red]{count}[/bold red] unprioritized items{age_text}"
        )

        # Confirm unless forced
        if not force:
            if not Confirm.ask("Delete these items?", default=False):
                console.print("❌ Deletion cancelled")
                return

        # Delete items
        console.print("🗑️  Deleting unprioritized items...")
        result = client.prune_unprioritized(days)

        deleted_count = result.get("deleted", 0)
        console.print(f"✅ Deleted [bold green]{deleted_count}[/bold green] items")

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def cleanup(
    days: int = typer.Option(
        30, "--days", "-d", help="Delete items older than this many days"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation prompt"),
) -> None:
    """Clean up old unprioritized content (default: items older than 30 days).

    Args:
        days: Delete items older than this many days
        force: Skip confirmation prompt
    """
    try:
        client = APIClient()

        # Get count
        count = client.count_unprioritized(days)

        if count == 0:
            console.print(f"✨ No unprioritized items older than {days} days")
            return

        # Show what will be deleted
        console.print(
            f"⚠️  Found [bold red]{count}[/bold red] unprioritized items older than {days} days"
        )

        # Confirm unless forced
        if not force:
            if not Confirm.ask("Delete these old items?", default=False):
                console.print("❌ Cleanup cancelled")
                return

        # Delete items
        console.print(f"🗑️  Deleting items older than {days} days...")
        result = client.prune_unprioritized(days)

        deleted_count = result.get("deleted", 0)
        console.print(
            f"✅ Cleaned up [bold green]{deleted_count}[/bold green] old items"
        )

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)
