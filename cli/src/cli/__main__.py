"""Prismis CLI - Command-line interface for managing content sources."""

import os
import sys
from pathlib import Path

import typer
from dotenv import load_dotenv

# Load environment variables from ~/.config/prismis/.env
config_home = os.getenv("XDG_CONFIG_HOME", str(Path.home() / ".config"))
dotenv_path = Path(config_home) / "prismis" / ".env"
if dotenv_path.exists():
    load_dotenv(dotenv_path)

# Add daemon src to path so we can import storage/database modules
daemon_src = Path(__file__).parent.parent.parent.parent / "daemon" / "src"
sys.path.insert(0, str(daemon_src))

from cli import source, prune, report, get, list, export  # noqa: E402

app = typer.Typer(
    name="prismis-cli",
    help="Prismis CLI - Manage content sources and configuration",
    add_completion=False,
)

# Add command modules
app.add_typer(source.app, name="source", help="Manage content sources")
app.add_typer(prune.app, name="prune", help="Clean up unprioritized content")
app.add_typer(report.app, name="report", help="Generate content reports")
app.add_typer(get.app, name="get", help="Retrieve content entries")
app.add_typer(list.app, name="list", help="List content entries")
app.add_typer(export.app, name="export", help="Export content to JSON/CSV")


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
