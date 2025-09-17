"""Report generation commands."""

import typer
from rich.console import Console
from pathlib import Path
from typing import Optional
from .api_client import APIClient

app = typer.Typer()
console = Console()


@app.command()
def generate(
    period: str = typer.Argument("24h", help="Time period (e.g., '24h', '7d', '30d')"),
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a content report for the specified period.

    Args:
        period: Time period for report (e.g., '24h', '7d', '30d')
        output: Optional file path to save report to
    """
    try:
        client = APIClient()

        console.print(f"📊 Generating report for period: [bold]{period}[/bold]...")

        # Get report from API
        report = client.get_report(period)

        if not report:
            console.print("⚠️  No content found for the specified period")
            return

        # Either save to file or print to console
        if output:
            output.write_text(report)
            console.print(f"✅ Report saved to: [bold green]{output}[/bold green]")
        else:
            # Print report to console
            console.print("\n" + report)

    except RuntimeError as e:
        console.print(f"[red]✗ Error: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def daily(
    output: Optional[Path] = typer.Option(
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
    output: Optional[Path] = typer.Option(
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
    output: Optional[Path] = typer.Option(
        None, "--output", "-o", help="Save report to file"
    ),
) -> None:
    """Generate a monthly report (last 30 days).

    Args:
        output: Optional file path to save report to
    """
    generate("30d", output)
