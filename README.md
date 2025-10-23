# Prismis

<div align="center">
  
  **Your AI-powered research department that never sleeps**
  
  [GitHub](https://github.com/nickpending/prismis) | [Issues](https://github.com/nickpending/prismis/issues) | [Roadmap](#roadmap)

  [![Status](https://img.shields.io/badge/Status-Alpha-orange?style=flat)](#status-alpha)
  [![Built with](https://img.shields.io/badge/Built%20with-Momentum-blueviolet?style=flat)](https://github.com/nickpending/momentum)
  [![Go](https://img.shields.io/badge/Go-1.21+-00ADD8?style=flat&logo=go)](https://go.dev)
  [![Python](https://img.shields.io/badge/Python-3.13+-3776AB?style=flat&logo=python)](https://python.org)
  [![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

  [![Buy Me A Coffee](https://img.shields.io/badge/Buy%20Me%20A%20Coffee-Support-FFDD00?style=flat&logo=buy-me-a-coffee&logoColor=black)](https://buymeacoffee.com/nickpending)

</div>

---

**Prismis** transforms information overload into intelligence. It continuously monitors your RSS feeds, Reddit communities, and YouTube channels, uses AI to surface what matters to YOU personally, then delivers it through a blazing-fast terminal interface.

Think of it as having a research assistant who reads everything and only interrupts you for the important stuff.

## Status: Alpha

**This is early software that works but has rough edges.** It's been in daily use for 3 months, handling 500+ items/day across 30+ sources, but expect quirks. Each release gets more polished.

## ✨ Features

- 🚀 **Instant TUI** - Launches in <100ms with gorgeous Bubbletea interface
- 🧠 **AI-Powered Priority** - LLM analyzes content against YOUR interests (HIGH/MEDIUM/LOW)
- 🎨 **Fabric Integration** - 200+ AI analysis patterns with tab completion (`:fabric <TAB>` to browse)
- 🔒 **Local-First** - Your data never leaves your machine. SQLite + Go binary
- 📡 **Multi-Source** - RSS/Atom feeds, Reddit (API optional), YouTube transcripts
- 🎯 **Personal Context** - Define what matters in simple markdown
- 🔔 **Smart Notifications** - Desktop alerts only for HIGH priority content
- 🌐 **Web Interface** - Beautiful daily briefing accessible from any device on your network
- ⚡ **Zero-Ops** - No Docker, no PostgreSQL, no cloud services needed

## 🎬 Quick Start

```bash
# Install everything (macOS/Linux)
make install

# Set your API keys
edit ~/.config/prismis/.env  # Add your OPENAI_API_KEY

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

Press `1` for HIGH priority. Press `Enter` to read. Press `:audio` for audio briefing. Press `:fabric <TAB>` to explore 200+ AI patterns. Or visit `http://localhost:8989` in any browser for the web interface.

## 🎮 Usage

### Web Interface

The daemon serves a beautiful web interface on your local network:

```bash
# Start the daemon
prismis-daemon &

# Access from any device on your network
# On Mac: http://localhost:8989
# On iPad/phone: http://YOUR_MAC_IP:8989
```

**Features:**
- 📱 Mobile-responsive daily briefing view (last 24 hours)
- ⭐ Top 3 Must-Reads with interest matching badges
- 🎧 Generate audio briefings directly from the UI
- 🔄 Auto-refreshes every 30 seconds
- 🎯 Priority filtering and mark-as-read

**Configuration for LAN access:**
```toml
# ~/.config/prismis/config.toml
[api]
host = "0.0.0.0"  # Allow access from other devices (default: 127.0.0.1)
```

### Terminal Interface

```bash
prismis  # Launch instantly
```

**Essential Keys:**
- `1/2/3` - View HIGH/MEDIUM/LOW priority content
- `j/k` - Navigate up/down (vim-style)
- `Enter` - Read full article
- `:` - Command mode (see below)
- `S` - Manage sources
- `?` - Show all keyboard shortcuts
- `q` - Quit

**Command Mode** (press `:` to enter):
- `:fabric <pattern>` - Run any of 200+ AI patterns (tab completion available)
  - `:fabric extract_wisdom` - Extract key insights
  - `:fabric summarize` - Create concise summary
  - `:fabric analyze_claims` - Fact-check claims
  - `:fabric explain_terms` - Explain technical terms
- `:audio` - Generate audio briefing from HIGH priority items (requires lspeak)
- `:export sources` - Copy all configured sources to clipboard for backup
- `:mark` - Mark article as read/unread
- `:copy` - Copy article content
- `:prune` - Remove unprioritized items (with y/n confirmation)
- `:prune!` - Force remove without confirmation
- `:prune 7d` - Remove items older than 7 days
- `:help` - Show all available commands

### Managing Sources

```bash
# RSS/Atom feeds
prismis-cli source add https://news.ycombinator.com/rss

# Reddit (fetches top comments for richer analysis)
# Note: Comment fetching increases LLM analysis costs. Configure max_comments in config.toml
prismis-cli source add reddit://programming

# YouTube channels (extracts transcripts)
prismis-cli source add youtube://UC9-y-6csu5WGm29I7JiwpnA

# List all sources
prismis-cli source list

# Remove a source
prismis-cli source remove 3

# Clean up unprioritized content
prismis-cli prune               # Remove all unprioritized items
prismis-cli prune --days 7      # Remove unprioritized items older than 7 days
```

### Running the Daemon

The daemon fetches and analyzes content every 30 minutes:

```bash
# One-time fetch (testing)
prismis-daemon --once

# Run continuously in background
prismis-daemon &

# Or run in foreground to see logs
prismis-daemon

# Stop the daemon
make stop
```

**Note**: You can run the daemon however you prefer - in a tmux session, as a background process, or with any process manager you're comfortable with. It's just a regular Python program.

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
- **LLM API key** - OpenAI, Anthropic, Groq, or local Ollama
- **Fabric** (optional) - AI content analysis patterns with tab completion
- **lspeak** (optional) - Text-to-speech for audio briefings (`uv tool install git+https://github.com/nickpending/lspeak.git`)

### Install from Source

```bash
git clone https://github.com/nickpending/prismis.git
cd prismis
make install

# Edit the .env file with your API keys
edit ~/.config/prismis/.env
# Change: OPENAI_API_KEY=sk-your-key-here
```

### Configuration

Prismis follows XDG standards:
- Config: `~/.config/prismis/`
- Data: `~/.local/share/prismis/`
- Logs: Wherever you redirect them (stdout/stderr by default)

**Security**: API keys are stored in `~/.config/prismis/.env` with 600 permissions (only you can read). Config references them with `api_key = "env:VARIABLE_NAME"` pattern for security.

## 🚀 Advanced Features

### Multiple LLM Providers

Prismis supports OpenAI, Anthropic, Groq, and Ollama:

```toml
# ~/.config/prismis/config.toml

# OpenAI (default)
[llm]
provider = "openai"
model = "gpt-4o-mini"
api_key = "env:OPENAI_API_KEY"  # References ~/.config/prismis/.env

# Anthropic Claude
[llm]
provider = "anthropic"
model = "claude-3-haiku-20240307"
api_key = "env:ANTHROPIC_API_KEY"

# Groq (fast inference)
[llm]
provider = "groq"
model = "mixtral-8x7b-32768"
api_key = "env:GROQ_API_KEY"

# Ollama (local models)
[llm]
provider = "ollama"
model = "llama2"
api_base = "http://localhost:11434"
# No API key needed for local Ollama
```

**Reddit API** (optional - improves reliability):
```bash
# Add to ~/.config/prismis/.env for better Reddit access
REDDIT_CLIENT_ID=your-reddit-client-id
REDDIT_CLIENT_SECRET=your-reddit-client-secret
```

### Fabric Integration

Built-in AI analysis using 200+ Fabric patterns. Tab completion helps you discover all available patterns:

```bash
# In TUI, select an article and press ':'
:fabric <TAB>              # Browse all available patterns
:fabric extract_wisdom     # Extract key insights
:fabric summarize          # Create concise summary
```

**Note**: Fabric analyzes the original/raw article content, not the AI-generated summary. This gives you deeper, unfiltered insights directly from the source material.

Results are automatically copied to your clipboard. Requires [Fabric](https://github.com/danielmiessler/fabric) to be installed.

### Audio Briefings

Generate Jarvis-style audio briefings from your HIGH priority content:

```bash
# In TUI
:audio                     # Generates MP3 briefing, saved to ~/.local/share/prismis/audio/

# Configure TTS provider (optional)
# ~/.config/prismis/config.toml
[audio]
provider = "system"        # Free macOS/Linux TTS (default)
# provider = "elevenlabs"  # Premium quality ($0.30/1K chars)
# voice = "your-voice-id"  # ElevenLabs voice ID (optional)
```

Requires [lspeak](https://github.com/nickpending/lspeak) for TTS generation:
```bash
uv tool install git+https://github.com/nickpending/lspeak.git

# For ElevenLabs (optional premium quality)
export ELEVENLABS_API_KEY=your-api-key
```

### API Access

The daemon exposes a REST API for custom integrations and the web interface:

```bash
# Get last 24 hours of content (daily briefing)
curl -H "X-API-Key: your-api-key" "http://localhost:8989/api/entries?since_hours=24"

# Get high priority items only
curl -H "X-API-Key: your-api-key" "http://localhost:8989/api/entries?priority=high&since_hours=24"

# Mark item as read
curl -X PATCH -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"read": true}' \
  "http://localhost:8989/api/entries/CONTENT_ID"

# Generate audio briefing
curl -X POST -H "X-API-Key: your-api-key" \
  "http://localhost:8989/api/audio/briefings"

# List all sources
curl -H "X-API-Key: your-api-key" "http://localhost:8989/api/sources"
```

**API Key:** Found in `~/.config/prismis/config.toml` under `[api] -> api_key`

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

## Known Issues & Limitations

**Current limitations in v0.1.0-alpha:**

- **Daemon process management** - Manual start/stop (workaround: use tmux or `prismis-daemon &`)
- **YouTube age-gating** - Some videos fail to extract transcripts (workaround: add RSS feed directly)
- **Report output path** - Must be explicitly configured in config.toml (no default)
- **Fabric errors** - May timeout on very long content (workaround: use shorter patterns like `summarize`)
- **Source error handling** - Failed sources show cryptic errors (manual: check logs in daemon output)

**What works well:**
- RSS feed ingestion with full content extraction
- Reddit fetching (works with or without API keys)
- LLM prioritization with personal context
- Instant TUI launch and navigation
- Fabric integration with 200+ patterns
- Daily report generation

## 🎯 Roadmap

**v0.2.0** (Next release):
- [ ] Fix daemon process management (systemd/launchd support)
- [ ] Better source error handling and recovery
- [ ] Improve YouTube transcript extraction reliability
- [ ] Default report output configuration
- [ ] Source health monitoring in TUI

**v0.3.0** (Future):
- [ ] MCP server for AI agent queries
- [ ] Papers support (arxiv RSS, PDF ingestion)
- [ ] Manual content ingestion (one-off URLs/PDFs)
- [ ] Link extraction from content
- [ ] Better duplicate detection
- [ ] Source categories and grouping

**v1.0.0** (Stable):
- [ ] Performance optimizations
- [ ] Full test coverage
- [ ] Documentation site
- [ ] Plugin system for custom sources

## 📄 License

[MIT](LICENSE) - Use it, fork it, make it yours.

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