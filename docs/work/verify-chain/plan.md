---
work_order: docs/work/wo-verify-chain.md
status: planned
---

# Plan: verify --chain

## Summary

Extend `prismis-daemon verify` with a `--chain` mode that drives the real `DaemonOrchestrator`
against one `--source` URL through real collaborators (real fetchers, real `ContentSummarizer`,
real `ContentEvaluator`, real `Embedder`, real `Notifier`), against a throwaway temp-XDG database,
using the operator's real LLM config/credentials. It mines the observability JSONL the run itself
writes, plus `fetch_source_content`'s own returned stats for one named, deliberate exception
(dedup), to render a per-link report where every link's state is one of six distinguishable values.
Fixes `observability.py` to honor `XDG_DATA_HOME` and to stamp a process-scoped run id onto every
event it logs, and instruments the three dark links named in D-DARK: `embeddings.py`, `notifier.py`,
and `Storage.create_or_update_content`.

## Revision note

This plan was revised after operator review. Two scope expansions were folded in, both previously
declined as unilateral moves and now explicitly granted by an updated work order:

1. **Attribution moved to the observability layer.** The original design filtered by a
   sentinel-bounded timestamp window plus `source_id` for fetcher events. The operator identified a
   real defect: `llm.call` events carry no `source_id` and no run identifier at all, so on cerebro —
   where the daemon is running right now with `fetch_interval = 90` — a chain run overlapping a
   daemon cycle would count the daemon's own summarize/evaluate calls as the chain's, inflating cost
   and item counts in a report that looks right and isn't. `observability.py` now stamps a
   process-scoped run id onto every event when one is set, closing this for every event type,
   including ones added later. The sentinel events are dropped — see Alternatives Considered.
2. **`Storage.create_or_update_content` is now instrumented as a third dark link**, alongside
   `embeddings.py` and `notifier.py`. It matches `add_content`'s existing `obs_log` shape in the same
   file (P15). Link 6 (store) is now reported from a real event with a real duration, not inferred
   from returned stats — dedup (link 2) is now the sole remaining inference-based exception.

## Grounding correction to the work order's stated facts — read before the rest of this plan

The work order (pre-revision) said "obs_log coverage: ... storage YES." That was true of
`storage.py` in general but false for the specific method `fetch_source_content` actually calls to
store an item. The updated work order's D-DARK section now states this directly and credits the
correction; it is restated here because the module table and the store link's design depend on it:

- `Storage.add_content` (storage.py:159-236) emits `db.insert` on both the duplicate path
  (storage.py:228-236) and the success path (storage.py:284-293). Nothing in the pipeline calls
  this method.
- `Storage.create_or_update_content` (storage.py:309-449) — the method `orchestrator.py:216` and
  `orchestrator.py:342` actually call — had, before this plan's change, zero `obs_log` calls
  anywhere in its body. Re-verified this pass: `grep -n "obs_log(" daemon/src/prismis_daemon/storage.py`
  returns 11 matches (storage.py:228, 286, 299, 789, 801, 1078, 1091, 1122, 1135, 1192, 1204), none
  between lines 309 and 449.

This is now fixed directly (file #4 below), not worked around.

A second correction, found while re-verifying the attribution redesign this pass:
`config.fetch_interval` (config.py:20, validated at config.py:115-118) is **not** actually wired to
the production scheduler. `__main__.py`'s `run_scheduler` hardcodes
`IntervalTrigger(minutes=30)` for the non-test-mode path (`__main__.py:96-97`) regardless of the
config value — `grep -rn "fetch_interval" daemon/src/prismis_daemon` shows no call site ever reads
`config.fetch_interval` to build the trigger. So the work order's cited `fetch_interval = 90` (a
real config value on cerebro) and the scheduler's actual ~30-minute period are two different
numbers that disagree with each other — a separate, pre-existing dead-config-value bug, out of this
work order's scope (flagged as a `concerns` item, not fixed here). It does not change this plan's
attribution design either way: the point of moving to a stamped `run_id` (below) is that correctness
no longer depends on how often the daemon's cycle runs at all — exact match, not a probability
argument tied to any particular cadence number.

## Files to modify or create

### 1. `daemon/src/prismis_daemon/observability.py` (MODIFY)

Two changes to this file.

**1a. `XDG_DATA_HOME` fix (unchanged from the prior revision).** `ObservabilityLogger.__init__`
(observability.py:14-25) resolves `base_dir` as `Path.home() / ".local" / "share"` and never reads
`XDG_DATA_HOME`, unlike `database.py`'s two call sites (database.py:28-30, database.py:106-108).
Add `import os` and change the default resolution to:

```python
if base_dir is None:
    xdg_data_home = os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))
    base_dir = Path(xdg_data_home) / "prismis" / "observability"
```

Mirrors `database.py`'s exact pattern (P15).

**1b. Process-scoped run id (new, per operator direction).** Add module-level state and two
functions, mirroring the existing "reset for testing" convention `circuit_breaker.py` already
established (`reset_circuit_breaker`, circuit_breaker.py:185-191 — P15):

```python
_current_run_id: str | None = None


def set_run_id(run_id: str | None) -> None:
    """Set the run id every subsequent log() call in this process stamps onto its event.

    Pass None to clear it. Process-scoped by design: this is a plain module-level global,
    not a file or environment write, so a separate process (the running daemon on cerebro)
    never sees it and its own events are never stamped — nothing to set it there.
    """
    global _current_run_id
    _current_run_id = run_id


def get_run_id() -> str | None:
    """The run id currently in effect for this process, or None."""
    return _current_run_id
```

`ObservabilityLogger.log` (observability.py:27-70) gains one conditional line before building the
JSON entry:

```python
entry = {"ts": datetime.now(UTC).isoformat(), "event": event}
if _current_run_id is not None:
    entry["run_id"] = _current_run_id
entry.update(metadata)
```

When unset (the default at import, and always true in the daemon's own long-running process, which
never calls `set_run_id`), the emitted entry is byte-for-byte the same shape as today —
`{ts, event, **metadata}` — no `run_id` key at all, not even `null`. This is what makes the daemon's
own events unaffected: there is nothing for them to be filtered against, and no existing consumer of
the JSONL sees a schema change. Additive per Constitution Principle VIII (schemas change by adding
fields, never a silent break for existing readers).

`verify_chain.run_chain` (file #4 below) calls `set_run_id(str(uuid.uuid4()))` as its first action
and clears it with `set_run_id(None)` in a `finally` block, so a run id never leaks past the single
chain invocation that set it — relevant because pytest runs a whole suite in one process, so a test
that sets a run id and forgets to clear it would leak into every test that runs after it in the same
session. Every test that exercises `set_run_id` directly (not through `run_chain`, which already
clears it) resets it to `None` in its own teardown, the same discipline `circuit_breaker`'s own
tests already apply via `reset_circuit_breaker`.

### 2. `daemon/src/prismis_daemon/embeddings.py` (MODIFY)

Unchanged from the prior revision. `Embedder.generate_embedding` (embeddings.py:32-56) has no
`obs_log` calls (SC-5, D-DARK). Add `import time` and `from .observability import log as obs_log`.
Wrap the method body in a try/except mirroring `summarizer.py`'s success/error `obs_log` shape
(summarizer.py:129-153):

- success: `obs_log("embedding.generate", model=self.model_name, dimension=<len(vector)>, duration_ms=<int>, status="success")`
- failure: `obs_log("embedding.generate", model=self.model_name, duration_ms=<int>, status="error", error=str(e))`, then **re-raise** — every existing caller (`orchestrator.py:221-233`, `orchestrator.py:347-371`, `orchestrator.py:617-637`) already wraps its own call to `generate_embedding` in a try/except that logs and continues, so re-raising preserves current behavior exactly; only the new event is additive.

### 3. `daemon/src/prismis_daemon/notifier.py` (MODIFY)

Unchanged from the prior revision. `Notifier` (notifier.py) has no `obs_log` calls (SC-5, D-DARK).
Add `import time` and `from .observability import log as obs_log`.

- `notify_new_content` (notifier.py:25-44): when `high_items` is empty after filtering, emit
  `obs_log("notification.send", status="skipped", reason="no_high_priority_items", count=0)` before
  the existing early return. Keep the existing outer try/except around `_send_notification`; on a
  caught exception, emit `obs_log("notification.send", status="error", count=len(high_items), error=str(e))`
  before the existing `logger.warning`.
- `_send_notification` (notifier.py:46-80): time the call with `time.time()`; after
  `subprocess.run`, emit `obs_log("notification.send", status="success" if result.returncode == 0 else "error", count=len(high_items), duration_ms=<int>, error=result.stderr if result.returncode != 0 else None)`.

This makes the subprocess non-zero-exit case — today just a `logger.warning` — a genuinely
distinguishable observability event, closing the exact "acknowledge an action before the thing that
can refuse it has run" gap Constitution Principle II names.

### 4. `daemon/src/prismis_daemon/storage.py` (MODIFY — new in this revision)

`Storage.create_or_update_content` (storage.py:309-449) gets `obs_log` calls matching
`add_content`'s existing shape in the same file (storage.py:228-236: `"db.insert"`, `table="content"`,
`operation=...`, `row_count`, `duration_ms`, `status` — P15, and the operator's explicit instruction
to match it). `time` is already imported at module scope (used by `add_content`), no new import
needed. Add `start_time = time.time()` as the first line of the method, then:

- update branch (storage.py:374-395, existing content): before `return existing["id"], False`, emit
  `obs_log("db.insert", table="content", operation="create_or_update_content", row_count=1, duration_ms=<int>, status="updated")`.
- create branch (storage.py:397-444, new content): before `return item.id, True`, emit
  `obs_log("db.insert", table="content", operation="create_or_update_content", row_count=1, duration_ms=<int>, status="created")`.
- error branch (storage.py:446-448): before `raise sqlite3.Error(...)`, emit
  `obs_log("db.insert", table="content", operation="create_or_update_content", error=str(e), duration_ms=<int>, status="error")`.

Event name stays `"db.insert"` for all three outcomes, matching `add_content`'s own convention of
using one event name and distinguishing outcomes via `status` (`"duplicate"`/`"success"`/`"error"`
there; `"created"`/`"updated"`/`"error"` here — different vocabulary because `create_or_update_content`
has no duplicate-skip branch, that filtering already happened upstream in
`fetch_source_content`'s dedup step).

### 5. `daemon/src/prismis_daemon/verify_chain.py` (NEW)

The chain module. Named to match SC-2's verification script (`CHAIN=daemon/src/prismis_daemon/verify_chain.py`).

Imports: `Config`, `init_db` (from `.database`), `ContentEvaluator`, `RSSFetcher`/`RedditFetcher`/
`YouTubeFetcher`/`FileFetcher`, `Notifier`, `log as obs_log`, `set_run_id`, `get_logger` (from
`.observability`), `DaemonOrchestrator`, `Storage`, `ContentSummarizer`, `Embedder`,
`ContentDeepExtractor`. `typer` is not imported here — the CLI surface stays in `__main__.py`; this
module is a library the command calls. **Never imports** `feedparser`, `praw`, `yt_dlp`, `httpx`,
`sentence_transformers`, or `llm_core` (SC-1's mechanical check).

Public surface:

```python
VALID_SOURCE_TYPES = ("rss", "reddit", "youtube", "file")

@dataclass
class LinkStatus:
    name: str                  # "fetch" | "dedup" | "summarize" | "evaluate" | "deep_extract"
                                # | "store" | "embed" | "notify"
    status: str                 # "ran" | "empty" | "never-reached" | "skipped-by-flag"
                                # | "skipped" | "circuit-open" | "error"
    duration_ms: int | None
    detail: str
    cost_usd: float | None = None
    model: str | None = None

def build_source(storage: Storage, url: str, source_type: str) -> dict[str, Any]:
    """Insert a real row into the temp DB's sources table via Storage.add_source
    (storage.py:75-118) and return the source dict fetch_source_content expects."""

def setup_isolated_run(url: str, source_type: str) -> tuple[Storage, dict[str, Any]]:
    """SC-1b ordering: init_db() BEFORE Storage() BEFORE build_source().
    Storage.__init__ (storage.py:41-45) eagerly opens a test connection, so against a
    fresh XDG_DATA_HOME with no db file it raises unless the schema exists first."""

def read_run_events(base_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Read today's JSONL and return every event whose run_id matches. No time-window
    logic needed — see 'Run attribution' below."""

def render_report(
    stats: dict[str, Any],
    events: list[dict[str, Any]],
    source_id: str,
    full: bool,
) -> list[LinkStatus]:
    """Pure aggregation: turns (returned stats, this run's events) into one LinkStatus
    per in-scope link. No I/O, no collaborators — the piece SC-8 requires be directly
    unit-tested without credentials or network."""

def run_chain(source_url: str, source_type: str, full: bool, console: Console) -> int:
    """Sets the run id first. Loads real Config, builds real collaborators (mirrors
    __main__.py:405-445), calls setup_isolated_run, calls
    orchestrator.fetch_source_content(...), conditionally calls
    notifier.notify_new_content(...) under --full (mirrors orchestrator.py:496-500 —
    calls the same production method with the same data, not a reimplementation),
    clears the run id in a finally block, calls read_run_events + render_report,
    prints a Rich table, returns an exit code (1 if any in-scope link is "error" or
    "circuit-open", else 0)."""
```

Per-link design (grounded in the reads above):

- **Link 1 (fetch)**: from `fetcher.complete`/`fetcher.error` events (rss.py:151-159,164-173;
  reddit.py:146-154,160-168; youtube.py:107-116,123-132; file.py:68-217), already restricted to
  this run by `run_id` (see "Run attribution" below); additionally filtered to
  `source_id == <synthetic source id>` as a cheap defense-in-depth check, since these events carry
  `source_id` for free. `status`/`duration_ms` direct from the event.
- **Link 2 (dedup)**: `Storage.get_existing_external_ids` (storage.py:479) has no `obs_log` call —
  confirmed by the same grep above, and this is the **sole remaining named exception** now that
  store (link 6) is instrumented. Per the work order's own invitation ("decide whether the report
  infers it by diffing counts... and sanction that explicitly"), this link is reported as
  **inferred**: `filtered = stats["items_fetched"] - stats["items_processed"]`. `duration_ms = None`
  ("folded into fetch — dedup is in-process list filtering with no separate timer, orchestrator.py:162-177").
- **Link 3 (summarize)** / **Link 4 (evaluate)**: from `llm.call` events with
  `action="summarize"`/`action="evaluate"` (summarizer.py:129-153, evaluator.py:219-244), restricted
  to this run's `run_id` (this is exactly the case the operator flagged — these events carry no
  `source_id`, so `run_id` is the *only* correct discriminator, not a defense-in-depth extra).
  `status`: `never-reached` if `stats["items_processed"] == 0`; `circuit-open` — **the sole remaining
  deliberate exception to reading returned stats**, sanctioned by SC-3 — if `stats["errors"]`
  contains an entry matching `"circuit breaker is open"` (the exact substring raised at
  summarizer.py:104-107 and evaluator.py:194-197, both before their own try block, so no `llm.call`
  event exists for that item) and zero matching `llm.call` events exist for this run; `empty` if
  items were processed but zero matching events exist and no circuit-open string is present (e.g.,
  every item had blank content — `summarizer.py:70-72` returns `None` before ever calling the LLM);
  `ran` if `>=1` matching event exists (aggregates count, total `duration_ms`, total `cost_usd`, the
  model name(s) seen); `error` if `stats["errors"]` contains an entry matching `"Failed to analyze item"`
  that is not the circuit-open pattern. Because summarize always runs before evaluate per item
  (orchestrator.py:244 before orchestrator.py:265) and both share one circuit breaker keyed by the
  same `config.llm_light_service` name (`get_circuit_breaker(service_name)`, circuit_breaker.py:178-182),
  a circuit-open failure always surfaces at the summarize stage first — link 4 is reported
  `never-reached` in that case, not a second `circuit-open`, and this asymmetry is called out in the
  report's `detail` text.
- **Link 5 (deep_extract, `--full` only)**: `skipped-by-flag` when `full=False`; `skipped` (detail:
  "deep service not configured") when `full=True` and `config.llm_deep_service is None`; otherwise
  from `llm.call` events with `action="deep_extract"` (deep_extractor.py:118-140), restricted by
  `run_id`, same ran/empty/error logic as links 3/4. **Named limitation, not solved**:
  `deep_extractor.py`'s circuit check (deep_extractor.py:92-97) raises `CircuitOpenError` before its
  own try block — the same bug shape as summarizer/evaluator — but the orchestrator's caller
  (`orchestrator.py:321-339`) catches and swallows it under INV-002 without adding to
  `stats["errors"]`, so a deep-extract circuit-open is invisible to both the JSONL and the returned
  stats. SC-3's own text names only `summarizer.py` and `evaluator.py`; this is flagged as a
  concern, not treated as in-scope to fix.
- **Link 6 (store)**: from the new `db.insert` events with `operation="create_or_update_content"`
  (storage.py, file #4 above), restricted by `run_id`. `status`: `never-reached` if
  `stats["items_processed"] == 0`; `ran` if `>=1` matching event with `status in ("created", "updated")`
  exists (aggregates count of each, total `duration_ms`; detail: "N created, M updated"); `error` if
  any matching event has `status="error"`, or (belt-and-suspenders, in case an item errored before
  ever reaching storage) `stats["errors"]` is non-empty and zero matching events exist despite
  `items_processed > 0`. No `empty` state is reachable: every item that is not caught by the
  per-item exception handler reaches `create_or_update_content` unconditionally
  (orchestrator.py:342) and that call always returns either the update or the create branch — there
  is no silent no-op path in this method, unlike `add_content`'s duplicate branch. No cost/model
  (not an LLM link).
- **Link 7 (embed)**: from the new `embedding.generate` events, restricted by `run_id`, same
  ran/empty/never-reached/error logic. `never-reached` when `items_processed == 0`.
- **Link 8 (serve)**: out of scope — omitted from the report entirely, matching the work order's own
  "Out of Scope: Link 8 (serve)."
- **Link 9 (notify, `--full` only)**: `skipped-by-flag` when `full=False`; when `full=True`: `empty`
  (detail: "no HIGH priority items") if `stats["new_high_priority_items"]` is empty — matches
  production's own gate at `orchestrator.py:496`, so `notifier.notify_new_content` is never called
  at all in that case, exactly like production; otherwise from the new `notification.send` event,
  restricted by `run_id`.

**Run attribution (SC-8b) — the selection rule, stated explicitly:**

`observability.py` now stamps `run_id` onto every event it logs whenever `set_run_id` has been
called in that process (file #1b above). This does the entire correctness job SC-8b and the
operator's message require, for every event type uniformly — `fetcher.*`, `llm.call`, `db.insert`,
`embedding.generate`, `notification.send` — with no per-call-site change anywhere else in the
pipeline and no gap for an event type added later.

1. `run_chain` generates `run_id = str(uuid.uuid4())` and calls `set_run_id(run_id)` as its first
   action, before `setup_isolated_run` or any collaborator is constructed.
2. Every `obs_log(...)` call made anywhere in this process for the rest of the run — by the
   fetchers, `storage.py`, `summarizer.py`, `evaluator.py`, `embeddings.py`, `notifier.py`,
   `deep_extractor.py`, `circuit_breaker.py` — carries `run_id` in its JSON line, because they all
   funnel through the same module-level `ObservabilityLogger.log`.
3. `run_chain` calls `set_run_id(None)` in a `finally` block once the pipeline call(s) return
   (success or exception), so the run id never survives past this one invocation.
4. `read_run_events(base_dir, run_id)` opens today's JSONL file and returns every line whose parsed
   `run_id` field equals this run's id. No timestamp math, no window, no boundary edge case.
5. `render_report` additionally filters `fetcher.*` events to `source_id == <this run's synthetic
   source id>` — redundant for correctness once `run_id` filtering is exact, but free (the field is
   already on the event) and catches a bug class `run_id` alone would not (e.g. the wrong source
   dict passed into the same run).

**Why the sentinel design is dropped, not kept alongside this**: the sentinel events
(`verify_chain.run` phase start/end + timestamp window) existed to solve exactly the attribution
problem `run_id` now solves directly and exactly, for every event type including `llm.call` — which
the sentinel design could not do, since it had no way to tag an `llm.call` event with anything
beyond "happened between these two timestamps." Keeping both would be two mechanisms doing one job;
Constitution Principle VII requires new machinery to be justified against the simpler alternative it
displaces, and here the simpler alternative (the run id alone) fully subsumes what the sentinels
added. Dropped.

**Daemon-unaffected guarantee, restated concretely**: the daemon's own process (running on cerebro)
never calls `verify_chain.run_chain` and therefore never calls `observability.set_run_id`.
`_current_run_id` in the daemon's process stays `None` for its entire lifetime, so every event the
daemon emits has the exact same `{ts, event, **metadata}` shape it has today — no `run_id` key, not
even `null` — and can never match any chain run's `run_id` filter by construction, not by
probability. This holds regardless of how often the daemon's cycle actually runs (see the second
Grounding correction above on `fetch_interval` not being wired to the scheduler) — the guarantee
does not depend on cadence at all.

### 6. `daemon/src/prismis_daemon/__main__.py` (MODIFY)

Unchanged from the prior revision. `verify()` (`__main__.py:701-770`) gains three new
`typer.Option` parameters, all defaulted so `prismis-daemon verify` with no flags is byte-for-byte
the same call it is today (SC-7):

```python
@app.command()
def verify(
    chain: bool = typer.Option(False, "--chain", help="Drive one source through the real pipeline end to end"),
    source: str | None = typer.Option(None, "--source", help="Source URL to drive (required with --chain)"),
    source_type: str = typer.Option("rss", "--type", help="Source type: rss|reddit|youtube|file"),
    full: bool = typer.Option(False, "--full", help="Also run deep extraction and notify (--chain only)"),
) -> None:
    """..."""
    if chain:
        if not source:
            console.print("[red]✗ --chain requires --source <url>[/red]")
            sys.exit(1)
        if source_type not in ("rss", "reddit", "youtube", "file"):
            console.print(f"[red]✗ invalid --type: {source_type}[/red]")
            sys.exit(1)
        from .verify_chain import run_chain
        sys.exit(run_chain(source, source_type, full, console))

    # --- everything below this line is the existing body, byte-for-byte unchanged ---
    import llm_core
    ...
```

The existing body (config / light / deep / active-sources checks, roll-up, `sys.exit`) is not
touched. This is the smallest change that satisfies D-EXTEND: one early-return branch, added above
unmodified code.

## Test files (new)

- `daemon/tests/unit/test_observability_unit.py` — `ObservabilityLogger()` (no `base_dir`) resolves
  under `XDG_DATA_HOME` from the (autouse-sealed) test environment, not `Path.home()`; a logged
  event round-trips through the real file. **New in this revision**: with no run id set,
  `get_run_id()` is `None` and a logged event's JSON line has no `"run_id"` key at all (proves the
  daemon-unaffected guarantee for real, not by inspection); `set_run_id("x")` then logging an event
  produces a line with `"run_id": "x"`; `set_run_id(None)` clears it back to the no-key shape. Test
  teardown always calls `set_run_id(None)` regardless of outcome, mirroring
  `circuit_breaker.reset_circuit_breaker`'s existing "reset for testing" convention, to prevent
  cross-test leakage within one pytest process.
- `daemon/tests/unit/test_embeddings_observability_unit.py` — real `Embedder().generate_embedding(...)`
  (the model is already cached for CI — `.github/workflows/ci.yml:58-64` caches
  `~/.cache/huggingface` keyed on `embeddings.py`'s hash specifically because production code
  already exercises the real model in tests); asserts an `embedding.generate` event lands in the
  JSONL with `status="success"` and a `dimension`. A second test forces a failure (constructing
  `Embedder(model_name="not-a-real-model")` so the real `SentenceTransformer(...)` constructor
  raises for real — no mock) and asserts `status="error"`.
- `daemon/tests/unit/test_notifier_observability_unit.py` — real `subprocess.run` against `echo`
  (mirrors `test_verify_subcommand_unit.py`'s own `command = "echo"` fixture convention) for the
  success path, and a real failing command (e.g. `sh -c 'exit 1'`) for the error path; asserts the
  `notification.send` events. No mocking of `Notifier` or `subprocess`.
- `daemon/tests/unit/test_storage_observability_unit.py` — **new in this revision**. Real `Storage`
  against the `test_db` fixture (conftest.py:141-164, real SQLite, no mock). Calls
  `storage.create_or_update_content(item_dict)` once (asserts a `db.insert` event with
  `operation="create_or_update_content"`, `status="created"`) and again with the same `external_id`
  (asserts a second event with `status="updated"`). A third test forces the error branch for real —
  e.g. a `source_id` that violates the `sources` FK (schema.sql:64) — and asserts `status="error"`.
- `daemon/tests/unit/test_verify_chain_source_unit.py` — `setup_isolated_run` against a genuinely
  empty temp `XDG_DATA_HOME` proves the init-before-construct ordering (SC-1b); `build_source`
  round-trips through `Storage.add_source` and produces a dict `fetch_source_content` accepts;
  invalid `--type` rejected.
- `daemon/tests/unit/test_verify_chain_report_unit.py` — `render_report` unit tests covering all
  six status values across the in-scope links, including: the SC-3 four-state distinction; the
  circuit-open case, built from a **real** `ContentSummarizer.summarize_with_analysis` call against
  a circuit forced open via the real `CircuitBreaker`/`reset_circuit_breaker`
  (`circuit_breaker.py:185-191`) with `record_failure(RuntimeError("insufficient_quota"))` called 3x
  — no network, no mock, the real early-raise branch (summarizer.py:100-107) — so the exact
  exception string `render_report` pattern-matches is captured from real code, not guessed. Also
  carries the SC-1 mechanical check: reads `verify_chain.py`'s own source text and asserts none of
  `feedparser`, `praw`, `yt_dlp`, `httpx`, `sentence_transformers` are imported, and `llm_core` is
  not imported/called.
- `daemon/tests/unit/test_verify_chain_events_unit.py` — **rewritten in this revision** (was the
  sentinel/window test). `read_run_events` proven against a real JSONL file seeded with: (a) a
  foreign event carrying a *different* `run_id`, (b) an old-style event carrying *no* `run_id` key
  at all (simulating the daemon's own, un-set-run-id shape), and (c) this run's own real events
  (via `set_run_id` + real `obs_log` calls). Asserts (a) and (b) are excluded and (c) is included —
  this is SC-8b's "seed the file with foreign events that must not appear in the output," now
  against the real selection mechanism instead of a timestamp window.
- `daemon/tests/integration/test_verify_chain_fetch_failure_integration.py` — SC-2 (grep-based,
  matches the work order's own script), SC-4 (a malformed `--source` — e.g. `"not-a-real-url"` —
  makes the **real** `RSSFetcher` raise `httpx.InvalidURL` synchronously, no network I/O; asserts
  both the absence of any `llm.call` event for this run's `run_id` AND the presence of a
  `fetcher.error` event AND `stats["errors"]` containing `"Failed to fetch from"`, per SC-4's "must
  additionally show" requirement — not absence alone, P29), and the CLI argument-surface path
  (`verify(chain=True, source=None, ...)` exits 1 before touching `Config`;
  `verify(chain=True, source="not-a-real-url", ...)` dispatches through the real `run_chain` end to
  end, network-free, credential-free, and exits non-zero).

## Alternatives Considered

**1. Run attribution.**

- **(Chosen, per operator direction) Process-scoped run id stamped by `observability.log()` itself.**
  One module-level global plus two functions in the file already being modified for the XDG fix;
  every event of every type gets exact attribution with zero change to any other call site.
- **Rejected (this plan's original design, superseded): sentinel start/end events + timestamp
  window + `source_id` for fetcher events.** Solved attribution for `fetcher.*` and (would have,
  had store been instrumented) `db.insert` events, both of which carry `source_id`, but had no
  mechanism to attribute `llm.call` events beyond "happened in this time window" — exactly the gap
  the operator identified as a real defect. Any overlap between a chain run and a live daemon
  cycle, whatever the daemon's actual cadence, would have silently counted the daemon's own
  `llm.call` spend as the chain's; the cadence figure itself does not matter to this argument (see
  the second Grounding correction above — the work order's cited `fetch_interval = 90` is not even
  the number that governs the scheduler in the deployed code, which hardcodes 30 minutes, so neither
  number was safe to reason from probabilistically).
- **Rejected: thread a `run_id` kwarg through every existing `obs_log(...)` call site** (fetchers,
  storage, summarizer, evaluator, circuit breaker, deep extractor — 20+ call sites across 8+
  files). Would give every event perfect attribution via the same mechanism ultimately chosen, but
  by touching every instrumented production module instead of the one file (`observability.py`)
  that already mediates all of them. Loses to the chosen design on P3 (reversibility — a global in
  one file is trivially revertible; a signature change across 8 files is not) with no correctness
  advantage, since `observability.py`'s own `log()` already sees every call.
- **Rejected: chain writes to its own dedicated JSONL file instead of the shared daily file.**
  Solves attribution perfectly (no sharing, no filter needed at all) but stops testing the actual
  thing D-REPORT cares about — that the chain reads the *same* artifact the daemon authoritatively
  writes, including its daily-rotation and concurrent-write behavior (P29's "prefer mining from the
  artifact the system itself authoritatively writes"). A parallel file is exactly the "chain
  reimplements a link" failure mode the work order's Stakes section calls out, just relocated to
  reporting instead of fetching.

**2. The store link's instrumentation shape (link 6).**

- **(Chosen, per operator direction) `obs_log` inside `Storage.create_or_update_content`, matching
  `add_content`'s existing shape in the same file.** Real event, real duration, consistent with how
  every other in-scope link is reported; no more inference for this link.
- **Rejected: a new, differently-named event** (e.g. `"content.upsert"` instead of reusing
  `"db.insert"`). Would be marginally more descriptive of what actually happened (create vs. update
  vs. duplicate-skip is a different shape than "insert"), but `add_content` in the same file already
  established `"db.insert"` with outcome carried in `status`, and a second event name for the same
  logical operation (writing to the `content` table) is inconsistent for no real gain — P15.
- **(Superseded) Infer status from `fetch_source_content`'s returned stats; report `duration_ms=None`.**
  This plan's prior revision, correct at the time given D-DARK's then-locked two-file scope. No
  longer applicable — D-DARK now names `storage.py` explicitly, and the operator granted this
  expansion after the planner declined to make it unilaterally.

## Pick Justification

- **P15 (existing patterns when they fit)**: the `XDG_DATA_HOME` fix mirrors `database.py`
  verbatim; the run-id "set/get, clear via None" shape mirrors `circuit_breaker.py`'s existing
  `reset_circuit_breaker`-for-testing convention; the new `obs_log` shapes in
  `embeddings.py`/`notifier.py` mirror `summarizer.py`'s success/error pair; the `storage.py`
  instrumentation matches `add_content`'s existing `"db.insert"` shape in the same file, per the
  operator's explicit instruction; `build_source` reuses `Storage.add_source` rather than
  hand-writing SQL (which is what `_seed_source` already does in
  `test_verify_subcommand_unit.py:128-135`).
- **P3 (reversibility)**: the run-id mechanism is a global plus two functions in one file already
  being touched, trivially revertible; the rejected `run_id`-threading-through-every-call-site
  alternative touches 8+ production files and is not.
- **Constitution Principle VII (Simplicity & YAGNI)**: the sentinel start/end events from this
  plan's prior revision are dropped because the run-id mechanism fully subsumes what they did —
  keeping both would be an unjustified parallel mechanism doing one job.
- **Constitution Principle VIII (Additive Change & Versioning)**: the `run_id` field is added to the
  observability event schema, never required, absent entirely when unset — an addition, not a
  breaking change to any existing or future reader of the JSONL.
- **P16 (root cause over symptom)**: fixing `observability.py`'s `XDG_DATA_HOME` gap at its source
  (matching every sibling module's resolution) rather than working around it is the root-cause fix;
  D-ISOLATE requires this explicitly. Likewise, instrumenting `create_or_update_content` directly
  is the root-cause fix for link 6's missing record — Principle III names this as a real production
  gap, not merely a reporting inconvenience for this chain.
- **Constitution Principle I** (tests prove the logic; no mocks for internal code; LLM providers are
  the only permitted mock boundary, and this chain must not mock even those): every new test above
  exercises real collaborators — real `Embedder`/`SentenceTransformer`, real `subprocess`, real
  `CircuitBreaker`, real `RSSFetcher` hitting a real (malformed) URL, real `Storage` against a real
  SQLite DB, real JSONL files on disk. No `unittest.mock.patch` targets any of `Notifier`,
  `Embedder`, `Storage`, `RSSFetcher`, or `run_chain` itself.
- **Constitution Principle II** (failures are distinguishable): the entire per-link `status` design
  exists to give this chain's report six visibly different states instead of one blank cell, and the
  `notifier.py` fix turns a swallowed non-zero subprocess exit into a distinguishable event.
- **Constitution Principle III** (behavior leaves a record): D-DARK's three instrumented links are
  exactly this — the pipeline runs unattended on a schedule and `embeddings.py`, `notifier.py`, and
  `Storage.create_or_update_content` currently leave zero trace of what they decided.
- **Constitution Principle V** (dependencies point one way): the chain does not import
  `cli/src/cli/source.py`'s `detect_and_normalize_source_url` for type inference, even though it is
  the closest existing pattern, because the daemon may never import from the CLI package. A
  `--type` flag defaulting to `"rss"` is the smaller, dependency-clean alternative.
- **B1/B2 (don't guess; don't agree to be helpful)**: the original "storage YES" claim was corrected
  with a citation rather than accepted, and this revision's attribution redesign is accepted because
  the operator's counter-evidence (llm.call carries no source_id, checked directly against the call
  sites) is itself correct — verified independently in this pass, not merely trusted. The
  `fetch_interval` cadence figure cited alongside that counter-evidence was checked too, and it
  turned out not to be wired to the scheduler at all — corrected above rather than repeated as fact.

## Success Criteria mapping

| SC | How this plan satisfies it |
|----|------|
| SC-1 | `verify_chain.py` calls `DaemonOrchestrator.fetch_source_content` for the entire pipeline; mechanical import check in `test_verify_chain_report_unit.py`. |
| SC-1b | `setup_isolated_run` calls `init_db()` before `Storage()`; proven against a genuinely empty temp dir in `test_verify_chain_source_unit.py`. |
| SC-2 | Grep-based test matches the work order's own script; chain never calls `get_active_sources`; observability fix makes the JSONL land under `XDG_DATA_HOME` too. |
| SC-3 | Six-state `LinkStatus.status` vocabulary; circuit-open handled as the sole remaining deliberate exception to reading the JSONL (sanctioned by SC-3 itself). |
| SC-4 | Real malformed-URL fetch failure test; asserts absence of any `llm.call` event carrying this run's `run_id` AND presence of the fetch-failure reason, not absence alone. |
| SC-5 | `embeddings.py`/`notifier.py` instrumented; `grep -c "obs_log("` on both files is `> 0` per the work order's own verification script. |
| SC-6 | `full=False` renders links 5/9 as `skipped-by-flag`; `full=True` attempts both (link 5 further gated on `config.llm_deep_service`, matching production's own conditional). |
| SC-7 | Existing `verify()` body is untouched below the new early-return; existing tests in `test_verify_subcommand_unit.py` require no edits. |
| SC-8 | Every new unit test is real-collaborator, network-free, credential-free — see "Test files" above; none reference `PRISMIS_LIVE_NETWORK_TESTS`. |
| SC-8b | Process-scoped `run_id` stamped by `observability.log()`, selection rule stated explicitly above; proven in `test_verify_chain_events_unit.py` and `test_observability_unit.py` by seeding foreign events (different run_id, and no run_id at all) that must not appear in the output. |
| SC-9 | New/modified modules ship with `[tool.ruff]`/`[tool.pyright]` coverage already configured at the `daemon/` unit level (`daemon/pyproject.toml:71-76`); no new dependency. |
| SC-10 | Not testable from this plan — recorded in the work order's Outputs as a manual, unguarded cerebro run per the work order's own instruction. |

## Risks

| Risk | Severity | Mitigation |
|------|----------|------------|
| `_current_run_id` is a module-level global; pytest runs a whole suite in one process, so a test that sets it and fails before clearing could leak into a later, unrelated test | MEDIUM | Every test that calls `set_run_id` directly resets it to `None` in teardown regardless of outcome (mirrors `circuit_breaker.reset_circuit_breaker`'s existing convention); `run_chain` itself clears it in a `finally` block so the normal path is self-cleaning. |
| Deep-extract's circuit-open case (deep_extractor.py:92-97) is invisible to both the JSONL and returned stats — swallowed by orchestrator.py's INV-002 catch | MEDIUM | Named as a concern for a separate, scoped follow-up; SC-3's own text names only summarizer.py/evaluator.py, so this is outside this work order's letter. Only reachable under `--full`. |
| `--full`'s `dataclasses.replace(config, auto_extract="all")` diverges from the operator's real `auto_extract` setting, so a chain run under `--full` does not prove the operator's actual production config will trigger deep extraction | LOW | This is what makes `--full`'s promise ("includes both") deterministic and testable at all; the deep *service* (credentials, model, `llm_deep_service` name) is still the real one — only the priority-threshold gate is overridden, and this is called out in the report's `detail` text for link 5. |
| `RSSFetcher`'s real `httpx.Client(timeout=30)` default could make a live cerebro run (not the test suite) slow against a truly hanging host | LOW | Only affects the manual SC-10 run, not the gate; the work order's own real-run recipe already expects the operator to pick a live, working source URL. |
| Rich table output width/column choices are a UX decision, not specified by the work order | LOW | Any Rich `Table` reusing the existing `console` (already imported in `__main__.py` and `orchestrator.py`) satisfies the letter of "reports... status, duration... cost... model"; exact column layout is left to the builder's judgment, not a spec gap. |

## Demo command

Runnable now, on this machine, network-free (does not require SC-10's cerebro run):

```bash
cd /Users/rudy/development/projects/prismis/daemon

# 1. Existing behavior is provably unchanged (SC-7)
uv run python -m prismis_daemon verify --help
# Expected: shows the new --chain/--source/--type/--full options in the help text,
# but invoking `verify` with zero flags still runs exactly the four original checks.

# 2. Chain mode, network-free failure path (SC-2, SC-4)
XDG_DATA_HOME=$(mktemp -d) uv run python -m prismis_daemon verify --chain --source not-a-real-url
# Expected (shape, not a captured transcript — SC-10 is the real captured run):
#   link 1 fetch     : error   (httpx.InvalidURL, no network attempted)
#   link 2 dedup     : never-reached
#   link 3 summarize : never-reached
#   link 4 evaluate  : never-reached
#   link 6 store     : never-reached
#   link 7 embed     : never-reached
#   (link 5 deep_extract, link 9 notify: skipped-by-flag — no --full)
#   exit code: 1
```

## Quality Gates

- `./.specify/verify.sh` — must exit 0, no `VERIFY_UNCOVERED` line (SC-9).
- `(cd daemon && uv run ruff check .)` — new/modified modules must be clean under the enabled rule
  set (`E4,E7,E9,F,B,ASYNC,BLE,ERA,RUF006,RUF012,RUF013,RUF100,ANN401,PGH,S110,S112`); `ERA001` is
  live, so any comment citing `create_or_update_content` must read as prose ("the
  `create_or_update_content` method in `storage.py`"), never as `create_or_update_content(storage.py:309)`,
  which ruff parses as commented-out code.
- `(cd daemon && uv run pyright)` — **covers only `src/prismis_daemon`** per `daemon/pyproject.toml:71-76`
  (`include = ["src/prismis_daemon"]`); a green run says nothing about the new test files. Every new
  test file above still needs to be read for type correctness by the builder/reviewer directly.
- `(cd daemon && uv run pytest -q -k "verify_chain or chain_report or obs")` — the chain's own new
  tests, per the work order's own verification block.
- `(cd daemon && uv run python -m prismis_daemon verify --help)` — SC-7 smoke check.
- **Mutation-verify every new test** (CLAUDE.md Learned Patterns): for each new assertion, disable
  the behavior it protects (delete the `obs_log` call, revert the `XDG_DATA_HOME` read, remove the
  run-id stamping line, remove the circuit-open substring match, etc.), confirm the test goes red,
  then restore. This applies to every test file listed above, not only the ones this plan calls out
  by name.
- No test in the new files rests on absence alone without a distinguishing positive assertion
  alongside it (CLAUDE.md Learned Patterns; SC-4 states this explicitly).

## Settled — do not re-open

Carried verbatim (updated) from the work order's Brief; the builder reads this plan, not the work
order.

- **D-ORCH.** The orchestrator *is* the chain. `DaemonOrchestrator.__init__` takes every
  collaborator injected; `fetch_source_content()` runs the whole per-source pipeline. The chain
  constructs the real orchestrator with real collaborators and calls it with a synthetic source
  dict built from `--source <url>`. It reimplements nothing, and never queries the sources table
  for active sources — the source under test comes from `--source` alone (it does, however, insert
  one row into the temp DB's `sources` table via `Storage.add_source`, required by the `content`
  table's `FOREIGN KEY (source_id) REFERENCES sources(id)` — this is setup, not a query of the
  active-sources list, and SC-2's grep only forbids `get_active_sources` appearing in the module).
- **D-ISOLATE.** Isolation is two knobs — `database.py` resolves the DB from `XDG_DATA_HOME`,
  `config.py` resolves credentials from `XDG_CONFIG_HOME`. Run with `XDG_DATA_HOME` on a temp dir
  and the real config. `observability.py` is fixed to also read `XDG_DATA_HOME`, matching every
  other module, per file #1a above.
- **D-REPORT.** Per-link reporting is mined from the observability JSONL the run itself writes, not
  from instrumentation invented for the test. **Attribution is solved at the observability layer**:
  `observability.py` gains a process-scoped run id that `log()` stamps onto every event when set
  (file #1b above) — this, not a timestamp window, is what makes every link including the LLM links
  correct when a chain run overlaps a live daemon cycle on cerebro. **One remaining deliberate
  exception** to reading the JSONL: circuit-open, sanctioned by SC-3 itself, read from
  `fetch_source_content`'s returned stats because the raise happens before either `summarizer.py` or
  `evaluator.py` reaches its own `obs_log` call.
- **D-DARK.** Three links are dark, not two: `embeddings.py`, `notifier.py`, and
  `Storage.create_or_update_content` (storage.py:309-449) — the method `fetch_source_content`
  actually calls to store an item, unlike the already-instrumented but never-called `add_content` in
  the same file. All three get instrumented (files #2, #3, #4 above). This scope was expanded by
  explicit operator decision after the planner declined to make it unilaterally in the prior
  revision.
- **D-EXTEND.** Extend the existing `verify` subcommand rather than forking a parallel command. Its
  current cheap checks stay as the default; the chain is additive behind `--chain`.
- **D-INIT.** `Storage.__init__` is not lazy; it eagerly opens a connection. The chain initializes
  the schema (`init_db()`) against the temp `XDG_DATA_HOME` before constructing any collaborator.
- **D-DEFAULT.** Default runs links 1–4, 6, 7. Deep extraction (link 5) and notify (link 9) go
  behind `--full`.

**Accepted risk (carried verbatim):** the circuit breaker's `_breakers` registry is a plain
module-level dict, reset on every fresh interpreter. A chain run (separate process) cannot see a
circuit the long-running daemon already opened, or vice versa. No persisted circuit state exists
anywhere in the tree to read instead — accepted rather than solved.

## Constitution Check — Principle I module table

| Module (path) | logic \| glue | Test that proves it, or why glue |
|---|---|---|
| `daemon/src/prismis_daemon/verify_chain.py` (new) | logic | `test_verify_chain_source_unit.py` (source construction, SC-1b ordering, type validation), `test_verify_chain_report_unit.py` (six-state per-link classification, real circuit-open capture, SC-1 mechanical import check), `test_verify_chain_events_unit.py` (SC-8b run-id-based event selection, foreign-event exclusion), `test_verify_chain_fetch_failure_integration.py` (real fetch-failure end-to-end, SC-2, SC-4, argument surface) |
| `daemon/src/prismis_daemon/__main__.py` (modified) | glue | The added branch is a pure dispatch (validate `--source` present, validate `--type`, call `run_chain`, `sys.exit` its return value) with no independent logic beyond argument validation; the validation itself is proven by the argument-surface cases in `test_verify_chain_fetch_failure_integration.py`. All pre-existing `verify()` logic is untouched and remains covered by the existing `test_verify_subcommand_unit.py`. |
| `daemon/src/prismis_daemon/observability.py` (modified) | logic | `test_observability_unit.py` — proves `XDG_DATA_HOME` resolution and the run-id stamping/clearing/unset-shape behavior, each mutation-verified by reverting the relevant line and confirming the test goes red. |
| `daemon/src/prismis_daemon/embeddings.py` (modified) | logic | `test_embeddings_observability_unit.py` — real `SentenceTransformer` call, success and failure paths, mutation-verified. |
| `daemon/src/prismis_daemon/notifier.py` (modified) | logic | `test_notifier_observability_unit.py` — real `subprocess.run` via `echo`/a failing command, success/skipped/error paths, mutation-verified. |
| `daemon/src/prismis_daemon/storage.py` (modified) | logic | `test_storage_observability_unit.py` — real `Storage` against the real `test_db` fixture, created/updated/error paths on `create_or_update_content`, mutation-verified. |
