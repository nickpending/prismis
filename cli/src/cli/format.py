"""Shared display formatting for CLI table output."""


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
