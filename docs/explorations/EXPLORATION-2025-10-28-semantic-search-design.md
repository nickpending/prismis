---
type: exploration
domain: technical
status: draft
started: 2025-10-28
updated: 2026-02-18
tags: [exploration]
---
# Semantic Search Architecture Design

**Date:** 2025-10-28
**Status:** Design Complete
**Task:** Task 3 - Design Semantic Search Architecture

## Goal

Enable semantic content discovery - find "LLM" when searching "large language models". Maintain prismis' local-first philosophy and priority intelligence.

## Design Decisions

### 1. Embedding Generation

**Decision:** Local sentence-transformers model (all-MiniLM-L6-v2)

**Rationale:**
- Local-first: Works offline, no API costs
- Fast: ~20-50ms per item during daemon fetch
- Good enough: Handles semantic matching (LLM ↔ large language models)
- Lightweight: ~25MB model size
- Swappable: Clean interface allows future provider changes

**Implementation:**
- Daemon generates embeddings during content fetch
- Embed `title + summary` (content often too long/noisy)
- Store embeddings in SQLite via sqlite-vec

**Alternative rejected:** LiteLLM embeddings (slower, requires network, API costs)

---

### 2. Storage Strategy

**Decision:** sqlite-vec extension for native vector search

**Rationale:**
- Keeps everything in SQLite (matches architecture)
- Real semantic search (not just keyword matching)
- Lightweight: Pure Python, no compilation
- Fast: Native cosine similarity queries
- Simple: ~100 LOC integration

**Schema:**
```sql
CREATE VIRTUAL TABLE content_embeddings USING vec0(
  content_id TEXT PRIMARY KEY,
  embedding FLOAT[384]  -- all-MiniLM-L6-v2 dimension
);
```

**Alternatives rejected:**
- FTS5: Only keywords, not truly semantic
- FAISS: Overkill, separate file sync complexity

---

### 3. Interface Design

**Decision:** API first - `GET /api/search?q=term`

**Rationale:**
- Web UI gets search automatically
- CLI can reuse storage method later
- TUI stays focused on daily feed browsing
- Simplest initial implementation

**API Endpoint:**
```
GET /api/search?q=<query>&limit=20&min_score=0.5

Response:
{
  "success": true,
  "message": "Found 5 results",
  "data": {
    "items": [...],  // Standard entry format
    "total": 5,
    "query": "large language models"
  }
}
```

Each item includes `relevance_score` field (0.0-1.0).

**Future expansion:** CLI command, TUI search can reuse `Storage.search_content()` method.

---

### 4. Query Flow & Ranking

**Decision:** Weighted ranking - similarity + priority + recency

**Ranking Formula:**
```python
score = (similarity * 0.6) + (priority_weight * 0.3) + (recency_weight * 0.1)

# Priority weights
priority_weight = {
  'high': 1.0,
  'medium': 0.5,
  'low': 0.0,
  None: 0.0  # Unprioritized
}

# Recency weight (0.0-1.0 based on age)
recency_weight = max(0.0, 1.0 - (days_old / 365))
```

**Rationale:**
- Preserves prismis' priority intelligence
- Recent HIGH priority content beats old LOW priority (even with lower similarity)
- Similarity remains primary factor (60%)
- Recency prevents stale content domination

**Query Process:**
1. User queries: `GET /api/search?q=large language models`
2. Generate query embedding (same model)
3. sqlite-vec cosine similarity search (get top 100 candidates)
4. Re-rank with weighted formula
5. Return top N (default 20)

**Result Format:**
- Reuse existing entry structure from `/api/entries`
- Add `relevance_score` field (final weighted score)
- Standard fields: id, title, url, summary, priority, published_at, etc.

**Alternative rejected:** Pure similarity ranking (ignores priority signal, defeats prismis' purpose)

---

## Implementation Summary

**Components to build:**

1. **Embeddings Module** (`daemon/src/prismis_daemon/embeddings.py`)
   - Load sentence-transformers model
   - Generate embeddings for text
   - Clean interface for swapping providers

2. **Schema Migration** (add to `schema.sql` or separate migration)
   - Create `content_embeddings` virtual table
   - Index on content_id

3. **Storage Methods** (add to `storage.py`)
   - `generate_and_store_embedding(content_id, text)` - daemon calls during fetch
   - `search_content(query, limit, min_score)` - search implementation

4. **API Endpoint** (add to `api.py`)
   - `GET /api/search` - public endpoint
   - Query validation
   - Call storage, return results

5. **Daemon Integration** (modify orchestrator)
   - Generate embeddings after LLM analysis
   - Handle embedding failures gracefully

**Estimated effort:**
- Core functionality: ~200 LOC
- Testing: ~100 LOC
- Total: 2-3 hours implementation

---

## Constraints Preserved

- ✅ <100ms TUI launch (search doesn't affect TUI startup)
- ✅ Local-first philosophy (no external APIs)
- ✅ Repository pattern (all SQL in storage.py)
- ✅ SQLite WAL mode compatible
- ✅ Priority intelligence maintained

---

## Future Enhancements

**Not in initial scope, but easy to add:**
- CLI command: `prismis-cli search "term"`
- TUI command: `:search term`
- Hybrid search: Combine semantic + FTS5 keywords
- Configurable ranking weights
- Filter by priority, read status, date range
- Batch re-embedding (for model upgrades)

---

## Success Criteria

**The design succeeds when:**
1. Search finds "LLM" when querying "large language models" ✓
2. HIGH priority recent content ranks above LOW priority old content ✓
3. Works offline ✓
4. TUI launches in <100ms (unaffected) ✓
5. Integration follows repository pattern ✓
6. Embedding provider swappable ✓

**Deliverable:** This design document captures architectural decisions for Task 4 (Implementation).
