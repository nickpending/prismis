# Prismis Feedback Loop — Unified Spec

> **Vision:** Users rate content. The system learns. `context.md` evolves automatically. Prioritization improves over time. No manual context editing required.

---

## 🎯 The Goal

Replace manual context curation with a continuous feedback loop:

```
┌─────────────────────────────────────────────────────────────┐
│                                                             │
│    User consumes content                                    │
│            │                                                │
│            ▼                                                │
│    User votes 👍/👎                                         │
│            │                                                │
│            ▼                                                │
│    System aggregates patterns ◄─────────────────────┐       │
│            │                                        │       │
│            ▼                                        │       │
│    context.md auto-updates (with backup)            │       │
│            │                                        │       │
│            ▼                                        │       │
│    Evaluator uses updated context                   │       │
│            │                                        │       │
│            ▼                                        │       │
│    Better prioritization ───────────────────────────┘       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**User effort:** Vote on content while reading.  
**System effort:** Everything else.

---

## 📊 Current State

### What Exists (mainline)

| Component | Status | Purpose |
|-----------|--------|---------|
| `interesting_override` | ✅ In prod | Flag unprioritized items for context review |
| `context_analyzer.py` | ✅ In prod | Analyze flagged items → suggest topics |
| Manual context.md | ✅ In prod | User writes/edits their interests |

**Limitations:**
- `interesting_override` only works on unprioritized/LOW items
- Context analyzer suggests but doesn't auto-update
- Requires user to manually review suggestions and edit context.md

### What Salt Built (PRs #6, #7, #8)

| PR | Component | Purpose |
|----|-----------|---------|
| #6 | `user_feedback` field | Up/down/null voting on ALL content |
| #6 | Web UI buttons | Vote from browser |
| #7 | `/api/feedback/statistics` | Aggregate votes by source/topic |
| #7 | `for_llm_context` string | Pre-formatted preferences for LLM |
| #8 | Preference injection | Feed learned prefs into evaluator |
| #8 | Visual indicator | Show when item was preference-influenced |

**What's missing from Salt's PRs:**
- TUI support (only Web UI)
- CLI support
- Auto-update of context.md (just injects into evaluator prompt)
- Backup/versioning

---

## 🏗️ Target Architecture

### Schema Changes

```sql
-- Replace interesting_override with user_feedback
-- (Salt's PR #6 already adds this)
ALTER TABLE content ADD COLUMN user_feedback TEXT 
    CHECK(user_feedback IN ('up', 'down', NULL)) DEFAULT NULL;

-- Migration: Convert existing interesting_override flags
UPDATE content SET user_feedback = 'up' WHERE interesting_override = 1;

-- Eventually: DROP COLUMN interesting_override (after migration period)
```

### New: Context Auto-Updater

A new component that:
1. Runs periodically (or on-demand)
2. Analyzes vote patterns
3. Generates updated context.md
4. Backs up old version
5. Writes new version

```python
# Pseudocode
class ContextAutoUpdater:
    def __init__(self, storage, config):
        self.storage = storage
        self.context_path = config.context_path
        self.backup_dir = config.context_backup_dir
        self.min_votes = config.get("auto_update_min_votes", 10)
        self.update_interval = config.get("auto_update_interval_hours", 24)
    
    def should_update(self) -> bool:
        """Check if enough new votes since last update."""
        stats = self.storage.get_feedback_statistics()
        return stats["total_votes"] >= self.min_votes
    
    def backup_context(self):
        """Create timestamped backup of current context.md."""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"context_{timestamp}.md"
        shutil.copy(self.context_path, backup_path)
        # Keep last N backups, prune older ones
        self.prune_old_backups(keep=10)
    
    def generate_new_context(self) -> str:
        """Use LLM to generate updated context.md from vote patterns."""
        current_context = self.context_path.read_text()
        stats = self.storage.get_feedback_statistics()
        
        prompt = f"""
        Current context.md:
        {current_context}
        
        User feedback patterns (last 30 days):
        - Topics upvoted: {stats['topics_upvoted']}
        - Topics downvoted: {stats['topics_downvoted']}
        - Trusted sources: {stats['trusted_sources']}
        - Distrusted sources: {stats['distrusted_sources']}
        
        Generate an updated context.md that:
        1. Preserves the user's original voice/style
        2. Adds topics they've shown interest in (upvotes)
        3. Moves downvoted topics to "Not Interested"
        4. Adjusts priority levels based on vote patterns
        5. Does NOT remove topics unless clearly contradicted by votes
        """
        
        # Call LLM, return new context
        ...
    
    def update(self):
        """Main update flow."""
        if not self.should_update():
            return
        
        self.backup_context()
        new_context = self.generate_new_context()
        self.context_path.write_text(new_context)
        
        # Reset vote counts or mark as processed
        self.storage.mark_feedback_processed()
```

### Component Interactions

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│     TUI      │     │   Web UI     │     │     CLI      │
│  (Go/Bubble) │     │   (HTML)     │     │   (Python)   │
└──────┬───────┘     └──────┬───────┘     └──────┬───────┘
       │                    │                    │
       │         user_feedback votes             │
       └────────────────────┼────────────────────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   API Layer   │
                    │  (FastAPI)    │
                    └───────┬───────┘
                            │
              ┌─────────────┼─────────────┐
              ▼             ▼             ▼
       ┌──────────┐  ┌──────────┐  ┌──────────────┐
       │ Storage  │  │ Stats    │  │ Auto-Updater │
       │ (SQLite) │  │ Endpoint │  │ (new)        │
       └──────────┘  └────┬─────┘  └──────┬───────┘
                          │               │
                          │    ┌──────────┘
                          ▼    ▼
                    ┌───────────────┐
                    │  context.md   │◄── backups/
                    └───────┬───────┘
                            │
                            ▼
                    ┌───────────────┐
                    │   Evaluator   │
                    │ (prioritizes) │
                    └───────────────┘
```

---

## 📋 Implementation Phases

### Phase 1: Merge Salt's Foundation
**Goal:** Get voting infrastructure in place

- [ ] Review & merge PR #6 (user_feedback field + Web UI)
- [ ] Review & merge PR #7 (statistics endpoint)
- [ ] Review field naming: `user_feedback` vs `user_vote` (consistency)
- [ ] Test Web UI voting flow

### Phase 2: TUI Support
**Goal:** Vote from terminal interface

- [ ] Add `UserFeedback string` to Go `ContentItem` struct
- [ ] Add `SetUserFeedback(contentID, vote)` in `db/queries.go`
- [ ] Add `:upvote` / `:downvote` commands (or `:up` / `:down`)
- [ ] Add keybindings (`+` / `-` or `u` / `d`)
- [ ] Add visual indicator for voted items in list view
- [ ] Remove priority restriction (allow voting on ALL content)
- [ ] Add `:votes` view filter (show upvoted/downvoted only)

### Phase 3: Deprecate interesting_override
**Goal:** Unify on single feedback mechanism

- [ ] Migration script: `interesting_override=1` → `user_feedback='up'`
- [ ] Update context analyzer to read from `user_feedback`
- [ ] Remove `:interesting` command from TUI
- [ ] Remove `interesting_override` from API
- [ ] Keep column for 1 release cycle, then drop

### Phase 4: Auto-Update context.md
**Goal:** Close the loop — votes automatically improve context

- [ ] Create `context_auto_updater.py`
- [ ] Add backup directory config (`context_backup_dir`)
- [ ] Implement backup rotation (keep last N)
- [ ] LLM prompt for generating updated context
- [ ] Conservative update strategy (add > modify > remove)
- [ ] Add to daemon scheduler (run every N hours or on threshold)
- [ ] API endpoint to trigger manual update
- [ ] API endpoint to restore from backup

### Phase 5: Review & Merge PR #8
**Goal:** Preference injection into evaluator

- [ ] Review PR #8 (preference learning injection)
- [ ] Ensure it plays nice with auto-updated context.md
- [ ] May need coordination: auto-updater vs. prompt injection
- [ ] Visual indicator for preference-influenced items

### Phase 6: Polish & Guard Rails
**Goal:** Safety and observability

- [ ] Diff view: show what changed in context.md
- [ ] Notification when context auto-updates
- [ ] "Review pending changes" mode (optional approval gate)
- [ ] Rollback command in TUI/CLI
- [ ] Metrics: track context.md changes over time
- [ ] Rate limiting: don't update more than once per N hours

---

## 🎹 TUI Keybindings (Proposed)

| Key | Action | Notes |
|-----|--------|-------|
| `+` | Upvote current item | Or `u` |
| `-` | Downvote current item | Or `d` |
| `=` | Clear vote | Or `0` |
| `V` | Toggle votes view | Show only voted items |
| `Shift+V` | Show vote stats | Modal with aggregates |

**Visual indicators in list:**
- `👍` or `▲` prefix for upvoted
- `👎` or `▼` prefix for downvoted
- Maybe color: green tint for up, red for down

---

## 🔒 Safety Considerations

### Backup Strategy
```
~/.config/prismis/
├── context.md              # Current version
└── context_backups/
    ├── context_20260129_120000.md
    ├── context_20260128_120000.md
    └── ... (keep last 10)
```

### Conservative Updates
The auto-updater should be **additive by default**:
- ✅ Add new topics user has upvoted
- ✅ Adjust priority (LOW→MEDIUM→HIGH) based on vote ratio
- ⚠️ Move topics to "Not Interested" only with strong signal (5+ downvotes)
- ❌ Never delete topics entirely without explicit user action

### Escape Hatches
- Manual edit always wins (detect manual changes, don't overwrite)
- `prismis context rollback` — restore previous version
- `prismis context rollback --list` — show available backups with dates
- `prismis context rollback --date YYYYMMDD` — restore specific backup
- `prismis context diff` — show pending changes before apply
- `prismis context lock` — disable auto-updates temporarily
- TUI: `:context rollback` command

### Config (TOML)
```toml
[context]
auto_update = true
update_interval_days = 30    # Configurable window
backup_count = 10            # Keep last N backups
# min_votes_before_update = 5  # Optional threshold
```

### LLM Update Flow
Every 30 days (or manual trigger), the system:
1. Collects all articles with votes in the window
2. Sends to LLM:
   - Current context.md
   - Articles + their ratings (title, summary, topics, source, up/down)
   - Aggregate vote statistics
3. LLM generates updated context.md:
   - Adds new HIGH topics (heavily upvoted)
   - Promotes topics (LOW→MED, MED→HIGH)
   - Demotes topics (HIGH→MED, MED→LOW) — **never removes**
   - Adds "Not Interested" entries for downvoted patterns
   - Preserves user voice/style
4. System backs up old context.md
5. Writes new version

---

## ✅ Decisions Made

1. **Naming:** `user_feedback` (Salt's naming) — don't care, keep it
2. **Update trigger:** Time-based, **30 days**, configurable in TOML
3. **Approval gate:** Fully automatic with backups (no approval required)
4. **Scope:** Downvotes = **downgrade topics**, not removal. LLM sees articles + ratings.
5. **CLI voting:** TBD — TUI + Web UI priority first

---

## 🗓️ Timeline Estimate

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| 1. Merge Salt's PRs | 1-2 days (review) | None |
| 2. TUI Support | 2-3 days | Phase 1 |
| 3. Deprecate interesting | 1 day | Phase 2 |
| 4. Auto-Update | 3-5 days | Phase 1, 3 |
| 5. Preference Injection | 1-2 days (review) | Phase 4 |
| 6. Polish | 2-3 days | Phase 5 |

**Total:** ~2-3 weeks for full loop

---

## 🤝 Credits

- **0xsalt** — PRs #6, #7, #8 (voting infrastructure, statistics, preference injection)
- **nickpending** — `interesting_override`, context analyzer, vision
- **TARS** — This spec, 85% humor setting 🤖
