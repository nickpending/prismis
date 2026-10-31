# Prismis

Prismis reads your RSS feeds, subreddits, YouTube channels and Hacker News, scores every item
against a plain-markdown description of what you care about, and shows you the few that matter:
in a terminal UI, a web page, a Mac menu-bar app, or a CLI built for scripts and agents.

[![Release](https://img.shields.io/github/v/release/nickpending/prismis?style=flat)](https://github.com/nickpending/prismis/releases)
[![Status](https://img.shields.io/badge/status-alpha-orange?style=flat)](#status)
[![License](https://img.shields.io/badge/license-MIT-blue.svg?style=flat)](LICENSE)

## Quick Start

On macOS with Homebrew (for Linux or a source checkout, see [Installation](#installation)):

```bash
# Install. Homebrew loads a third-party tap only after you trust it.
brew tap nickpending/prismis
brew trust nickpending/prismis   # unknown command? run `brew update` first
brew install prismis-daemon prismis-cli prismis-tui

# Set your LLM key (the default config uses OpenRouter)
mkdir -p ~/.config/prismis
echo 'OPENROUTER_API_KEY=sk-your-key-here' >> ~/.config/prismis/.env
chmod 600 ~/.config/prismis/.env

# First run: writes ~/.config/prismis/config.toml, then exits
prismis-daemon

# Tell Prismis what you care about: print an interview prompt, paste it into any
# chatbot, answer its questions, then save the context.md it returns to
# ~/.config/prismis/context.md. It also gives you `prismis-cli source add` lines to run.
# (The sample context.md written on first run is a placeholder until you replace it.)
prismis-cli context bootstrap

# Start the daemon and keep it running; it creates the database on its first start
brew services start prismis-daemon

# Add your sources (the bootstrap answer lists the ones you named)
prismis-cli source add https://simonwillison.net/atom/everything/
prismis-cli source add reddit://rust
prismis-cli source add youtube://UCsBjURrPoezykLs9EqgamOA

# Smoke-check that everything is wired up (config + LLM services + sources + context.md)
prismis-daemon verify

# Launch the TUI
prismis
```

The first fetch runs as soon as the daemon starts, then every 30 minutes.

## How it works

```
sources ──► fetch ──► LLM summary + priority ──► SQLite ◄── TUI / web / CLI / API
                         (your context.md)          │
                                                    └──► menu-bar app (macOS, new HIGH items)
```

The daemon fetches new items, pulls the full article (and the top comments for Reddit and Hacker
News), and asks an LLM to summarize each one and rate it HIGH, MEDIUM or LOW against your
`context.md`. With a deep-synthesis service configured, HIGH items also get a second pass: what's counterintuitive, the buried lede,
what to do about it, and the best quotes. Your 👍/👎 votes feed back into how items are scored,
and old MEDIUM and LOW items are archived on a schedule while HIGH items are kept.

Everything is stored in SQLite on your machine. Item text is sent to the LLM provider you
configure (OpenRouter by default, or any OpenAI-compatible endpoint, including a local one).

## Reading

**Terminal.** `prismis` opens the TUI. `1`/`2`/`3` show HIGH, MEDIUM and LOW, `j`/`k` move, `Enter`
reads, `+`/`-` vote, `K` cycles the kind filter, `S` manages sources, `:` opens command mode
(`:fabric <pattern>`, `:audio`, `:context suggest`, `:prune`) and `?` lists every key.
To read from a daemon on another machine, set `[remote]` in `config.toml` or run
`prismis --remote http://server:8989`.

**Web.** The daemon serves a mobile-friendly daily briefing at `http://localhost:8989`: the last 24
hours, top must-reads, voting, an article detail panel with the deep synthesis, and a generated
audio briefing. To reach it from your phone, set `host = "0.0.0.0"` under `[api]` in
`config.toml`; the page asks for the API key from that file.

**Menu bar (macOS).** The Prismis app sits in the menu bar and shows a dot when HIGH items have
arrived since you last looked. Its panel lists the newest five unread HIGH items, and clicking one
opens its detail panel in the web view. It talks to the same daemon the TUI does, local or
`[remote]`, and never sends notifications. See [Installation](#menu-bar-app-macos-from-source).

**CLI.** `prismis-cli` lists, searches, exports and manages everything, and most commands take
`--json` for scripts and agents:

```bash
prismis-cli list --priority high --unread
prismis-cli search "prompt injection" --kind research,tutorial
prismis-cli get <id> --raw | fabric --pattern extract_wisdom
prismis-cli list --priority high --unread --json | llm "Summarize my reading queue"
```

`prismis-cli --help` lists every command.

## Sources

```bash
prismis-cli source add https://example.com/feed.xml                  # RSS or Atom
prismis-cli source add https://news.ycombinator.com/rss              # HN, read with its discussion
prismis-cli source add reddit://programming                          # a subreddit, with top comments
prismis-cli source add youtube://UC9-y-6csu5WGm29I7JiwpnA            # a channel's transcripts
prismis-cli source add https://raw.githubusercontent.com/o/r/main/CHANGELOG.md   # a file, one item per change
prismis-cli source add --naughty https://example.com/feed.xml        # fetched with a browser user-agent
prismis-cli source list
```

Use `--naughty` for a site that refuses prismis's own user-agent — its add fails, or its
items arrive title-only with `HTTP 403`; `prismis-cli source update <id> --naughty` (or
`--no-naughty`) sets it on a source you already have.

Every item is also tagged with one of ten kinds (release, news, analysis, vulnerability,
incident, research, tutorial, experience, question, humor) that the TUI, web view, CLI and API can
filter on.

## Configuration

`~/.config/prismis/config.toml` is written on first run with working defaults; keys live in
`~/.config/prismis/.env`, never in the config itself. [docs/configuration.md](docs/configuration.md)
covers LLM services (including a separate deep-synthesis model and local servers), Reddit
credentials, audio briefings, archival windows, LAN access and remote clients.

## API

The daemon's REST API on port 8989 backs the web view and is open to your own tools; every `/api`
route needs the `X-API-Key` header. See [API.md](API.md), or the live Swagger docs at
`http://localhost:8989/docs`.

## Installation

### Homebrew (macOS)

Each component installs on its own, so a laptop that only reads can skip the daemon:

```bash
brew tap nickpending/prismis
brew trust nickpending/prismis   # unknown command? run `brew update` first
brew install nickpending/prismis/prismis-tui     # the terminal UI, for reading
brew install nickpending/prismis/prismis-cli     # the CLI, for agents and scripts
brew install nickpending/prismis/prismis-daemon  # the daemon, for the machine that fetches
```

`brew upgrade` picks up every tagged release.

### Menu-bar app (macOS, from source)

The app is not notarized, so it builds and signs itself on your Mac instead of installing from
the tap. Needs the Xcode Command Line Tools (`xcode-select --install`).

```bash
git clone https://github.com/nickpending/prismis.git
cd prismis
make install-menubar    # builds, self-signs and installs ~/Applications/Prismis.app
open ~/Applications/Prismis.app
```

It reads `~/.config/prismis/config.toml`: the `[remote]` url and key when set, otherwise the local
daemon with the `[api]` key. Launch at Login is off until you turn it on in its menu. The first
poll of a daemon on your LAN asks for Local Network permission.

### From source (macOS or Linux)

Needs Go 1.26+, Python 3.14+ and [uv](https://github.com/astral-sh/uv).

```bash
git clone https://github.com/nickpending/prismis.git
cd prismis
make install            # TUI, daemon, CLI with its local-mode commands, and a .env template
```

On a machine that only talks to a remote daemon, `make install-cli` installs the client-only CLI.
Optional extras: [Fabric](https://github.com/danielmiessler/fabric) for `:fabric` patterns and
[lspeak](https://github.com/nickpending/lspeak) for audio briefings.

## Development

```bash
make test       # every unit's own checks, the command CI runs
make dev        # one daemon fetch cycle from source
make dev-tui    # the TUI from source
```

`make test` runs ruff, ruff format, mypy and pytest in `daemon/`, `cli/` and `packaging/homebrew/`;
gofmt, go vet, staticcheck and go test in `tui/`; and, on a Mac, swift format, swift build and swift
test for the menu-bar app in `macos/`. It runs every check and names each one that failed. It needs
Go, uv and Python, plus Xcode or the Command Line Tools for the menu-bar app's checks on a Mac;
elsewhere it reports `macos` skipped. Issues and pull requests are welcome at
[github.com/nickpending/prismis/issues](https://github.com/nickpending/prismis/issues).

## Status

Alpha. It runs every day for its author, releases ship through Homebrew, and rough edges are
tracked as [issues](https://github.com/nickpending/prismis/issues).

## License

[MIT](LICENSE)
