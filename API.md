# Prismis API Documentation

Local-first content intelligence API for managing RSS feeds, Reddit, YouTube, and other content sources with semantic prioritization.

## Base URL

```
http://localhost:8989
```

The daemon binds `127.0.0.1` by default. Set `host = "0.0.0.0"` under `[api]` in the config to
serve it to your LAN.

## Authentication

All API endpoints (except `/health`) require an API key header:

```http
X-API-Key: your-api-key-from-config
```

Get your API key from `~/.config/prismis/config.toml`, where the daemon generates it on first run:

```toml
[api]
key = "prismis-..."
```

## Interactive Documentation

FastAPI provides auto-generated interactive docs:

- **Swagger UI**: http://localhost:8989/docs
- **ReDoc**: http://localhost:8989/redoc

## Linking to an Item

The web view at `/` opens one item's detail panel when its URL carries the item's id in the
fragment:

```
http://localhost:8989/#item=123e4567-e89b-12d3-a456-426614174000
```

- The panel opens at load, and on a later change to the fragment in an open page. It loads the
  item through [Get Entry Detail](#get-entry-detail); an id no item has shows the panel's
  load-failure state.
- The id must be the item's canonical lowercase UUID, the `id` the API returns. Anything else,
  uppercase or carrying extra characters included, is ignored: no panel opens and no request
  is sent.
- The link carries no API key. The page uses the key stored in the browser, and asks for one
  first when none is stored, then opens the item.
- The id travels in the fragment, so it is never sent to the daemon or written to its access log.
- Closing the panel removes the fragment without adding a history entry, so a reload shows the
  feed.

## Common Response Format

All endpoints return JSON with this structure:

```json
{
  "success": true,
  "message": "Operation completed",
  "data": { ... }
}
```

Error responses:

```json
{
  "success": false,
  "message": "Error description",
  "error_code": "RESOURCE_NOT_FOUND"
}
```

## Status Codes

- `200` - Success
- `201` - Created
- `400` - Bad Request
- `401` - Unauthorized (missing/invalid API key)
- `404` - Not Found
- `422` - Validation Error
- `500` - Server Error

---

## Endpoints

### Health Check

**`GET /health`**

Check daemon health and database connectivity. No authentication required.

**Response:**
```json
{
  "status": "healthy",
  "database": "connected"
}
```

---

## Sources Management

### Create Source

**`POST /api/sources`**

Add a new content source (RSS feed, Reddit subreddit, YouTube channel).

**Request Body:**
```json
{
  "url": "https://example.com/feed.xml",
  "type": "rss",
  "name": "My Blog"
}
```

**Parameters:**
- `url` (string, required): Source URL
- `type` (string, required): One of: `rss`, `reddit`, `youtube`, `file`
- `name` (string, required): Display name for the source

**Response:**
```json
{
  "success": true,
  "message": "Source added successfully",
  "data": {
    "source_id": "550e8400-e29b-41d4-a716-446655440000"
  }
}
```

---

### List Sources

**`GET /api/sources`**

Get all configured sources.

**Response:**
```json
{
  "success": true,
  "data": {
    "sources": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440000",
        "url": "https://example.com/feed.xml",
        "type": "rss",
        "name": "My Blog",
        "created_at": "2024-01-15T10:30:00Z",
        "last_fetched": "2024-01-15T11:00:00Z",
        "paused": false
      }
    ]
  }
}
```

---

### Update Source

**`PATCH /api/sources/{source_id}`**

Update source name or URL.

**Request Body:**
```json
{
  "name": "Updated Name",
  "url": "https://new-url.com/feed.xml"
}
```

**Response:**
```json
{
  "success": true,
  "message": "Source updated successfully"
}
```

---

### Delete Source

**`DELETE /api/sources/{source_id}`**

Remove a source and all its content (favorited items preserved).

**Query Parameters:**
- `keep_favorited` (boolean, default: true): Preserve favorited content

**Response:**
```json
{
  "success": true,
  "message": "Source deleted, 3 favorited items preserved"
}
```

---

### Pause Source

**`PATCH /api/sources/{source_id}/pause`**

Temporarily stop fetching from this source.

**Response:**
```json
{
  "success": true,
  "message": "Source paused"
}
```

---

### Resume Source

**`PATCH /api/sources/{source_id}/resume`**

Resume fetching from a paused source.

**Response:**
```json
{
  "success": true,
  "message": "Source resumed"
}
```

---

## Content Entries

### List Entries

**`GET /api/entries`**

Get content items with filtering.

**Query Parameters:**
- `priority` (string, optional): Filter by priority. Single value (`high`) or comma-separated
  (`high,medium,low`)
- `kind` (string, optional): Filter by kind(s), single or comma-separated; an unknown kind
  returns a 422 naming the valid kinds (see `GET /api/kinds`)
- `unread_only` (boolean, default: false): Only unread items
- `include_archived` (boolean, default: false): Include archived items
- `interesting_override` (boolean, optional): Filter by the interesting-override flag
- `since` (ISO 8601 timestamp, optional): Only items fetched after this time
- `since_hours` (integer 1–720, optional): Only items fetched in the last N hours
- `changed_since` (ISO 8601 timestamp, optional): Only items whose row changed after this
  time (fetched, re-scored, voted, read, archived); the marker a syncing client advances from
  the `updated_at` of the rows it receives
- `source` (string, optional): Filter by source name (case-insensitive substring)
- `sort_by` (string, default: `priority`): `priority`, `date` or `unread`
- `limit` (integer 1–10000, default: 50): Max items to return
- `skip_dedup` (boolean, default: true): Skip fuzzy title deduplication; pass `false` to group
  near-duplicates, which fills `duplicate_count` and `duplicate_sources`
- `view` (string, default: `full`): `full` returns every field including `content` and the whole
  `analysis`; `list` omits `content` and cuts `analysis` to the keys a list needs
- `compact` (boolean, default: false): Compact format for LLM consumption. Each item carries only
  `id`, `title`, `url`, `priority`, `kind`, `title_only`, `title_only_reason`, `published_at`,
  `source_name`, `summary`, `duplicate_count` and `duplicate_sources`; every other field
  (including `read`, `favorited`, `content` and `analysis`) is absent, not defaulted

**Response** (`view=list`):
```json
{
  "success": true,
  "message": "Retrieved 1 content items",
  "data": {
    "items": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "source_id": "550e8400-e29b-41d4-a716-446655440000",
        "source_name": "My Blog",
        "source_type": "rss",
        "external_id": "https://example.com/article",
        "title": "Understanding CRDTs",
        "url": "https://example.com/article",
        "summary": "Conflict-free replicated data types...",
        "analysis": {"kind": "analysis", "matched_interests": ["distributed systems"]},
        "priority": "high",
        "kind": "analysis",
        "title_only": false,
        "title_only_reason": null,
        "has_deep_extraction": false,
        "published_at": "2024-01-15T09:00:00Z",
        "fetched_at": "2024-01-15T10:30:00Z",
        "read": false,
        "favorited": false,
        "interesting_override": false,
        "user_feedback": null,
        "notes": null,
        "archived_at": null,
        "created_at": null,
        "updated_at": null,
        "relevance_score": null,
        "duplicate_count": null,
        "duplicate_sources": null
      }
    ],
    "total": 1,
    "query": null,
    "filters_applied": {"priority": "high", "sort_by": "priority", "limit": 1}
  }
}
```

`filters_applied` echoes every filter parameter, including the ones left at their defaults; it is
shortened here. `relevance_score` is set only by search, and `duplicate_count` and
`duplicate_sources` only when deduplication runs.

---

### Available Kinds

**`GET /api/kinds`**

Get the kinds actually present among non-archived content, so a client can build a
kind filter from what has actually been classified rather than a second hardcoded
copy of the ten kinds declared in `kind_classifier.KINDS` (used by the web UI, which
has no database of its own -- the TUI solves the same problem locally).

**Query Parameters:**
- `since_hours` (integer, optional, 1-720): Hours to look back, the same
  convenience parameter `GET /api/entries` takes. If omitted, considers all
  non-archived content.

**Response:**
```json
{
  "success": true,
  "message": "Retrieved 3 kind(s)",
  "data": {
    "kinds": ["news", "release", "tutorial"],
    "since_hours": null
  }
}
```

An empty `kinds` list means no non-archived content in the window carries a kind --
not an error.

---

### Version

**`GET /api/version`**

The running daemon's version, shown in the web UI header. Requires the API key;
`/health` deliberately carries no version because it is unauthenticated.

**Response:**
```json
{
  "success": true,
  "message": "Version retrieved",
  "data": {
    "version": "0.3.3"
  }
}
```

---

### Drift Check

**`GET /api/drift`**

The latest drift check. Each daemon cycle compares the share of items created in the
last 24 hours that have no priority with the median of the 28 daily shares before it,
and flags when the share is at least `[drift] threshold` (default 0.20) above that
median. While flagged, the check keeps the baseline it flagged with and clears only
when the share falls below that baseline plus half the threshold. Requires the API key.

`data` is `null` before the first check has run.

**Response:**
```json
{
  "success": true,
  "message": "Drift check retrieved",
  "data": {
    "id": 412,
    "checked_at": "2026-10-04T12:00:00+00:00",
    "window_items": 93,
    "window_null": 75,
    "share": 0.806,
    "baseline": 0.552,
    "baseline_windows": 28,
    "deviation": 0.254,
    "threshold": 0.2,
    "flagged": true,
    "reason": null
  }
}
```

A check that did not judge (fewer than 20 items in the window, or fewer than 14
qualifying daily windows of history) has `flagged: false`, `deviation: null`, and a
`reason` naming the minimum that was not met.

---

### Update Entry Status

**`PATCH /api/entries/{content_id}`**

Mark entry as read, favorite, or flag for context analysis.

**Request Body:**
```json
{
  "read": true,
  "favorited": false,
  "interesting_override": true
}
```

**Parameters:**
- `read` (boolean, optional): Mark as read/unread
- `favorited` (boolean, optional): Favorite/unfavorite
- `interesting_override` (boolean, optional): Flag for context analysis

**Response:**
```json
{
  "success": true,
  "message": "Content updated",
  "data": {
    "entry": { ... }
  }
}
```

---

### Get Entry Detail

**`GET /api/entries/{content_id}`**

Get entry with AI-generated summary.

**Response:**
```json
{
  "success": true,
  "data": {
    "entry": {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "title": "Understanding CRDTs",
      "summary": "AI-generated summary...",
      "priority": "high",
      "read": false,
      "favorited": false
    }
  }
}
```

---

### Get Raw Entry

**`GET /api/entries/{content_id}/raw`**

Get full original content (no AI summary).

**Response:**
```json
{
  "success": true,
  "data": {
    "entry": {
      "id": "123e4567-e89b-12d3-a456-426614174000",
      "title": "Understanding CRDTs",
      "content": "Full article text...",
      "url": "https://example.com/article"
    }
  }
}
```

---

## Search

### Semantic Search

**`GET /api/search`**

Search content using semantic similarity (embeddings). Results are ranked by
`relevance_score`: 80% semantic match, 10% priority and 10% source authority.

**Query Parameters:**
- `q` (string, required): Search query
- `limit` (integer 1–50, default: 20): Max results
- `min_score` (number 0.0–1.0, default: 0.1): Minimum `relevance_score`; the default filters
  near-zero noise, and `0.0` disables the filter
- `source` (string, optional): Filter by source name (case-insensitive substring)
- `compact` (boolean, default: false): Compact format for LLM consumption. Each item carries only
  `id`, `title`, `url`, `priority`, `kind`, `title_only`, `title_only_reason`, `relevance_score`,
  `published_at`, `source_name` and `summary`; every other field (including `read`, `favorited`,
  `content` and `analysis`) is absent, not defaulted
- `kind` (string, optional): Filter by kind(s). Single value (`release`) or
  comma-separated (`release,question`), from the ten kinds declared in
  `kind_classifier.KINDS`. An unknown kind returns a 422 naming the valid kinds --
  the same validation `GET /api/entries` applies. Constrains the underlying KNN
  candidate query itself, so a filter on anything outside the nearest 100 matches
  still finds them rather than filtering an already-limited pool.

**Response:**
```json
{
  "success": true,
  "message": "Found 1 results for 'distributed systems'",
  "data": {
    "items": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "title": "Understanding CRDTs",
        "url": "https://example.com/article",
        "source_name": "My Blog",
        "summary": "Conflict-free replicated data types...",
        "priority": "high",
        "kind": "analysis",
        "title_only": false,
        "title_only_reason": null,
        "published_at": "2024-01-15T09:00:00Z",
        "relevance_score": 0.87
      }
    ],
    "total": 1,
    "query": "distributed systems",
    "filters_applied": {"limit": 20, "min_score": 0.1, "source": null, "kind": null, "compact": false}
  }
}
```

Each item carries the same fields as `GET /api/entries`; the example shows a few. Read the score
from `relevance_score`.

---

## Content Pruning

### Prune Unprioritized

**`POST /api/prune`**

Delete unprioritized content (favorited and flagged items protected).

**Query Parameters:**
- `days` (integer, optional): Only delete items older than this many days

**Response:**
```json
{
  "success": true,
  "message": "Pruned 42 items",
  "data": {
    "deleted_count": 42
  }
}
```

**Protected from pruning:**
- Items with any priority (high/medium/low)
- Favorited items
- Items flagged with `interesting_override=true`

---

### Count Unprioritized

**`GET /api/prune/count`**

Preview how many items would be deleted by prune.

**Query Parameters:**
- `days` (integer, optional): Only count items older than this many days

**Response:**
```json
{
  "success": true,
  "data": {
    "count": 42
  }
}
```

---

## Context Assistant

### Analyze Flagged Items

**`POST /api/context`**

Analyze flagged items and suggest improvements to `context.md` using LLM.

Analyzes items you flagged with `interesting_override=true` to find gaps in your context topics.

**Response:**
```json
{
  "success": true,
  "data": {
    "suggested_topics": [
      {
        "topic": "Conflict-free replicated data types (CRDTs)",
        "section": "high",
        "action": "add",
        "existing_topic": null,
        "gap_analysis": "Multiple flagged articles about CRDTs, but no existing topic covers this",
        "rationale": "Captures distributed systems pattern missing from current topics"
      },
      {
        "topic": "Local-first software & offline sync",
        "section": "high",
        "action": "expand",
        "existing_topic": "Local-first software",
        "gap_analysis": "Existing topic too narrow, missed offline sync patterns",
        "rationale": "Broader coverage of local-first architecture"
      }
    ],
    "flagged_count": 5
  }
}
```

**Actions:**
- `add`: New topic area not currently covered
- `expand`: Existing topic too narrow
- `narrow`: Existing topic too broad
- `split`: One topic covering unrelated things

---

## Audio Briefings

### Generate Audio Briefing

**`POST /api/audio/briefings`**

Generate audio summary of unread high-priority items using TTS.

**Query Parameters:**
- `max_items` (integer, default: 10): Max items to include

**Response:**
```json
{
  "success": true,
  "data": {
    "audio_file": "/path/to/briefing.mp3",
    "duration_seconds": 180,
    "items_included": 7
  }
}
```

Requires audio provider configured in `config.toml`.

---

## Archive Status

### Get Archive Statistics

**`GET /api/archive/status`**

Get statistics about archived content.

**Response:**
```json
{
  "success": true,
  "data": {
    "total_archived": 1250,
    "by_priority": {
      "high": 100,
      "medium": 450,
      "low": 700
    },
    "oldest_archived": "2023-06-15T10:00:00Z"
  }
}
```

---

## Error Codes

| Code | Description |
|------|-------------|
| `RESOURCE_NOT_FOUND` | Source/entry not found |
| `INVALID_SOURCE_TYPE` | Unsupported source type |
| `VALIDATION_ERROR` | Invalid request data |
| `DATABASE_ERROR` | Database operation failed |
| `AUTH_ERROR` | Missing or invalid API key |
| `LLM_ERROR` | LLM API call failed (context analysis) |

---

## Rate Limits

No rate limits - this is a local-only API.

---

## Building Clients

### Example: Python Client

```python
import requests

class PrismisClient:
    def __init__(self, api_key: str, base_url: str = "http://localhost:8989"):
        self.base_url = base_url
        self.headers = {"X-API-Key": api_key}

    def list_entries(self, priority: str = None, limit: int = 50):
        params = {"limit": limit}
        if priority:
            params["priority"] = priority

        response = requests.get(
            f"{self.base_url}/api/entries",
            headers=self.headers,
            params=params
        )
        return response.json()

    def mark_read(self, entry_id: str):
        response = requests.patch(
            f"{self.base_url}/api/entries/{entry_id}",
            headers=self.headers,
            json={"read": True}
        )
        return response.json()
```

### Example: JavaScript/TypeScript Client

```typescript
class PrismisClient {
  constructor(
    private apiKey: string,
    private baseUrl: string = 'http://localhost:8989'
  ) {}

  async listEntries(priority?: string, limit = 50) {
    const params = new URLSearchParams({ limit: String(limit) });
    if (priority) params.set('priority', priority);

    const response = await fetch(
      `${this.baseUrl}/api/entries?${params}`,
      {
        headers: { 'X-API-Key': this.apiKey }
      }
    );
    return response.json();
  }

  async markRead(entryId: string) {
    const response = await fetch(
      `${this.baseUrl}/api/entries/${entryId}`,
      {
        method: 'PATCH',
        headers: {
          'X-API-Key': this.apiKey,
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ read: true })
      }
    );
    return response.json();
  }
}
```

---

## Architecture Notes

**Daemon**: FastAPI service managing content sources, priorities, and LLM analysis

**Storage**: SQLite with full-text search and vector embeddings

**TUI**: Bubble Tea terminal interface (reference implementation)

**API-First**: Build any client - web, mobile, CLI, Raycast extension, etc.

---

## Version

`GET /api/version` returns the running daemon's version.
