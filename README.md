# Prismis

<div align="center">
  
  **Your AI-powered research department that never sleeps**
  
  [prismis.io](https://prismis.io) | [Documentation](https://prismis.io/docs) | [Discord](https://discord.gg/prismis)

  [![Go](https://img.shields.io/badge/Go-1.21+-00ADD8?style=flat&logo=go)](https://go.dev)
  [![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python)](https://python.org)
  [![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

</div>

---

**Prismis** transforms information overload into intelligence. It continuously monitors your RSS feeds, Reddit communities, and YouTube channels, uses AI to surface what matters to YOU personally, then delivers it through a blazing-fast terminal interface.

Think of it as having a research assistant who reads everything and only interrupts you for the important stuff.

## ✨ Features

- 🚀 **Instant TUI** - Launches in <100ms with gorgeous Bubbletea interface
- 🧠 **AI-Powered Priority** - LLM analyzes content against YOUR interests (HIGH/MEDIUM/LOW)
- 🔒 **Local-First** - Your data never leaves your machine. SQLite + Go binary
- 📡 **Multi-Source** - RSS/Atom feeds, Reddit (no API key needed), YouTube transcripts
- 🎯 **Personal Context** - Define what matters in simple markdown
- 🔔 **Smart Notifications** - Desktop alerts only for HIGH priority content
- ⚡ **Zero-Ops** - No Docker, no PostgreSQL, no cloud services needed

## 🎬 Quick Start

```bash
# Install everything (macOS/Linux)
make install

# Tell Prismis what you care about
cat > ~/.config/prismis/context.md << 'EOF'
## High Priority Topics
- AI/LLM breakthroughs, especially local models
- Rust systems programming innovations
- SQLite and database innovations

## Medium Priority Topics
- React/Next.js updates
- Developer tool releases

## Low Priority Topics
- General programming tutorials
- Conference announcements

## Not Interested
- Crypto, blockchain, web3
- Gaming news
- Politics
EOF

# Add your favorite sources
prismis-cli source add https://simonwillison.net/atom/everything/
prismis-cli source add reddit://rust
prismis-cli source add youtube://UCsBjURrPoezykLs9EqgamOA

# Launch the TUI
prismis
```

Press `1` for HIGH priority. Press `Enter` to read. Press `m` to mark as read. That's it.

## 🎮 Usage

### Terminal Interface

```bash
prismis  # Launch instantly
```

**Essential Keys:**
- `1/2/3` - View HIGH/MEDIUM/LOW priority content
- `j/k` - Navigate up/down (vim-style)
- `Enter` - Read full article
- `m` - Mark as read (disappears from view)
- `S` - Manage sources
- `?` - Show all keyboard shortcuts
- `q` - Quit

### Managing Sources

```bash
# RSS/Atom feeds
prismis-cli source add https://news.ycombinator.com/rss

# Reddit (works without API keys!)
prismis-cli source add reddit://programming

# YouTube channels (extracts transcripts)
prismis-cli source add youtube://UC9-y-6csu5WGm29I7JiwpnA

# List all sources
prismis-cli source list

# Remove a source
prismis-cli source remove 3
```

### Running the Daemon

The daemon fetches and analyzes content every 30 minutes:

```bash
# One-time fetch (testing)
prismis-daemon --once

# Run as background service (macOS)
make start-daemon

# Check daemon logs
tail -f ~/Library/Logs/prismis-daemon.log
```

## 🏗️ Architecture

Prismis uses a multi-process architecture optimized for responsiveness:

```
Internet Sources          Python Daemon           Go TUI
     │                         │                     │
     └──► Fetchers ──► LLM ──► SQLite (WAL) ◄────── Read
          (RSS/Reddit)  (AI)      │
                                  └──► Notifications
                                       (HIGH only)
```

- **Python Daemon**: Fetches content, analyzes with AI, manages database
- **Go TUI**: Lightning-fast terminal interface with instant launch
- **SQLite WAL**: Enables concurrent access without locks
- **LLM Analysis**: Uses your personal context to assign priorities

## 🔧 Installation

### Prerequisites

- **macOS or Linux**
- **Go 1.21+** for the TUI
- **Python 3.13+** for the daemon
- **OpenAI API key** (or Anthropic/Ollama for local models)

### Install from Source

```bash
git clone https://github.com/nickpending/prismis.git
cd prismis
make install

# Set your API key
export OPENAI_API_KEY="sk-..."  # Add to your shell profile
```

### Configuration

Prismis follows XDG standards:
- Config: `~/.config/prismis/`
- Data: `~/.local/share/prismis/`
- Logs: `~/Library/Logs/` (macOS)

## 🚀 Advanced Features

### Local LLM Support

Use Ollama or other local models:

```toml
# ~/.config/prismis/config.toml
[llm]
provider = "ollama"
model = "llama2"
api_base = "http://localhost:11434"
```

### Fabric Integration (Coming Soon)

Pipe content to fabric patterns for deeper analysis:

```bash
prismis get --high | fabric --pattern extract_wisdom
```

### API Access

The daemon exposes a REST API for custom integrations:

```bash
# Get high priority items
curl localhost:8989/api/content?priority=high

# Mark item as read
curl -X PUT localhost:8989/api/content/123/read
```

## 🧪 Development

```bash
# Run tests
make test

# Development mode
make dev        # Run daemon once
make dev-tui    # Run TUI with live reload

# Build binaries
make build
```

### Project Structure

```
prismis/
├── daemon/     # Python daemon (fetching, analysis, API)
├── tui/        # Go terminal interface  
├── cli/        # Python CLI for management
└── scripts/    # Installation and service scripts
```

## 📚 Documentation

Visit [prismis.io/docs](https://prismis.io/docs) for:
- Detailed setup guides
- API documentation
- Content source configuration
- Custom integration examples

## 🤝 Contributing

Prismis is open source and welcomes contributions! Check out our [Contributing Guide](CONTRIBUTING.md) to get started.

Some areas we'd love help with:
- Additional content sources (Mastodon, BlueSky, etc.)
- Enhanced LLM analysis patterns
- Cross-platform notification support
- TUI themes and customization

## 📈 Why Prismis?

**The Problem**: You have 50+ sources generating 500+ items daily. Current RSS readers show everything chronologically. You miss important stuff, waste time on noise.

**The Solution**: Prismis reads everything and uses AI to understand what matters to YOU specifically. High priority content triggers notifications. Medium priority waits in your TUI. Low priority is there if you're bored. Unprioritized items auto-cleanup.

**The Philosophy**: Your attention is precious. Software should protect it, not exploit it.

## 🎯 Roadmap

- [x] Core daemon with RSS/Reddit/YouTube
- [x] Instant-launch TUI (<100ms)
- [x] LLM prioritization pipeline
- [x] Desktop notifications
- [ ] Neovim-style command mode (`:` commands)
- [ ] Fabric integration for content analysis
- [ ] Daily digest generation
- [ ] MCP server for AI agents
- [ ] Mobile app (iOS/Android)

## 📄 License

MIT - Use it, fork it, make it yours.

## 🙏 Acknowledgments

Built with amazing tools:
- [Bubbletea](https://github.com/charmbracelet/bubbletea) - Delightful TUI framework
- [LiteLLM](https://github.com/BerriAI/litellm) - Universal LLM interface
- [uv](https://github.com/astral-sh/uv) - Blazing fast Python package manager

---

<div align="center">
  
  **Stop reading everything. Start reading what matters.**
  
  [Get Started](https://prismis.io) | [Star on GitHub](https://github.com/nickpending/prismis)

</div>