# Configuration

The daemon writes `~/.config/prismis/config.toml` on its first run with working defaults, and loads
`~/.config/prismis/.env` at startup (a variable already in the environment wins). Data lives in
`~/.local/share/prismis/`. After upgrading from an older Prismis, `prismis-daemon migrate-config`
brings an old config up to date; it saves a backup first and running it twice changes nothing.

`prismis-daemon verify` checks the result: config validity, each LLM service's reachability, at
least one active source, and a well-formed `context.md`. It is read-only and exits non-zero on any
failure.

## Your interests: context.md

`~/.config/prismis/context.md` is what every item is scored against: plain markdown with High,
Medium and Low priority topic sections. `prismis-cli context bootstrap` prints an interview prompt
that produces one. Over time, `:context suggest` in the TUI proposes edits from the items you
upvoted; you decide what goes in.

The daemon reads context.md again at the start of every fetch cycle, so an edit applies to the next
cycle without a restart. If the file cannot be read, the cycle logs why and scores with the text it
last read; a missing file is the default context.

A vote never changes how items are scored and never changes context.md. `[context] learn_from_votes`
is the switch for carve-out proposals: when it is on, the daemon proposes context.md lines from
your downvotes, and nothing is written until you accept the specific proposal. Carve-outs are not
built yet, so the switch does nothing today. It is off by default, and votes only accumulate.
Other keys left under `[context]` from older releases are ignored with a logged note; delete them.

## LLM services

Every model call goes through a service: a `[services.<name>]` table with a `base_url`, a `model`
and, usually, an `api_key`. The `[llm]` section names which service does which job:

```toml
[llm]
light_service = "openrouter"   # required: summaries, priority and context analysis
# deep_service = "deep"        # optional: second-pass synthesis on HIGH items
# kind_service = "kind"        # optional: tags each item with one of ten kinds
auto_extract = "none"          # "none" | "high" | "all": which items get the deep pass
deep_extract_exclude = ["reddit"]

[services.openrouter]
base_url = "https://openrouter.ai/api/v1"
model    = "openai/gpt-5.4-nano"
api_key  = "env:OPENROUTER_API_KEY"
```

**Keys never go in config.toml.** `api_key` is always `env:NAME`, read from the environment or
`.env` when a call is made. A literal key, or a missing variable, fails with an error naming the
service and the variable. Leave `api_key` out for a keyless local server.

Every service except the kind service speaks the OpenAI chat-completions API, so any compatible
endpoint works:

```toml
# OpenAI directly (put OPENAI_API_KEY in .env)
[services.openrouter]
base_url = "https://api.openai.com/v1"
model    = "gpt-5-mini"
api_key  = "env:OPENAI_API_KEY"

# A reasoning-class model for the deep pass; also set deep_service = "deep" and auto_extract = "high"
[services.deep]
base_url = "https://openrouter.ai/api/v1"
model    = "anthropic/claude-sonnet-4.5"
api_key  = "env:OPENROUTER_API_KEY"

# Kind classification through OpenRouter's Decisions endpoint
[services.kind]
adapter  = "decisions"
base_url = "https://openrouter.ai/api/alpha/decisions"
model    = "typesafe/jev-1.13"
api_key  = "env:OPENROUTER_API_KEY"

# Any OpenAI-compatible local server
[services.local]
base_url = "http://localhost:8080/v1"
model    = "llama3"
```

Optional `app_title` and `app_url` set the attribution headers OpenRouter shows. With no
`deep_service` the daemon runs light-only; a deep-pass failure never blocks an item from being
stored. An item the kind service isn't confident about stays unclassified, and
`prismis-cli analyze kinds` classifies items analysed before a kind service existed.

## Fetching

```toml
[daemon]
fetch_interval = 30      # minutes between fetch cycles
max_items_rss = 25
max_items_reddit = 50
max_items_youtube = 10
max_days_lookback = 30   # ignore anything older
# fetch_allow_private_hosts = ["nas.lan"]   # article fetches refuse private addresses unless named here

[reddit]
max_comments = 5         # top comments read with each post (0 = all)
client_id = "env:REDDIT_CLIENT_ID"          # optional; Reddit is more reliable with API credentials
client_secret = "env:REDDIT_CLIENT_SECRET"

[hackernews]
max_comments = 5         # top comments read with each story (0 = all)
```

Comments make analysis richer and each LLM call larger, so they raise cost.

## Archival

Old items are archived by priority and read state. A window left out means never:

```toml
[archival]
enabled = true

[archival.windows]       # days
medium_unread = 30
medium_read = 14
low_unread = 14
low_read = 7
# high_unread, high_read: omitted, so HIGH items are never archived
```

`prismis-cli prune` removes items that never received a priority.

## Web view, API and remote clients

```toml
[api]
key = "..."              # generated on first run; the web view and API ask for it
host = "127.0.0.1"       # "0.0.0.0" to serve the web view and API to your network

[remote]                 # on a laptop that reads from a daemon elsewhere
url = "http://server:8989"
key = "the daemon's [api] key"
```

The daemon listens on port 8989. With `[remote]` set, both `prismis-cli` and the TUI talk to that
daemon instead of localhost; `prismis --remote http://server:8989` does the same for one run.

## Opening an article in an agent

`A` (or `:agent`) in the TUI, and `prismis-cli open <id>` from a shell, start an agent with a
prompt naming the article (id, title, URL, summary), the two `prismis-cli get` commands that fetch
the rest of it, and a starter line:

```toml
[agent]
target = "claude"        # "claude", "pi" or "herdr"; no default: unset, nothing starts
# command = "claude"     # default: the target's name; split on spaces, the prompt goes last
# kind = "claude"        # herdr only, default "claude": the agent herdr starts ("pi" too)
# prompt = "..."         # the starter line that ends the prompt
```

The title, URL and summary come from the article's publisher, so they reach the agent inside a
block the prompt marks as publisher data, never instructions, with control and invisible
formatting characters removed and `<` and `>` escaped. The prompt also tells the agent that what
the two `prismis-cli get` commands return is untrusted article content.

`command` is how a wrapper runs the engine: `command = "ember claude"` runs `ember claude <prompt>`.
The default `prompt` is "I just read this. Help me dig into it: what matters, what is
questionable, and where to go next."

`claude` and `pi` take over the terminal until you exit them; the TUI then comes back with the
outcome in its status line. `herdr` opens the agent in a pane to the right without taking focus, and
the next `A` on the same article focuses that agent instead of opening another. herdr picks the
executable itself from `kind`, so `command` does not apply to it.

`A` runs `prismis-cli open`, so it needs prismis-cli on your PATH, and prismis-cli reaches the
daemon over its API even when the TUI reads the database directly.

## New HIGH items on a Mac

The daemon sends no desktop notifications. On a Mac, the Prismis menu-bar app shows unread HIGH
items and marks the ones that arrived since you last looked; install it with
`make install-menubar`. It reads this machine's config.toml: the `[remote]` url and key when they
are set, otherwise the local daemon with the `[api]` key.

A `[notifications]` section left from an older release is ignored, with a warning in the daemon's
log at startup; delete it.

## Audio

```toml
[audio]
provider = "system"      # free native TTS, or "elevenlabs" with ELEVENLABS_API_KEY set
# voice = "Rachel"
```

Audio briefings (`:audio` in the TUI, or the button in the web view) need
[lspeak](https://github.com/nickpending/lspeak): `uv tool install git+https://github.com/nickpending/lspeak.git`.
