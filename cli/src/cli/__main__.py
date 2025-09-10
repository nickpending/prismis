"""Prismis CLI - Command-line interface for managing content sources."""

import typer
from pathlib import Path
import sys

# Add daemon src to path so we can import storage/database modules
daemon_src = Path(__file__).parent.parent.parent.parent / "daemon" / "src"
sys.path.insert(0, str(daemon_src))

from cli import source  # noqa: E402

app = typer.Typer(
    name="prismis-cli",
    help="Prismis CLI - Manage content sources and configuration",
    add_completion=False,
)

# Add source management commands
app.add_typer(source.app, name="source", help="Manage content sources")


def main() -> None:
    """Main entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
