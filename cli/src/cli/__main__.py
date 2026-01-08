"""Prismis CLI - Command-line interface for managing content sources."""

import os
import sys
from pathlib import Path
from typing import Annotated

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

from cli import (  # noqa: E402
    analyze,
    archive,
    embeddings,
    export,
    get,
    list,
    prune,
    report,
    search,
    source,
    statistics,
)
from cli.remote import set_remote_url  # noqa: E402

app = typer.Typer(
    name="prismis-cli",
    help="Prismis CLI - Manage content sources and configuration",
    add_completion=False,
)


@app.callback()
def main_callback(
    remote: Annotated[
        str | None,
        typer.Option("--remote", help="Remote daemon URL (e.g., http://server:8989)"),
    ] = None,
) -> None:
    """Prismis CLI - Manage content sources and configuration."""
    if remote:
        set_remote_url(remote)


# Add multi-command modules as sub-typers
app.add_typer(source.app, name="source", help="Manage content sources")
app.add_typer(prune.app, name="prune", help="Clean up unprioritized content")
app.add_typer(report.app, name="report", help="Generate content reports")
app.add_typer(archive.app, name="archive", help="Archive management")
app.add_typer(
    embeddings.app, name="embeddings", help="Semantic search index management"
)
app.add_typer(analyze.app, name="analyze", help="Content analysis and repair")

# Add single-command modules as direct commands
app.command(name="get", help="Retrieve content entries")(get.get)
app.command(name="list", help="List content entries")(list.list)
app.command(name="export", help="Export content to JSON/CSV")(export.export)
app.command(name="search", help="Search content semantically")(search.search)
app.command(name="statistics", help="Display system-wide statistics")(
    statistics.statistics
)


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
