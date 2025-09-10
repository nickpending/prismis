# prismis

> Your AI-powered research department that never sleeps

**prismis** reads everything so you don't have to. It continuously monitors RSS feeds, Reddit, and YouTube, uses LLM analysis to surface what matters to you personally, then delivers it through a blazing-fast terminal interface.

## Quick Start

```bash
# Install (macOS/Linux)
make install

# Add your sources
prismis-cli source add https://simonwillison.net/atom/everything/
prismis-cli source add reddit://rust

# Launch the TUI
prismis
```

Press `1` for HIGH priority items. Press `m` to mark as read. That's it.

## Why prismis?

- **Zero-ops local** - SQLite + Go binary. No Docker, no PostgreSQL, no cloud
- **Actually intelligent** - LLM analyzes against YOUR interests, not generic algorithms  
- **Instant TUI** - Sub-100ms launch. Read without leaving your terminal
- **Privacy-first** - Your reading habits never leave your machine
- **XDG compliant** - Proper Unix citizen that respects your system

## Installation

Requires: macOS/Linux, Go 1.21+, Python 3.13

```bash
make install
```

Set your API key:
```bash
export OPENAI_API_KEY="sk-..."  # or use Anthropic
```

Tell it what you care about (`~/.config/prismis/context.md`):
```markdown
## High Priority Topics
- AI/LLM breakthroughs
- Rust performance improvements

## Not Interested  
- Crypto, blockchain, web3
```

## Usage

### Add Sources

```bash
# RSS/Atom feeds
prismis-cli source add https://example.com/feed.xml

# Subreddits (uses PRAW or public JSON API)
prismis-cli source add reddit://rust

# YouTube channels (extracts transcripts)
prismis-cli source add youtube://UC9-y-6csu5WGm29I7JiwpnA
```

### Read Content

```bash
prismis  # Launch TUI
```

**Keyboard:**
- `1/2/3` - HIGH/MEDIUM/LOW priority
- `4` or `*` - Favorites view
- `j/k` - Navigate  
- `Enter` - Read full text
- `m` - Mark read (disappears immediately)
- `f` - Toggle favorite (preserved even if source deleted)
- `r` - Refresh content
- `s` - Source management
- `?` - Help
- `q` - Quit

### Run Daemon

The daemon fetches content every 30 minutes:

```bash
# Test run
prismis-daemon --once

# Background service (macOS)
launchctl load ~/Library/LaunchAgents/com.prismis.daemon.plist
```

## Architecture

### Core Design Principles

1. **Daemon Owns All Writes** - The Python daemon is the single source of truth for database mutations. All changes (marking read, favoriting, etc.) go through the REST API to ensure consistency and validation.

2. **Multiple Access Modes** - The daemon exposes data through different interfaces for different consumers:
   ```
                       SQLite Database
                             ↑
                       Python Daemon
                             ↓
           ┌─────────────────┼─────────────────┐
           ↓                 ↓                 ↓
       REST API         Future: MCP       Direct Reads
     (localhost:8989)                      (TUI only)
           ↓                 ↓                 ↓
        Clients         AI Agents          Fast TUI
   ```

3. **Clean Separation** - Each component has a single responsibility:
   - Daemon: Fetches content, analyzes with LLM, manages database
   - API: Controlled access to mutations 
   - TUI: Pure client for human consumption
   - CLI: Source management operations

### Data Flow

```
Internet → Fetchers → LLM Analysis → SQLite → TUI
           ↓          ↓               ↓        ↓
        (RSS,      (Your context)  (WAL mode) (Go/Bubbletea)
        Reddit,    (GPT-4o-mini)
        YouTube)
```

**Components:**
- `daemon/` - Python fetcher + analyzer (APScheduler + LiteLLM + FastAPI)
- `tui/` - Go terminal interface (Bubbletea + Lipgloss)
- `cli/` - Python CLI for source management (Typer)

## Development

```bash
make test      # Run tests
make build     # Build everything
make dev       # Run daemon once
```

**Key Files:**
- `~/.config/prismis/context.md` - Your interests (config)
- `~/.local/share/prismis/prismis.db` - Content database (XDG data)

## FAQ

**Q: Why not use an existing RSS reader?**  
A: They don't prioritize based on YOUR specific interests using AI.

**Q: Why terminal-based?**  
A: Because context switching to a browser breaks flow. Terminal is home.

**Q: Does it support $FEED_FORMAT?**  
A: If it's RSS/Atom, yes. PRs welcome for other formats.

**Q: Can I use local LLMs?**  
A: Yes, via LiteLLM. Set `provider = "ollama"` in config.

## Status

This is a personal tool that works for me. It might work for you too.

Built because existing tools are either too simple (basic RSS) or too complex (self-hosted web apps). Sometimes you just want to read the news that matters without leaving your terminal.

## License

MIT