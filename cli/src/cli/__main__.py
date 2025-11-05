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

from cli import source, prune, report, get, list, export, archive, embeddings  # noqa: E402

app = typer.Typer(
    name="prismis-cli",
    help="Prismis CLI - Manage content sources and configuration",
    add_completion=False,
)

# Add multi-command modules as sub-typers
app.add_typer(source.app, name="source", help="Manage content sources")
app.add_typer(prune.app, name="prune", help="Clean up unprioritized content")
app.add_typer(report.app, name="report", help="Generate content reports")
app.add_typer(archive.app, name="archive", help="Archive management")
app.add_typer(
    embeddings.app, name="embeddings", help="Semantic search index management"
)

# Add single-command modules as direct commands
app.command(name="get", help="Retrieve content entries")(get.get)
app.command(name="list", help="List content entries")(list.list)
app.command(name="export", help="Export content to JSON/CSV")(export.export)


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
