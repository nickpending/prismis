---
type: exploration
domain: technical
status: implemented
started: 2025-10-31
updated: 2026-02-18
tags: [exploration]
---
# URL Monitoring Design
**Date:** 2025-10-31
**Task:** 7 - Design Static URL Monitoring
**Status:** Complete

## Overview

Design for monitoring static text files (CHANGELOG.md, documentation) for changes. Primary use case: tracking GitHub project changelogs to get notified when dependencies update.

## Core Use Case

Track CHANGELOG.md files from various projects (Anthropic SDK, Python, libraries) to receive notifications when they're updated, with a summary of what changed.

Example:
- Monitor `https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/CHANGELOG.md`
- Get notified when it changes
- See diff of what's new
- LLM summarizes key changes

## Design Decisions

### 1. Change Detection Strategy

**Decision:** Content hash (SHA256)

**Rationale:**
- GitHub raw files don't provide reliable ETags/Last-Modified headers
- CHANGELOG.md files are static text - no dynamic content noise
- Simple SHA256 hash definitively indicates change
- No complexity needed for this use case

**Implementation:**
```python
current_hash = hashlib.sha256(content.encode()).hexdigest()
previous_hash = get_previous_hash_from_db(source_id)

if current_hash != previous_hash:
    # Content changed - create new entry
```

**Alternatives considered:**
- HTTP headers (ETag/Last-Modified): Unreliable for GitHub raw files
- Hybrid approach: Unnecessary complexity for static text files

---

### 2. External ID Generation

**Decision:** `sha256(url + content_hash)[:16]`

**Rationale:**
- Must create NEW entry per change (preserve history)
- Combining URL + content_hash ensures unique external_id per version
- Matches RSS fetcher pattern for stability
- 16 chars sufficient for deduplication

**Example:**
```python
url = "https://raw.githubusercontent.com/.../CHANGELOG.md"
content_hash = "abc123def456..."
external_id = hashlib.sha256(f"{url}{content_hash}".encode()).hexdigest()[:16]
# Result: "7a3f2e1b4c9d8e5a"
```

**Alternatives considered:**
- URL only: Wouldn't create new entries on updates (single entry per file)
- URL + timestamp: Less deterministic, harder to debug
- Just content_hash: Would treat same changelog content across different repos as duplicates

---

### 3. Update vs New Entry Semantics

**Decision:** Create new entry for each content change

**Rationale:**
- User wants timeline of updates: "Anthropic SDK updated 3 times this month"
- Preserves history of what changed when
- Aligns with changelog monitoring use case (track release frequency)
- Already supported by `create_or_update_content()` - just need unique external_id

**Behavior:**
```
Oct 30: "Anthropic SDK CHANGELOG Updated"
Oct 15: "Anthropic SDK CHANGELOG Updated"
Oct 1:  "Anthropic SDK CHANGELOG Updated"
```

Each entry shows in feed with different content/diffs.

**Alternative considered:**
- Update existing entry: Would lose history, single entry per URL (rejected - not useful for changelog tracking)

---

### 4. Title Generation

**Decision:** `"{source_display_name} Updated"`

**Rationale:**
- Changelog formats vary wildly (Markdown, plain text, different version schemes)
- Parsing version numbers reliably is complex and brittle
- Source display name provides context
- LLM summary in content shows actual changes
- Simple, works for all text file types

**Example:**
```python
title = f"{source['display_name']} Updated"
# "Anthropic SDK CHANGELOG Updated"
```

**Alternatives considered:**
- Extract version from content: Too brittle, formats vary
- LLM-generated title: Unnecessary LLM call, summary already exists
- Generic timestamp: Less informative than source name

---

### 5. Diff Generation & Storage

**Decision:** Generate unified diff, store as content field

**Rationale:**
- Need to show "what changed" without storing full history
- Previous version already in database (from last entry)
- Unified diff is readable for LLM and humans
- No schema changes needed
- First fetch stores full content (nothing to diff against)

**Implementation:**
```python
# Query for most recent entry from this source
previous_entry = storage.get_latest_entry_for_source(source_id)

if previous_entry:
    # Generate diff
    import difflib
    diff = difflib.unified_diff(
        previous_entry['content'].splitlines(),
        current_content.splitlines(),
        lineterm=''
    )
    diff_text = '\n'.join(diff)

    item = ContentItem(
        content=diff_text,  # Diff for LLM analysis
        analysis={
            "content_hash": current_hash,
            "full_text": current_content,  # For reference
            "diff_stats": {
                "added_lines": count_added,
                "removed_lines": count_removed
            }
        }
    )
else:
    # First fetch - no previous version
    item = ContentItem(
        content=current_content,  # Full content
        analysis={
            "content_hash": current_hash,
            "first_fetch": True
        }
    )
```

**Why this works:**
- Leverages existing database entries (no new tables)
- Diff is LLM-friendly (+ lines = new content)
- Full text preserved in analysis for reference
- Standard unified diff format

**Alternatives considered:**
- Store previous version in new table: Unnecessary schema complexity
- Heuristic extraction (grab top N lines): Unreliable, format-dependent
- No diff, just full content: Doesn't answer "what changed"

---

### 6. Content Scope

**Decision:** Raw text/markdown files only (no HTML parsing)

**Rationale:**
- Primary use case is CHANGELOG.md files (plain text/markdown)
- HTML parsing adds significant complexity (BeautifulSoup, content extraction)
- GitHub raw URLs provide clean text
- False positive handling not needed for structured text

**Supported:**
- `https://raw.githubusercontent.com/.../CHANGELOG.md`
- `https://raw.githubusercontent.com/.../README.md`
- Any text/markdown file served as plain text

**Not supported (v1):**
- HTML pages (no `<html>` parsing)
- Binary files
- Dynamic pages with JavaScript

**Future consideration:**
If HTML support needed later, create separate fetcher type or add content extraction logic.

---

### 7. False Positive Handling

**Decision:** Accept minimal noise, no special handling

**Rationale:**
- CHANGELOG.md files don't have timestamps/build IDs typically
- User can mark as read if update is uninteresting
- Over-engineering normalization adds complexity without clear benefit
- Real changelogs change infrequently (not spam risk)

**If false positives occur:**
User workflow: Open entry → See diff is just timestamp → Mark read → Continue

**Not implementing (v1):**
- Content normalization (strip comments, timestamps)
- Configurable CSS selectors (no HTML support anyway)
- Minimum change threshold

---

### 8. Integration with Fetcher Plugin Pattern

**Source Type:** `"file"`

**Fetcher Class:** `FileFetcher`

**Interface:**
```python
class FileFetcher:
    def __init__(self, max_items: int = None, config: Config = None):
        if config is None:
            config = Config.from_file()
        self.max_items = max_items or config.get_max_items("file")
        self.config = config

    def fetch_content(self, source: dict) -> List[ContentItem]:
        """
        Fetch file content, detect changes, generate diff.

        Returns:
            List with 0 or 1 ContentItem (only if changed)
        """
```

**Orchestrator Integration:**
```python
# In orchestrator.py dispatch
if source_type == "file":
    fetcher = self.file_fetcher
elif source_type == "reddit":
    fetcher = self.reddit_fetcher
# ...
```

**Registration:**
```python
# In __main__.py
file_fetcher = FileFetcher(config=config)

# In fetchers/__init__.py
from .file import FileFetcher
__all__ = ["RSSFetcher", "RedditFetcher", "YouTubeFetcher", "FileFetcher"]
```

---

## Implementation Flow

### Fetch Cycle

1. **Fetch URL content** (requests.get)
2. **Calculate content hash** (SHA256)
3. **Query for previous entry** from same source
4. **Compare hashes:**
   - If identical: Return empty list (no change)
   - If different: Continue to step 5
5. **Generate external_id** (`sha256(url + hash)[:16]`)
6. **Generate diff** (if previous entry exists)
7. **Create ContentItem:**
   - Title: `"{source_name} Updated"`
   - Content: Diff text (or full content if first fetch)
   - Analysis: hash, full_text, diff_stats
   - Published: Current timestamp
8. **Return** `[ContentItem]`

### CLI Usage

```bash
# Add file source
prismis-cli source add \
  https://raw.githubusercontent.com/anthropics/anthropic-sdk-python/main/CHANGELOG.md \
  file \
  "Anthropic SDK CHANGELOG"

# Wait for daemon cycle (or force fetch)
prismis-daemon --once

# View updates
prismis  # TUI shows "Anthropic SDK CHANGELOG Updated"
```

---

## Data Storage

### ContentItem Structure

```python
ContentItem(
    source_id=source["id"],
    external_id="7a3f2e1b4c9d8e5a",  # sha256(url + content_hash)[:16]
    title="Anthropic SDK CHANGELOG Updated",
    url="https://raw.githubusercontent.com/.../CHANGELOG.md",
    content=diff_text,  # Unified diff or full content (first fetch)
    published_at=datetime.utcnow(),
    fetched_at=datetime.utcnow(),
    analysis={
        "content_hash": "abc123def456...",
        "full_text": "# Changelog\n\n## [1.5.0]...",  # Reference
        "diff_stats": {
            "added_lines": 42,
            "removed_lines": 3,
            "changed_lines": 5
        },
        "first_fetch": False  # True only for initial fetch
    }
)
```

### No Schema Changes Required

Uses existing `content` table structure:
- `content` column: Stores diff text
- `analysis` column (JSON): Stores hash, full_text, stats
- `external_id` column: Prevents duplicates via unique external_id

---

## Error Handling

### URL Fetch Failures

```python
try:
    response = requests.get(url, timeout=10)
    response.raise_for_status()
except requests.RequestException as e:
    logger.warning(f"Failed to fetch {url}: {e}")
    return []  # Skip this cycle, try again next time
```

**Behavior:** Failed fetches logged but don't crash daemon. Retry next cycle.

### Non-Text Content

```python
content_type = response.headers.get('Content-Type', '')
if 'text' not in content_type and 'markdown' not in content_type:
    logger.warning(f"Skipping non-text file: {url} ({content_type})")
    return []
```

### Diff Generation Failures

```python
try:
    diff = difflib.unified_diff(...)
except Exception as e:
    logger.warning(f"Diff generation failed: {e}")
    # Fall back to full content
    content = current_content
```

---

## Configuration

### Config File (`config.toml`)

```toml
[fetchers.file]
max_items = 1  # Only creates 1 entry per change
timeout = 10   # HTTP request timeout (seconds)
```

### Per-Source Settings

Standard source table fields:
- `url`: File URL to monitor
- `type`: "file"
- `display_name`: User-friendly name for titles
- `enabled`: Enable/disable monitoring

---

## Performance Considerations

### Fetch Overhead

- **Single HTTP GET per source per cycle**
- **Content hash calculation:** O(n) on content length, fast for text files
- **Diff generation:** O(n+m) on line count, acceptable for documents

### Optimization

- **No change = no entry:** Hash comparison prevents unnecessary database writes
- **Batch processing:** Follows existing orchestrator pattern
- **Timeout protection:** 10s timeout prevents hanging on slow servers

---

## Testing Strategy

### Unit Tests

```python
def test_change_detection():
    # Hash comparison logic

def test_external_id_generation():
    # Uniqueness across URL+hash combinations

def test_diff_generation():
    # Unified diff output format

def test_first_fetch_no_diff():
    # No previous entry = full content
```

### Integration Tests

```python
def test_fetch_changelog_real_url():
    # Fetch actual GitHub raw URL

def test_update_creates_new_entry():
    # Modify content, verify new external_id

def test_no_change_returns_empty():
    # Same content = no new entry
```

---

## Future Enhancements (Not v1)

### HTML Support
Add `HtmlFileFetcher` with content extraction (BeautifulSoup, readability).

### Content Normalization
Strip HTML comments, timestamps if false positives emerge.

### Configurable Selectors
Let users specify CSS selectors for specific content sections.

### Change Notifications
Immediate push notifications for HIGH priority file sources.

### Diff Visualization
Render unified diff with syntax highlighting in TUI.

---

## Summary

Simple, focused design for monitoring static text files:

✅ **Content hash detection** - Reliable change detection
✅ **Diff generation** - Shows what changed
✅ **New entry per change** - Preserves update history
✅ **No schema changes** - Uses existing infrastructure
✅ **Text/markdown only** - No HTML complexity
✅ **Standard plugin pattern** - Integrates cleanly

**Estimated implementation:** ~150 lines following existing fetcher patterns.

**Next:** Task 8 - Implement FileFetcher based on this design.
