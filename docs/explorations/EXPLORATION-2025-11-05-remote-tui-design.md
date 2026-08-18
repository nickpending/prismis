---
type: exploration
domain: technical
status: draft
started: 2025-11-04
updated: 2026-02-18
tags: [exploration]
---
# Remote TUI Architecture Design

**Date:** 2025-11-05
**Task:** Task 9 - Design Remote TUI Architecture
**Status:** Design Complete

## Problem Statement

Enable prismis TUI to access content when daemon runs on a remote server (e.g., home server) while TUI runs on laptop/desktop. Current implementation assumes local SQLite access at `~/.local/share/prismis/prismis.db`.

## Design Objectives

1. Remote TUI access via network (daemon on server, TUI on client)
2. Preserve instant TUI experience (acceptable startup delay for remote)
3. Minimize network traffic (incremental updates, not full refetch)
4. Maintain existing TUI code/UX (same data structures, same filters)
5. Graceful failure handling (network issues, API unreachable)

## Architectural Approach

### Core Strategy: API-Only for Remote Mode

**Remote mode = no local database**
- TUI fetches all data via REST API
- Client-side caching and filtering
- Writes already use API (mark read, favorite) - no changes needed
- Local mode unchanged (direct SQLite access)

### Mode Detection

**Option 1: CLI Flag** (Primary)
```bash
# Local mode (default)
prismis

# Remote mode
prismis --remote https://server.example.com:8989
```

**Option 2: Environment Variable** (Alternative)
```bash
export PRISMIS_REMOTE_URL=https://server.example.com:8989
prismis
```

**Implementation:**
- Check flag/env var on startup
- If set → remote mode (API client)
- If unset → local mode (SQLite)
- No config file option (prefer explicit per-invocation control)

## Data Sync Strategy

### Initial Load (Startup)

**Local mode:**
```go
items, _, err := db.GetContentWithFilters(...)
```

**Remote mode:**
```go
// Fetch all content via API
items, err := api.FetchEntries() // GET /api/entries
// Cache in memory
cache := items
// Apply filters locally
filtered := applyFilters(cache, priority, showAll, ...)
```

### Incremental Updates (Polling)

**Pattern:** Delta sync with timestamp tracking

```go
// Every 60 seconds
lastSync := time.Now()

// Poll for new items only
newItems, err := api.FetchEntriesSince(lastSync) // GET /api/entries?since=<timestamp>

// Merge into cache
cache = mergeItems(cache, newItems)

// Re-apply filters
filtered := applyFilters(cache, currentFilters)
```

**Benefits:**
- Minimal network traffic (only new/changed items)
- Fast incremental updates
- Standard pattern (email IMAP, RSS readers, Slack)

### Client-Side Filtering

All TUI filters applied in-memory on cached data:
- Priority (high/medium/low/all/favorites/unprioritized)
- Read status (unread only / show all)
- Archived status (exclude / show archived)
- Source type (rss/reddit/youtube/file)
- Sort order (newest/oldest)

**No network round-trip for filter changes** - instant response.

### Refresh Triggers

**Automatic:**
- Poll every 60 seconds for incremental updates
- After write operations (mark read, favorite) - refresh affected item

**Manual:**
- User presses 'r' key - force full refresh

**Configurable:**
- Poll interval in config.toml (default 60s, 0 disables)

## API Changes Required

### Extend `/api/entries` Endpoint

**Current signature:**
```python
GET /api/entries?priority=high&unread_only=true&include_archived=false&limit=50&since_hours=24
```

**New signature:**
```python
GET /api/entries?since=<ISO8601_timestamp>&limit=1000
```

**Changes:**
1. Add `since` query parameter (ISO 8601 timestamp)
   - Returns only items created/modified after this timestamp
   - Empty result if no new items
2. Make `since_hours` optional (related to discovered task F1)
   - If `since` provided, ignore `since_hours`
   - If neither provided, return all content
3. Increase default `limit` or make unlimited
   - Remote TUI needs full dataset on initial load
   - Suggest `limit=0` means unlimited, or `limit=5000` default

**Example requests:**
```bash
# Initial load - all content
GET /api/entries?limit=0

# Incremental update - only new since timestamp
GET /api/entries?since=2025-11-05T02:00:00Z&limit=0

# Response format (unchanged)
{
  "success": true,
  "message": "Retrieved 12 content items",
  "data": {
    "items": [...],
    "total": 12,
    "filters_applied": {...}
  }
}
```

### No New Endpoints Required

Existing endpoints cover all operations:
- `GET /api/entries` - read content (extended above)
- `PATCH /api/entries/{id}` - update read/favorite status
- `GET /api/sources` - list sources
- `GET /api/search` - semantic search

## Data Structures

**No changes to ContentItem struct** - same in both modes:
```go
type ContentItem struct {
    ID         string
    Title      string
    URL        string
    Summary    string
    Priority   string
    Content    string
    Analysis   string
    Published  time.Time
    Read       bool
    Favorited  bool
    SourceType string
    SourceName string
    SourceID   string
}
```

**UI code unchanged** - renders `[]ContentItem` identically regardless of source.

## Implementation Impact

### New Code Required

1. **API fetch functions** (Go)
   ```go
   // tui/internal/api/content.go (new file)
   func FetchEntries(baseURL string) ([]ContentItem, error)
   func FetchEntriesSince(baseURL string, since time.Time) ([]ContentItem, error)
   ```

2. **Client-side filtering** (Go)
   ```go
   // tui/internal/filters/filters.go (new file)
   func ApplyFilters(items []ContentItem, opts FilterOptions) []ContentItem
   ```

3. **Mode detection** (Go)
   ```go
   // tui/cmd/prismis/main.go (modify)
   func detectMode() (Mode, string) // returns (Local|Remote, apiBaseURL)
   ```

4. **Cache management** (Go)
   ```go
   // tui/internal/cache/cache.go (new file)
   type ContentCache struct {
       items    []ContentItem
       lastSync time.Time
   }
   ```

### Modified Code

1. **API endpoint** (Python)
   - `daemon/src/prismis_daemon/api.py` - extend `/api/entries`

2. **Model initialization** (Go)
   - `tui/internal/ui/model.go` - choose data source based on mode

3. **Fetch commands** (Go)
   - `tui/internal/ui/model.go` - `fetchItemsWithState()` uses mode-appropriate source

**Estimate:** ~200-300 lines new Go code, ~50 lines Python changes.

## Failure Handling

### Startup Failures

**API unreachable on startup:**
```
Error: Cannot connect to remote daemon at https://server:8989
Check that:
1. Daemon is running on server
2. Network connection is available
3. API key is correct in config
```
Exit code 1, don't start TUI.

### Runtime Failures

**Poll fails during operation:**
- Log warning, keep showing cached data
- Retry on next poll interval
- Show indicator in TUI status line: "⚠ Offline (showing cached data)"

**Write fails (mark read, favorite):**
- Show error message in TUI
- Don't update cache (keep server state)
- User can retry manually

## Configuration

**config.toml additions:**
```toml
[tui]
# Auto-refresh interval in seconds (0 disables)
refresh_interval = 60

[api]
# API key (already exists)
key = "prismis-api-key"
# No base_url in config - use CLI flag for remote mode
```

**Design rationale:** Remote access is session-specific (laptop vs desktop vs server), so prefer CLI flag over static config.

## Security Considerations

1. **HTTPS required for remote** - TUI should warn if using http:// for remote URL
2. **API key in config** - already implemented, no changes
3. **No credentials in CLI flags** - API key from config only
4. **Server-side auth** - already implemented (verify_api_key dependency)

## Performance Characteristics

### Network Traffic

**Initial load:**
- ~1MB for 1000 items (rough estimate: 1KB per item)
- One-time on startup

**Incremental updates:**
- ~10-50KB per poll (assuming 10-50 new items per daemon cycle)
- 60-second intervals = 1KB/sec average

**Comparison to local mode:**
- Local: 0 network traffic, instant (<5ms SQLite query)
- Remote: Initial load 1-2 sec, polls 100-200ms each

### Startup Time

**Local mode:** <100ms (current constraint)
**Remote mode:** 1-3 seconds (initial API fetch + render)

**Design decision:** Abandon <100ms constraint for remote mode - network latency makes it impossible. Focus on responsive UX during load (show spinner).

### Filter Performance

Both modes identical - filtering happens in memory on cached `[]ContentItem`.

## Multi-Device Scenarios

### Laptop + Desktop + Server

**Setup:**
```bash
# On server (always running)
prismis-daemon

# On laptop (remote access)
prismis --remote https://homeserver.local:8989

# On desktop (local access via SSH tunnel)
ssh -L 8989:localhost:8989 server
prismis  # connects to localhost:8989 via tunnel
```

### Mobile Web

Already supported - web UI at `http://server:8989` works on any device. No TUI changes needed.

## Offline Behavior

**Remote mode has no offline capability by design:**
- No local database to fall back to
- Network required for all operations
- Graceful degradation: show cached data, indicate offline status

**If offline capability needed in future:** Would require hybrid mode (local SQLite replica + API sync). Out of scope for this design.

## Trade-offs Analysis

### Chosen Approach: API-Only Remote

**Pros:**
- Simple implementation (~250 lines)
- No database sync complexity
- Consistent with existing write operations
- Standard polling pattern

**Cons:**
- Initial load slower (1-3 sec vs <100ms)
- No offline access
- Network dependency

### Alternative Rejected: Local SQLite Replica + Sync

**Pros:**
- Instant startup (local DB)
- Offline capability
- Consistent read performance

**Cons:**
- Complex sync logic (conflicts, merge strategy)
- Database replication (~500-800 lines)
- File transfer mechanism (rsync, scp, custom protocol)
- WAL mode concurrency issues

**Rejection rationale:** Complexity not justified - daemon fetches every 30 min, so real-time sync unnecessary. API polling sufficient for this use case.

## Implementation Roadmap

### Phase 1: API Extension (Task 10 prerequisite)
1. Extend `/api/entries` with `since` parameter
2. Make `since_hours` optional
3. Test with curl

### Phase 2: TUI Remote Mode (Task 10)
1. Add mode detection (CLI flag + env var)
2. Implement API fetch functions
3. Add client-side filtering
4. Cache management
5. Poll loop for incremental updates
6. Error handling and UX

### Phase 3: Polish
1. Loading spinner for initial fetch
2. Offline indicator
3. Retry logic for failed polls
4. Configuration for poll interval

## Success Criteria

**Design complete when:**
- [x] Remote access approach defined (API-only)
- [x] Sync strategy documented (initial + incremental)
- [x] API changes specified (`since` parameter)
- [x] Mode detection method chosen (CLI flag)
- [x] Failure handling defined
- [x] Performance characteristics understood
- [x] Implementation scope estimated (~250 lines)

**Implementation complete when (Task 10):**
- [ ] `prismis --remote https://server:8989` works
- [ ] Incremental polling functional
- [ ] Client-side filtering maintains UX
- [ ] Error messages clear and helpful
- [ ] All existing TUI features work in remote mode

## Open Questions

None - design is complete and ready for implementation.

## References

- Task 9: Design Remote TUI Architecture
- Task 10: Implement Remote TUI Mode (Based on Design)
- Discovered Task F1: Fix API Time Filter Restriction (related - `since_hours` optional)
- Current API: `daemon/src/prismis_daemon/api.py`
- Current TUI data layer: `tui/internal/db/queries.go`
