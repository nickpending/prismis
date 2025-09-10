"""Default configuration and context files for Prismis daemon."""

import os
import secrets
from pathlib import Path


DEFAULT_CONFIG_TOML = """# Prismis Configuration

[daemon]
fetch_interval = 30  # minutes between fetches
max_items_rss = 25  # maximum items to fetch from RSS feeds  
max_items_reddit = 50  # maximum items to fetch from Reddit sources
max_items_youtube = 10  # maximum items to fetch from YouTube (transcripts are expensive)
max_days_lookback = 30  # ignore content older than this

[llm]
provider = "openai"  # or "anthropic", "ollama"
model = "gpt-4.1-mini"  # cost-effective model for content analysis
api_key = "env:OPENAI_API_KEY"  # reads from environment variable

[reddit]
client_id = "env:REDDIT_CLIENT_ID"  # Reddit API client ID
client_secret = "env:REDDIT_CLIENT_SECRET"  # Reddit API client secret
user_agent = "prismis:local:v1.0 (by /u/prismis)"  # User agent for API requests

[notifications]
high_priority_only = true  # only notify for HIGH priority items
command = "terminal-notifier"  # notification command (Mac)

[api]
key = "{api_key}"  # API key for REST endpoints (auto-generated)
"""

DEFAULT_CONTEXT_MD = """# Personal Context for Prismis

This file defines your interests and priorities for content analysis.
The LLM will use these to determine content priority (HIGH/MEDIUM/LOW).

## High Priority Topics

- AI/LLM breakthroughs, especially local models
- Rust systems programming, performance, WASM
- SQLite innovations, extensions, DuckDB

## Medium Priority Topics

- React/Next.js updates
- Python tooling (uv, ruff)
- Database design patterns

## Low Priority Topics

- General programming tutorials
- Cloud provider news

## Not Interested

- Crypto, blockchain, web3
- Gaming news
- Politics
"""


def ensure_config() -> None:
    """Create default configuration directory and files if they don't exist.

    Creates $XDG_CONFIG_HOME/prismis/ (or ~/.config/prismis/) with:
    - config.toml: Daemon configuration
    - context.md: Personal interest context for LLM
    """
    # XDG Base Directory support
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    config_dir = Path(xdg_config_home) / "prismis"

    # Create directory if it doesn't exist
    config_dir.mkdir(parents=True, exist_ok=True)

    # Create config.toml if it doesn't exist
    config_file = config_dir / "config.toml"
    if not config_file.exists():
        # Generate a random API key
        api_key = f"prismis-{secrets.token_hex(8)}"
        config_content = DEFAULT_CONFIG_TOML.format(api_key=api_key)
        config_file.write_text(config_content)
        print(f"Created {config_file}")
        print(f"Generated API key: {api_key}")
    else:
        print(f"Config already exists: {config_file}")

    # Create context.md if it doesn't exist
    context_file = config_dir / "context.md"
    if not context_file.exists():
        context_file.write_text(DEFAULT_CONTEXT_MD)
        print(f"Created {context_file}")
    else:
        print(f"Context already exists: {context_file}")


if __name__ == "__main__":
    ensure_config()
