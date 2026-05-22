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

**This is early software that works but has rough edges.** It's been in daily use for 3 months, handling 500+ items/day across 30+ sources, but expect quirks. Version 0.3.0 adds semantic search, remote TUI mode, smart archival, and static file monitoring. Each release gets more polished.

## ✨ Features

- 🚀 **Instant TUI** - Launches in <100ms with gorgeous Bubbletea interface
- 🧠 **AI-Powered Priority** - LLM analyzes content against YOUR interests (HIGH/MEDIUM/LOW)
- 🔬 **Deep Extraction** - Two-tier LLM synthesis on HIGH-priority items: Counterintuitive findings, buried ledes, "so what" actions, quotable lines
- 👍 **Preference Learning** - Upvote/downvote content to train AI prioritization over time
- 🔍 **Semantic Search** - Local embeddings enable vector similarity search across all content
- 🤖 **Context Assistant** - LLM analyzes flagged items to suggest context.md improvements with gap analysis
- 🌐 **Remote TUI Mode** - Access your daemon from anywhere with incremental sync
- 🎨 **Fabric Integration** - 200+ AI analysis patterns with tab completion (`:fabric <TAB>` to browse)
- 🔒 **Local-First** - Your data never leaves your machine. SQLite + Go binary
- 📡 **Multi-Source** - RSS/Atom feeds, Reddit (API optional), YouTube transcripts, static files
- 🎯 **Personal Context** - Define what matters in simple markdown
- 🔔 **Smart Notifications** - Desktop alerts only for HIGH priority content
- 🌐 **Web Interface** - Beautiful daily briefing accessible from any device on your network
- 🗄️ **Smart Archival** - Priority-aware retention policy automatically manages old content
- ⚡ **Zero-Ops** - No Docker, no PostgreSQL, no cloud services needed

## 🎬 Quick Start

```bash
# Install everything (macOS/Linux)
make install

# Upgrading from an older install? Bring config to current shape:
prismis-daemon migrate-config

# Smoke-check that everything is wired up (config + LLM services + sources)
prismis-daemon verify

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
prismis           # Launch instantly (local mode)
prismis --remote  # Remote mode with incremental sync from server daemon
```

**Essential Keys:**
- `1/2/3` - View HIGH/MEDIUM/LOW priority content
- `j/k` - Navigate up/down (vim-style)
- `Enter` - Read full article
- `+`/`-` - Upvote/downvote content (trains AI prioritization)
- `i` - Flag item as interesting (for context analysis)
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
- `:context suggest` - Get LLM topic suggestions from flagged items (requires flagging with `i`)
- `:context edit` - Open context.md in $EDITOR
- `:context review` - Show count of flagged items ready for analysis
- `:audio` - Generate audio briefing from HIGH priority items (requires lspeak)
- `:export sources` - Copy all configured sources to clipboard for backup
- `:mark` - Mark article as read/unread
- `:copy` - Copy article content
- `:prune` - Remove unprioritized items (with y/n confirmation)
- `:prune!` - Force remove without confirmation
- `:prune 7d` - Remove items older than 7 days
- `:help` - Show all available commands

### Context Assistant Workflow

Improve your context.md over time by flagging interesting unprioritized items:

1. **Flag interesting items** - Press `i` on unprioritized content that should have matched your topics but didn't
2. **Get suggestions** - Run `:context suggest` to analyze flagged items with LLM
3. **Review analysis** - Suggestions copied to clipboard with gap analysis:
   - **Add**: New topic area not covered
   - **Expand**: Existing topic too narrow
   - **Narrow**: Existing topic too broad
   - **Split**: One topic covering unrelated things
4. **Update context.md** - Run `:context edit` or manually update based on suggestions
5. **Repeat** - As you flag more items, patterns emerge and your context improves

The LLM studies your existing topic style (length, phrasing, tone) and matches it in suggestions.

### Managing Sources

```bash
# RSS/Atom feeds
prismis-cli source add https://news.ycombinator.com/rss

# Reddit (fetches top comments for richer analysis)
# Note: Comment fetching increases LLM analysis costs. Configure max_comments in config.toml
prismis-cli source add reddit://programming

# YouTube channels (extracts transcripts)
prismis-cli source add youtube://UC9-y-6csu5WGm29I7JiwpnA

# Static file monitoring (GitHub changelogs, release notes)
prismis-cli source add file:///path/to/CHANGELOG.md

# List all sources
prismis-cli source list

# Remove a source
prismis-cli source remove 3

# Semantic search across all content
prismis-cli search "local-first database innovations"

# Backfill deep-extraction synthesis on HIGH-priority items
prismis-cli extract --priority high --limit 3   # Process 3 items
prismis-cli extract --priority high --limit 100 # Larger batch

# Retry failed LLM analysis
prismis-cli analyze repair

# Clean up unprioritized content
prismis-cli prune               # Remove all unprioritized items
prismis-cli prune --days 7      # Remove unprioritized items older than 7 days

# View system statistics
prismis-cli statistics          # Content and source counts
```

### Archival Policy

Prismis automatically archives old content based on priority (configurable in `~/.config/prismis/config.toml`):

**Retention Windows:**
- **HIGH priority**: Kept until read, then archived after 30 days
- **MEDIUM priority**: 14 days unread / 30 days read
- **LOW priority**: 7 days unread / 30 days read

**Protected from archival:**
- Favorited items (⭐)
- Items with notes
- Unread HIGH priority items (kept indefinitely)

This ensures important content stays visible until you've seen it, while automatically cleaning up old read content.

### CLI Automation

Query and export content for automation workflows:

```bash
# Get a single entry (formatted display)
prismis-cli get <entry-id>

# Get raw content for piping to external tools
prismis-cli get <entry-id> --raw | fabric --pattern extract_wisdom

# List recent entries
prismis-cli list --limit 10
prismis-cli list --priority high --unread

# Export in JSON or CSV format
prismis-cli export --format json > backup.json
prismis-cli export --format csv --priority high > high-priority.csv
```

**LLM-Friendly JSON Output:**

Most CLI commands support `--json` for machine-readable output, perfect for AI agent integration:

```bash
# Get structured data for LLM processing
prismis-cli statistics --json              # System metrics
prismis-cli search "topic" --compact --json # Search results (compact: 24x smaller)
prismis-cli list --json                    # Content list
prismis-cli get <id> --json                # Entry metadata
prismis-cli source list --json             # All sources
prismis-cli source add <url> --json        # Source creation response

# Example: Let AI analyze your reading backlog
prismis-cli list --priority high --unread --json | llm "Summarize my reading queue"
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

# Post-deploy smoke check (config valid, LLM services reachable, ≥1 source)
prismis-daemon verify

# Idempotent config migration (after upgrading from older Prismis)
prismis-daemon migrate-config
```

**Note**: You can run the daemon however you prefer - in a tmux session, as a background process, or with any process manager you're comfortable with. It's just a regular Python program.

`prismis-daemon verify` is read-only and safe to run against a production daemon. It checks four things and exits 0 on all-pass / 1 on any failure: config validity, light LLM service reachable, deep LLM service reachable (or "not configured"), at least one active source.

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

### LLM Configuration (Dual-Service)

Prismis routes LLM calls through two services so routine work and deep synthesis can use different models. Provider routing is handled by `llm-core` via `~/.config/llm-core/services.toml`; the daemon only references service names.

```toml
# ~/.config/prismis/config.toml
[llm]
light_service = "prismis-openai"        # required — used for priority/summary/context analysis
deep_service  = "prismis-openai-deep"   # optional — second-tier synthesis on HIGH items
auto_extract  = "high"                  # "high" | "medium" | "none" — gate for auto deep extraction
```

The light service handles every routine call (fetch-cycle priority, summarization, context analyzer). The deep service runs the second-tier synthesis prompt that produces the Counterintuitive / Buried lede / So what / Pushback sections plus quotables. When `deep_service` is unset, the daemon runs in light-only mode (graceful degradation — deep extraction failures never block storage).

Service definitions live in `~/.config/llm-core/services.toml`, where you map service names to providers, models, and API keys. Examples:

```toml
# ~/.config/llm-core/services.toml
[services.prismis-openai]
provider = "openai"
model    = "gpt-4o-mini"
key      = "openai"

[services.prismis-openai-deep]
provider = "openai"
model    = "gpt-5-mini"        # reasoning-class model recommended for deep synthesis
key      = "openai"

[services.prismis-anthropic]
provider = "anthropic"
model    = "claude-3-haiku-20240307"
key      = "anthropic"

[services.prismis-ollama]
provider = "ollama"
model    = "llama2"
base_url = "http://localhost:11434"
```

API keys are resolved by `apiconf` from `~/.config/apiconf/config.toml`; `key = "openai"` references the entry named `openai` there. Run `prismis-daemon migrate-config` once after upgrading from a pre-iter-12 install — it idempotently rewrites the config to dual-service shape and adds the `[services.prismis-openai-deep]` stub.

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

The daemon exposes a REST API for custom integrations and the web interface.

**Full API Documentation:** See [API.md](API.md) for complete endpoint reference, authentication, request/response formats, and client examples.

**Quick Examples:**

```bash
# Get last 24 hours of content (daily briefing)
curl -H "X-API-Key: your-api-key" "http://localhost:8000/api/entries?since_hours=24"

# Get high priority items only
curl -H "X-API-Key: your-api-key" "http://localhost:8000/api/entries?priority=high&since_hours=24"

# Mark item as read
curl -X PATCH -H "X-API-Key: your-api-key" \
  -H "Content-Type: application/json" \
  -d '{"read": true}' \
  "http://localhost:8000/api/entries/CONTENT_ID"

# Get context suggestions from flagged items
curl -X POST -H "X-API-Key: your-api-key" \
  "http://localhost:8000/api/context"
```

**API Key:** Found in `~/.config/prismis/config.toml` under `[api] -> api_key`

**Interactive Docs:** http://localhost:8000/docs (Swagger UI)

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

- **[API Reference](API.md)** - Complete API documentation for building custom clients
- **[prismis.io/docs](https://prismis.io/docs)** - Detailed setup guides and tutorials
- **[Interactive API Docs](http://localhost:8000/docs)** - Swagger UI (when daemon running)

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

**Current limitations in v0.3.0-alpha:**

- **Daemon process management** - Manual start/stop (workaround: use tmux or `prismis-daemon &`)
- **YouTube age-gating** - Some videos fail to extract transcripts (workaround: add RSS feed directly)
- **Fabric errors** - May timeout on very long content (workaround: use shorter patterns like `summarize`)

**What works well:**
- ✅ RSS/Reddit/YouTube/file ingestion with full content extraction
- ✅ Reddit comment enrichment for richer LLM analysis
- ✅ Semantic search with local embeddings and vector similarity
- ✅ Remote TUI mode with incremental sync from server daemon
- ✅ Priority-aware archival policy for automated retention
- ✅ Clear API naming (`/api/entries` with consistent error codes)
- ✅ CLI automation with single-command invocation and piping support
- ✅ Fabric integration with 200+ patterns and tab completion
- ✅ Web interface with mobile-responsive daily briefing
- ✅ Audio briefing generation with Jarvis personality
- ✅ Instant TUI launch (<100ms) and navigation

## 🎯 Roadmap

**v0.3.0** (Current):
- [x] Semantic search with local embeddings
- [x] Remote TUI mode with incremental sync
- [x] Priority-aware archival policy
- [x] Static file monitoring for changelogs
- [x] CLI automation improvements

**v0.4.0** (Next):
- [ ] Enhanced TUI filtering (date range, source, topics)
- [ ] Daemon process management (systemd/launchd)
- [ ] Better source error handling and recovery
- [ ] Export markdown format for Obsidian integration

**v1.0.0** (Future):
- [ ] MCP server for AI agent queries
- [ ] Papers support (arxiv RSS, PDF ingestion)
- [ ] Manual content ingestion (one-off URLs/PDFs)
- [ ] Performance optimizations and full test coverage

## 📄 License

[MIT](LICENSE) - Use it, fork it, make it yours.

## 🙏 Acknowledgments

Built with amazing tools:
- [Bubbletea](https://github.com/charmbracelet/bubbletea) - Delightful TUI framework
- [llm-core](https://github.com/nickpending/llm-core) - Single-turn LLM abstraction with service-based routing
- [uv](https://github.com/astral-sh/uv) - Blazing fast Python package manager

---

<div align="center">
  
  **Stop reading everything. Start reading what matters.**
  
  [Get Started](https://prismis.io) | [Star on GitHub](https://github.com/nickpending/prismis)

</div>