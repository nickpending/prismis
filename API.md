# Prismis API Documentation

Local-first content intelligence API for managing RSS feeds, Reddit, YouTube, and other content sources with semantic prioritization.

## Base URL

```
http://localhost:8000
```

The daemon runs locally only. No remote access.

## Authentication

All API endpoints (except `/health`) require an API key header:

```http
X-API-Key: your-api-key-from-config
```

Get your API key from `~/.config/prismis/config.toml`:

```toml
[api]
api_key = "your-secret-key-here"
```

## Interactive Documentation

FastAPI provides auto-generated interactive docs:

- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

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

Get content items with filtering and pagination.

**Query Parameters:**
- `priority` (string, optional): Filter by priority (`high`, `medium`, `low`)
- `read` (boolean, optional): Filter by read status
- `favorited` (boolean, optional): Filter by favorite status
- `source_id` (uuid, optional): Filter by source
- `limit` (integer, default: 50): Max items to return
- `offset` (integer, default: 0): Pagination offset

**Response:**
```json
{
  "success": true,
  "data": {
    "entries": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "source_id": "550e8400-e29b-41d4-a716-446655440000",
        "source_name": "My Blog",
        "title": "Understanding CRDTs",
        "url": "https://example.com/article",
        "summary": "Conflict-free replicated data types...",
        "priority": "high",
        "read": false,
        "favorited": false,
        "published_at": "2024-01-15T09:00:00Z",
        "created_at": "2024-01-15T10:30:00Z"
      }
    ],
    "total": 150,
    "limit": 50,
    "offset": 0
  }
}
```

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

Search content using semantic similarity (embeddings).

**Query Parameters:**
- `q` (string, required): Search query
- `limit` (integer, default: 10): Max results

**Response:**
```json
{
  "success": true,
  "data": {
    "results": [
      {
        "id": "123e4567-e89b-12d3-a456-426614174000",
        "title": "Understanding CRDTs",
        "summary": "Conflict-free replicated data types...",
        "similarity": 0.87,
        "priority": "high"
      }
    ],
    "query": "distributed systems",
    "total": 5
  }
}
```

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
    def __init__(self, api_key: str, base_url: str = "http://localhost:8000"):
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
    private baseUrl: string = 'http://localhost:8000'
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

API Version: 0.1.3

Last Updated: 2024-11-15
