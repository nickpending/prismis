---
type: exploration
domain: technical
status: draft
started: 2025-10-29
updated: 2026-02-18
tags: [exploration]
---
# Content Archival Policy Design

**Date:** 2025-10-29
**Status:** Design Complete
**Task:** Task 5 - Design Content Archival Policy

## Overview

Design automatic content archival policy to realize prismis's "ephemeral by design" philosophy while preserving searchable history and protecting user-curated content.

**Core Philosophy:** TUI shows current content, archive preserves searchable history without manual intervention.

## Problem Statement

Current state: 1,661 items in database (18 read, 1,643 unread). Without archival:
- TUI becomes cluttered with old content
- LOW priority items accumulate indefinitely if unread
- "Ephemeral" design is stated but not implemented
- Users lose focus on what matters now

Database size is not the concern (projected 1.2GB in 5 years is trivial for SQLite). Archival is about **workflow and attention management**, not storage optimization.

## Design Decisions

### 1. Archival Semantics

**Decision: Soft Hide (not deletion)**

Archived content is:
- Hidden from default TUI/API views (`archived_at IS NOT NULL`)
- Fully preserved in database (no content stripping, no deletion)
- Searchable via semantic search (embeddings intact)
- Accessible via explicit flag (`--archived`, `include_archived=true`)
- Reversible via favoriting (favorites auto-unarchive)

**Rationale:**
- Preserves searchable history (HIGH RISK requirement from task invariants)
- No data loss risk
- Simple to implement (single WHERE clause)
- Aligns with "intelligent research archive" concept

**Rejected alternatives:**
- **Hard delete:** Violates "archived items remain queryable" invariant
- **Content stripping:** Premature optimization, breaks fabric patterns
- **Separate table:** Unnecessary complexity, same query performance

### 2. Trigger Rules (Priority-Aware Aging)

**Decision: Daemon scheduled job with priority-based windows**

| Priority | Unread Threshold | Read Threshold | Rationale |
|----------|------------------|----------------|-----------|
| HIGH | Never | 30 days | Always important until processed, then reasonable retention |
| MEDIUM | 14 days | 30 days | If ignored for 2 weeks, you're not reading it |
| LOW | 7 days | 30 days | If ignored for 1 week, you're not reading it |
| NULL (unprioritized) | N/A | N/A | Handled by existing `:prune` command (delete, not archive) |

**Archive criteria (ALL must be true):**
```sql
archived_at IS NULL  -- Not already archived
AND favorited = 0    -- Never archive favorites
AND notes IS NULL    -- Never archive noted content
AND (age_rule_matches)  -- Priority-specific windows above
```

**Rationale:**
- Acknowledges user attention as signal (unread LOW for 7d = not reading)
- HIGH priority stays until explicitly processed (respects LLM intelligence)
- Read items get generous 30-day window regardless of priority
- Automatic cleanup without user thinking about it

**Rejected alternatives:**
- **Read-only trigger:** Doesn't handle LOW priority accumulation (1,643 unread items problem)
- **Manual only:** Tedious, defeats "ephemeral" automation goal
- **Single time window:** Ignores priority intelligence from LLM

### 3. Protection Rules (Absolute Guarantees)

**Decision: Favorites and notes trigger protection**

Protected content (NEVER archived):
1. `favorited = 1` - User explicitly marked as important
2. `notes IS NOT NULL` - User invested time adding context
3. Interaction: Favoriting archived item could auto-unarchive (implementation choice)

**Rationale:**
- Favorites = "I want this forever" explicit signal (CRITICAL INVARIANT from task)
- Notes = implicit investment, shows content has lasting value
- Existing protection logic already handles `favorited` during source deletion

**Rejected alternatives:**
- **Time-based favorite protection:** Unnecessarily complex, favorites mean "keep"
- **No notes protection:** Risk archiving content user annotated

### 4. Schema Changes

**Decision: Single `archived_at TIMESTAMP` column**

```sql
-- Migration
ALTER TABLE content ADD COLUMN archived_at TIMESTAMP DEFAULT NULL;
CREATE INDEX idx_content_archived ON content(archived_at);
```

**Semantics:**
- `NULL` = Active (visible in default views)
- `TIMESTAMP` = Archived (hidden from default views, timestamp records when)

**Query patterns:**
```sql
-- Default view (TUI/API)
WHERE archived_at IS NULL

-- Include archived (search, explicit --archived flag)
-- No WHERE clause on archived_at

-- Archived only
WHERE archived_at IS NOT NULL
```

**Rationale:**
- Simple boolean-like semantics with timestamp benefits
- Timestamp enables future analytics ("archived 6 months ago")
- Single index efficient for both active and archived queries
- Reversible (SET archived_at = NULL to unarchive)

**Rejected alternatives:**
- **Boolean flag:** Loses timing information, no advantage
- **Separate table:** Unnecessary complexity, same performance
- **Status enum:** Overengineered for binary active/archived state

### 5. Trigger Mechanism (Daemon Automation)

**Decision: APScheduler job in daemon, runs every 6 hours**

```python
# In daemon orchestrator or separate module
@scheduler.scheduled_job('interval', hours=6)
def run_archival_policy():
    storage.archive_old_content()
    logger.info(f"Auto-archival: {count} items archived")
```

**What it does:**
```sql
UPDATE content
SET archived_at = CURRENT_TIMESTAMP
WHERE archived_at IS NULL
  AND favorited = 0
  AND notes IS NULL
  AND (
    -- HIGH: Only read + 30 days old
    (priority = 'high' AND read = 1 AND fetched_at < datetime('now', '-30 days'))
    OR
    -- MEDIUM: Unread 14d OR read 30d
    (priority = 'medium' AND (
      (read = 0 AND fetched_at < datetime('now', '-14 days'))
      OR (read = 1 AND fetched_at < datetime('now', '-30 days'))
    ))
    OR
    -- LOW: Unread 7d OR read 30d
    (priority = 'low' AND (
      (read = 0 AND fetched_at < datetime('now', '-7 days'))
      OR (read = 1 AND fetched_at < datetime('now', '-30 days'))
    ))
  )
```

**Rationale:**
- Silent, automatic - realizes ephemeral philosophy
- 6-hour interval balances freshness vs overhead
- WAL mode handles concurrent access with daemon writes
- No user intervention required

**Rejected alternatives:**
- **Manual CLI command only:** Too much friction, archives won't happen
- **On-read trigger:** Requires API write path, more complex
- **Daily batch:** 6 hours more responsive, negligible overhead

### 6. Configuration & Control

**Decision: TOML config file with enable/disable and tunable windows**

```toml
# ~/.config/prismis/config.toml
[archival]
enabled = true  # Master switch

[archival.windows]
high_unread = null   # null = never archive
high_read = 30
medium_unread = 14
medium_read = 30
low_unread = 7
low_read = 30
```

**User controls:**
- **Disable archival:** Set `enabled = false`
- **Tune windows:** Adjust days per priority
- **View status:** `prismis-cli archive status` (shows archived count, last run)
- **View archived:** `prismis-cli list --archived` or `--include-archived`

**Logging:**
```
[2025-10-29 01:30] Auto-archival: 47 items archived (23 LOW, 18 MEDIUM, 6 HIGH)
```

**Rationale:**
- Transparency without noise (config + logging + status command)
- User can disable or tune without code changes
- Sensible defaults for 90% of users
- Follows prismis pattern (compare to source config)

**Rejected alternatives:**
- **Hardcoded only:** No user control if defaults don't fit
- **Complex per-source rules:** Overengineered for v1
- **UI for configuration:** TOML sufficient, keeps complexity low

### 7. Search Integration

**Decision: Semantic search always includes archived content**

```python
@app.get("/api/search")
async def search_content(...):
    # NO archived_at filter
    # Search is for finding old content - include everything
```

**Rationale:**
- Search purpose is to find things you can't see (old content)
- Archived content still has embeddings (semantic search works)
- If you're searching for it, you want to find it regardless of age

**Rejected alternatives:**
- **Separate `include_archived` flag:** Adds friction to primary use case
- **Exclude archived:** Defeats search purpose (finding old content)

### 8. TUI Filtering Strategy

**Decision: Add archived as filter dimension to existing multi-dimensional system**

**IMPLEMENTATION CORRECTION:**
TUI already has sophisticated filtering (priority/read/source/sort). Archived extends this, not replaces it.

**Integration approach:**

**Database Layer (`tui/internal/db/queries.go`):**
- Add `includeArchived bool` parameter to `GetContentWithFilters()`
- Add `AND archived_at IS NULL` when `!includeArchived`

**Model State (`tui/internal/ui/model.go`):**
- Add `showArchived bool` field to Model struct
- Hotkey `5`: Toggle archived visibility
- Three states: hidden (default), archived-only, all

**UI Feedback:**
- **Header (filter state):** Add `"ARCHIVED"` indicator to existing filter display
- **Footer (metrics):** Optionally add `"ARCH: 847"` to counts (follows `"HIDDEN: 12"` pattern for unprioritized)

**Command (`tui/internal/commands/registry.go`):**
- Register `:archived` command, returns `RefreshMsg`

**Rationale:**
- Follows existing patterns (multi-dimensional filters)
- Hotkey `5` continues number sequence (0-4 existing)
- Command for discoverability
- Minimal code changes, maximum consistency

### 9. API Changes (Read-Only)

**Decision: Add `include_archived` query parameter, no write endpoints**

**Modified endpoints:**
```python
@app.get("/api/entries")
async def get_content(
    priority: Optional[str] = None,
    unread_only: bool = False,
    include_archived: bool = Query(False),  # NEW
    ...
)

@app.get("/api/entries/{content_id}")
async def get_entry(
    content_id: str,
    include_archived: bool = Query(False),  # NEW
    ...
)
```

**New endpoints:**
```python
@app.get("/api/archive/status")
async def archive_status():
    return {
        "enabled": config.archival.enabled,
        "last_run": storage.get_last_archival_run(),
        "total_items": storage.count_all(),
        "archived_items": storage.count_archived(),
        "active_items": storage.count_active(),
    }
```

**Storage layer:**
```python
def get_content_by_priority(..., include_archived: bool = False):
    query = "SELECT ... WHERE ..."
    if not include_archived:
        query += " AND archived_at IS NULL"
```

**NO write endpoints:**
- No `/api/archive/{id}` POST (no manual archival)
- No `/api/archive/{id}` DELETE (no manual unarchive)
- Daemon-only writes keep architecture simple

**Rationale:**
- Read-only API consistent with "daemon owns writes" pattern
- CLI/Web UI inherit filtering through API automatically
- Manual intervention via favoriting (existing write path)

### 10. CLI Integration

**Decision: Status command + list flag, no write commands**

**New commands:**
```bash
prismis-cli archive status
# Output:
# Archival: Enabled
# Last run: 2025-10-29 01:30 (4 hours ago)
# Total: 1661 items (814 active, 847 archived)
# Breakdown: 287 unread, 527 read, 847 archived
```

**Modified commands:**
```bash
prismis-cli list --archived        # Show archived only
prismis-cli list --include-archived  # Show both active and archived
```

**No write commands:**
- No `prismis-cli archive <id>` (no manual archival)
- No `prismis-cli archive run` (daemon handles)

**Rationale:**
- Status gives visibility without noise
- List flags provide access to archived content
- No write commands = simpler, cleaner

## Implementation Files

**Files to modify:**
1. `daemon/src/prismis_daemon/schema.sql` - Add archived_at column + index
2. `daemon/src/prismis_daemon/storage.py` - Add archival methods, modify queries
3. `daemon/src/prismis_daemon/api.py` - Add include_archived param, status endpoint
4. `daemon/src/prismis_daemon/orchestrator.py` - Add scheduled archival job
5. `daemon/src/prismis_daemon/config.py` - Add archival config section
6. `daemon/src/prismis_daemon/__main__.py` - Add scheduled job to APScheduler
7. `cli/src/cli/list.py` - Add --archived, --include-archived flags
8. `cli/src/cli/archive.py` - NEW: status command
9. `cli/src/cli/api_client.py` - Add get_archive_status method
10. `cli/src/cli/__main__.py` - Register archive sub-typer
11. `tui/internal/db/queries.go` - Add includeArchived parameter to GetContentWithFilters
12. `tui/internal/ui/model.go` - Add showArchived field, hotkey `5` handler
13. `tui/internal/ui/feed.go` - Update header filter display, optionally footer counts
14. `tui/internal/commands/registry.go` - Register :archived command
15. `config.example.toml` - Add archival config example

**Migration path:**
- Existing data: All content starts with `archived_at = NULL` (active)
- First run: Daemon archives items matching criteria
- No backfill needed

## Edge Cases & Considerations

### Favoriting Archived Content
**Question:** User favorites an archived item - auto-unarchive it?

**Recommendation:** YES, auto-unarchive on favorite
- Favoriting signals "I want to see this"
- Archived favorites violate user intent
- Simple: `UPDATE content SET archived_at = NULL, favorited = 1 WHERE id = ?`

### Adding Notes to Archived Content
**Question:** User adds notes to archived item - auto-unarchive?

**Recommendation:** YES, auto-unarchive on note add
- Notes indicate renewed interest
- Archived noted content violates protection rule
- Same pattern as favoriting

### Priority Change
**Question:** User changes priority of archived item (unlikely via API)?

**Recommendation:** Leave archived
- Priority change doesn't necessarily mean "show me this"
- If user wants it visible, they'll favorite it
- Edge case unlikely to occur

### Search Result Interaction
**Question:** User opens archived item from search - auto-unarchive?

**Recommendation:** NO, leave archived
- Viewing ≠ wanting in active view
- Search is for reference, not resurrection
- If they want it back, they'll favorite it

## Performance Considerations

**Query performance:**
- Index on `archived_at` handles `WHERE archived_at IS NULL` efficiently
- Existing indexes on `priority`, `read`, `fetched_at` support archival query
- Archival UPDATE query runs on ~50-500 items (< 1ms expected)

**Storage impact:**
- Additional 8 bytes per row (TIMESTAMP)
- Index overhead: Minimal (~4KB per 1000 items)
- Total database size impact: < 1% increase

**Daemon overhead:**
- Archival job runs every 6 hours
- Expected query time: < 100ms for typical dataset
- No impact on fetch cycle or other operations

## Success Metrics

**Feature works if:**
1. TUI shows only active content by default (clean, focused)
2. Archived count visible but not intrusive
3. Search finds archived content seamlessly
4. Favorites never archived (CRITICAL INVARIANT)
5. Config allows disable/tuning without code changes
6. No performance degradation on TUI launch (<100ms maintained)

## Future Enhancements (Out of Scope for V1)

- Per-source archival rules (some sources archive faster/slower)
- Bulk unarchive command
- Archival analytics (what gets archived most, trend analysis)
- Machine learning on user favorites to adjust windows automatically
- Permanent deletion of very old archived content (multi-stage archival)

## Conclusion

This design realizes prismis's ephemeral philosophy through automatic, intelligent archival that:
- Keeps TUI focused on current content
- Preserves searchable history indefinitely
- Protects user-curated content absolutely
- Requires zero manual intervention
- Maintains architectural simplicity (daemon-only writes, read-only API)
- Provides visibility and control without complexity

Implementation is straightforward: one column, one index, one scheduled job, filters on existing queries. The complexity is in the policy (priority-aware windows), not the mechanism.
