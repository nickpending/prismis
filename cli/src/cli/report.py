"""Report generation commands."""

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markup import escape

from .api_client import APIClient

app = typer.Typer()
console = Console()

# The API's `since_hours` bound (daemon GET /api/entries: 1-720).
MAX_REPORT_HOURS = 720
MEDIUM_CAP = 10
LOW_CAP = 15
_LOW_SUMMARY_CHARS = 150
_PERIOD_RE = re.compile(r"^(\d+)([hd])$")


def parse_period(period: str) -> int:
    """Parse a period like '48h' or '7d' into hours.

    Raises:
        ValueError: If the format is not `<n>h` or `<n>d`, or exceeds 720 hours
    """
    match = _PERIOD_RE.match(period.strip().lower())
    if not match or int(match.group(1)) < 1:
        raise ValueError(
            f"Invalid period '{period}': use <n>h or <n>d (e.g. '24h', '7d')"
        )
    hours = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
    if hours > MAX_REPORT_HOURS:
        raise ValueError(
            f"Period '{period}' is {hours} hours; the limit is "
            f"{MAX_REPORT_HOURS} hours (30 days)"
        )
    return hours


def _time_ago(published_at: str | None, now: datetime) -> str:
    """Human-readable age of an ISO timestamp; empty when absent or unparseable."""
    if not published_at:
        return ""
    try:
        published = datetime.fromisoformat(published_at)
    except ValueError:
        return ""
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    seconds = (now - published).total_seconds()
    hours = int(seconds / 3600)
    if hours < 1:
        return f"{int(seconds / 60)} minutes ago"
    if hours < 24:
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(hours / 24)
    return f"{days} day{'s' if days != 1 else ''} ago"


def render_report(
    items: list[dict[str, Any]], period_hours: int, generated_at: datetime
) -> str:
    """Render list-shaped entries as a markdown report.

    Items are grouped by priority; unprioritized items are left out. High shows
    every item, medium the first 10, low the first 15, each followed by an
    'and N more' line when capped.
    """
    high = [i for i in items if i.get("priority") == "high"]
    medium = [i for i in items if i.get("priority") == "medium"]
    low = [i for i in items if i.get("priority") == "low"]

    def meta(item: dict[str, Any]) -> str:
        return f"{item.get('source_name') or 'Unknown'} • " + _time_ago(
            item.get("published_at"), generated_at
        )

    lines = [f"# Daily Intelligence Brief - {generated_at.strftime('%B %d, %Y')}", ""]

    if high:
        lines += [f"## 🔴 High Priority Developments ({len(high)})", ""]
        for item in high:
            lines += [
                f"### {item.get('title', '')}",
                f"*{meta(item)}*  ",
                f"[Read More]({item.get('url', '')})",
                "",
            ]
            if item.get("summary"):
                lines.append(item["summary"])
            lines.append("")

    if medium:
        lines += [f"## 🟡 Medium Priority Updates ({len(medium)})", ""]
        for item in medium[:MEDIUM_CAP]:
            lines += [
                f"### {item.get('title', '')}",
                f"*{meta(item)}*  ",
                f"[Read More]({item.get('url', '')})",
                "",
            ]
            if item.get("summary"):
                lines.append(item["summary"])
            lines.append("")
        if len(medium) > MEDIUM_CAP:
            lines.append(f"*... and {len(medium) - MEDIUM_CAP} more items*")
        lines.append("")

    if low:
        lines += [f"## 🔵 Low Priority FYI ({len(low)})", ""]
        for item in low[:LOW_CAP]:
            lines += [
                f"**{item.get('title', '')}**  ",
                f"*{meta(item)} • [Link]({item.get('url', '')})*  ",
            ]
            summary = item.get("summary") or ""
            if summary:
                if len(summary) > _LOW_SUMMARY_CHARS:
                    summary = summary[:_LOW_SUMMARY_CHARS] + "..."
                lines.append(f"{summary}  ")
            lines.append("")
        if len(low) > LOW_CAP:
            lines.append(f"*... and {len(low) - LOW_CAP} more items*")
        lines.append("")

    counted = len(high) + len(medium) + len(low)
    lines += [
        "## 📊 Summary",
        f"In the last {period_hours} hours: {counted} items analyzed, "
        f"{len(high)} high priority, {len(medium)} medium, {len(low)} low",
    ]
    return "\n".join(lines)


@app.command()
def generate(
    period: str = typer.Argument("24h", help="Time period (e.g., '24h', '7d', '30d')"),
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a content report for the specified period.

    Args:
        period: Time period for report (e.g., '24h', '7d', '30d')
        output: Optional file path to save report to
    """
    try:
        try:
            hours = parse_period(period)
        except ValueError as e:
            console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
            raise typer.Exit(1) from e

        client = APIClient()

        console.print(f"📊 Generating report for period: [bold]{period}[/bold]...")

        items = client.get_content(since_hours=hours, view="list", limit=10000)

        if not items:
            console.print("⚠️  No content found for the specified period")
            return

        report = render_report(items, hours, datetime.now(timezone.utc))

        # Either save to file or print to console
        if output:
            output.write_text(report)
            console.print(f"✅ Report saved to: [bold green]{output}[/bold green]")
        else:
            # Print report to console
            console.print("\n" + report, markup=False, highlight=False, soft_wrap=True)

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1) from e


@app.command()
def daily(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a daily report (last 24 hours).

    Args:
        output: Optional file path to save report to
    """
    generate("24h", output)


@app.command()
def weekly(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a weekly report (last 7 days).

    Args:
        output: Optional file path to save report to
    """
    generate("7d", output)


@app.command()
def monthly(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a monthly report (last 30 days).

    Args:
        output: Optional file path to save report to
    """
    generate("30d", output)
