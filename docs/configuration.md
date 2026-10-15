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

Votes do nothing on their own unless `[context] learn_from_votes = true`, and it is off by default.
Turned on, it feeds learned preferences into scoring and lets `auto_update_enabled` rewrite
context.md once enough votes accumulate (`auto_update_min_votes`, `auto_update_interval_days`),
keeping `backup_count` backups. It is off because the current model credits a vote to the topics
an item matched, so downvoting one post can teach a dislike of a whole topic you asked for; see
issue #96.

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

## Notifications and audio

```toml
[notifications]
high_priority_only = true
command = "terminal-notifier"   # macOS

[audio]
provider = "system"      # free native TTS, or "elevenlabs" with ELEVENLABS_API_KEY set
# voice = "Rachel"
```

Audio briefings (`:audio` in the TUI, or the button in the web view) need
[lspeak](https://github.com/nickpending/lspeak): `uv tool install git+https://github.com/nickpending/lspeak.git`.
