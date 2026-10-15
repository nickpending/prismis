"""Shared display formatting for CLI table output."""

from datetime import datetime


def format_priority(priority_val: str) -> str:
    """Wrap a priority label in the Rich color markup `list` and `search` share.

    Args:
        priority_val: An already-uppercased priority label (e.g. `(entry.get(
            "priority") or "N/A").upper()`). This only adds color markup; it
            does not normalize or validate the input.

    Returns:
        The label wrapped in `[red]`/`[yellow]`/`[green]` for HIGH/MEDIUM/LOW,
        or the label unchanged for any other value.
    """
    if priority_val == "HIGH":
        return f"[red]{priority_val}[/red]"
    elif priority_val == "MEDIUM":
        return f"[yellow]{priority_val}[/yellow]"
    elif priority_val == "LOW":
        return f"[green]{priority_val}[/green]"
    return priority_val


def format_kind(kind_val: str | None) -> str:
    """Render a content item's `kind` for the `list` and `search` tables.

    Args:
        kind_val: The item's `kind` field, e.g. `entry.get("kind")`. Unclassified
            items carry no kind, so this is `None` or an empty string.

    Returns:
        `kind_val` unchanged when it is truthy, otherwise `"-"`.
    """
    return kind_val if kind_val else "-"


def format_published(published_at: str | None) -> str:
    """Render an item's `published_at` (RFC3339, as the API sends it) for a table.

    Returns:
        "YYYY-MM-DD HH:MM" in the timestamp's own offset, "N/A" when there is none,
        or the value unchanged when it does not parse.
    """
    if not published_at:
        return "N/A"
    try:
        return datetime.fromisoformat(published_at).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return published_at
