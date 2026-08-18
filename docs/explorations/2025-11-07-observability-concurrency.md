---
type: exploration
domain: technical
status: implemented
started: 2025-11-06
updated: 2026-02-18
tags: [exploration]
---
# Observability JSONL Concurrency Strategy

**Date:** 2025-11-07
**Context:** Iteration 10 - System Observability & Context Intelligence
**Status:** Decided - Option 1 (fcntl file locking)

## The Problem

Need thread-safe and process-safe JSONL append logging for observability events:
- **Concurrent writers:** APScheduler threads, FastAPI workers, parallel fetchers, separate CLI processes
- **Write frequency:** Dozens of events per minute
- **Target:** `~/.local/share/prismis/observability/YYYY-MM-DD_events.jsonl`
- **Scale:** 1-2 users, 30+ sources, ~500 items/day

## Initial Misconceptions

**First answer:** Just append to file, no locking needed at this scale
- **Wrong:** Ignored threading from APScheduler and FastAPI workers
- **Correction:** Daemon has multiple threads + separate CLI processes → race conditions will corrupt JSONL

**Second answer:** Use `threading.Lock()` only
- **Wrong:** Only handles threads within daemon process, not CLI process writes
- **Correction:** Need both thread-safety AND process-safety

## Architecture Options Analyzed

### Option 1: Simple File Lock (fcntl) ⭐ SELECTED

**Implementation:**
```python
import fcntl
import json
from pathlib import Path
from datetime import datetime

_log_lock = threading.Lock()  # Optional: reduce fcntl calls within process

def log_event(event_name: str, **metadata):
    file_path = Path.home() / ".local/share/prismis/observability" / f"{datetime.now().strftime('%Y-%m-%d')}_events.jsonl"
    file_path.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "event": event_name,
        **metadata
    }

    # Retry wrapper for lock failures
    for attempt in range(3):
        try:
            with open(file_path, "a") as f:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(json.dumps(event) + '\n')
                    f.flush()
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            break
        except BlockingIOError:
            if attempt < 2:
                time.sleep(0.01 * (attempt + 1))
            else:
                print(f"Failed to log event: {event}", file=sys.stderr)
```

**Pros:**
- OS-level guarantees (works across processes)
- Stdlib only (fcntl on Unix)
- Simple implementation (~50 lines with retry)
- Follows existing retry pattern (like SQLite WAL)
- Graceful degradation (print to stderr on failure)

**Cons:**
- Platform-specific (need msvcrt for Windows)
- Lock contention blocks writers (negligible at dozens/minute)

**Performance:** ~7ms per event (acceptable)

### Option 2: Queue + Background Thread

In-memory queue, single writer thread, CLI posts to daemon API.

**Rejected because:**
- Events lost on crash
- Requires daemon running for CLI logging
- More complex (queue + thread + API endpoint)
- Doesn't match "simple, direct" project philosophy

### Option 3: External Tool (rotatelogs)

Pipe to Apache rotatelogs or similar.

**Rejected because:**
- External dependency
- Unix-only
- Breaks "local-first, zero-ops" philosophy

### Option 4: Hybrid Thread+File Lock

threading.Lock for same-process, fcntl for cross-process.

**Rejected because:**
- Most complex implementation
- Performance gain not justified (0.1-7ms vs 7ms)
- Manual rotation logic

## Decision Rationale

**Chose Option 1** for:
1. **Simplicity:** Fewest moving parts, clearest failure modes
2. **Alignment:** Matches existing patterns (WAL retry, pathlib, no external deps)
3. **Correctness:** Guaranteed thread-safe + process-safe
4. **Performance:** 7ms negligible at dozens of events/minute
5. **Philosophy:** "Local-first, zero-ops" - stdlib only

## Implementation Notes

**File location:** `~/.local/share/prismis/observability/YYYY-MM-DD_events.jsonl`

**Retention:** 30-day cleanup policy (separate scheduled job)

**Error handling:**
- 3 retry attempts with exponential backoff (10ms, 20ms)
- Print to stderr on final failure
- System continues (logging failure doesn't break daemon)

**Platform support:**
- Unix: fcntl.flock()
- Windows: Use msvcrt.locking() (different API, same concept)

**Integration:**
- Callable from any module: `from prismis_daemon.observability import obs; obs.log("event.name", **metadata)`
- Daily rotation handled automatically by filename
- No manual file management needed

## Lessons Learned

1. **Don't dismiss concurrency at "small scale"** - Threads and processes matter even at 1-2 users
2. **Check actual architecture** - APScheduler + FastAPI = multiple threads by default
3. **Simplest correct solution wins** - fcntl is simpler than queues, reliable enough for this scale
4. **Follow existing patterns** - WAL retry logic proved the pattern works

## References

- Architecture analysis: `.workflow/artifacts/subagents/ARCHITECTURE-kx9m.md`
- Iteration plan: `.workflow/artifacts/ITERATION.md` (Task 1)
- SQLite concurrency pattern: `daemon/src/prismis_daemon/database.py` (WAL mode + retry)
