"""`prismis-daemon refetch --type <youtube|rss|reddit>`: backfill stored items
whose content `readability.is_readable` rejects (gh #80, wo refetch-unreadable).

Selection (`Storage.get_unreadable_content`) finds non-archived items of the
requested type, oldest fetched first, whose stored content isn't readable --
including the ones analysed before gh #80 that predate `title_only` ever being
written, which `get_readable_external_ids`'s title_only lookup cannot find.

Each selected item is re-extracted through its own fetcher's single-item path:
`YouTubeFetcher.refetch_transcript` for a stored video URL, the shared
`article_extractor.extract_article` for an RSS item's URL, and
`RedditFetcher.refetch_one` rebuilding the item from its permalink via PRAW.
Content that comes back readable is routed through
`DaemonOrchestrator.analyze_and_store_item` -- the one analysis path (SC-1) --
in place, same row, no duplicate. Content that's still not readable leaves the
row's content alone and patches `title_only: true` onto its existing analysis
with no LLM call.

`--dry-run` reports the selection count and an upper-bound cost estimate
derived from the 99th-percentile real summarize/evaluate/classify_kind cost in the
observability log, making no extraction, LLM call or write. A real run reports
each item's outcome and ends with the counts and the summed real cost of the
calls it actually made.
"""

from __future__ import annotations

import json
import math
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from .analysis import get_learned_preferences, title_only_reason
from .article_extractor import extract_article
from .models import ContentItem
from .observability import get_logger as get_obs_logger
from .observability import log as obs_log
from .observability import set_run_id
from .orchestrator import DaemonOrchestrator
from .readability import is_readable

logger = logging.getLogger(__name__)

# File sources are out of scope (their external_id embeds the content hash, and
# none are unreadable); `analyze repair` stays the separate, narrower command it
# already is.
REFETCH_TYPES = ("youtube", "rss", "reddit")

# The obs_log "action" values a summarize+evaluate+classify_kind pass through
# analyze_and_store_item can log (llm_call.py, kind_classifier.py) -- the three
# the dry-run cost estimate sums the 99th percentile of.
_COST_ACTIONS = ("summarize", "evaluate", "classify_kind")


@dataclass
class RefetchOutcome:
    """What happened to one selected item."""

    external_id: str
    title: str
    status: str  # "recovered" | "still_title_only" | "failed"
    detail: str | None = None


@dataclass
class RefetchReport:
    """What one `run_refetch` call did, for `__main__.py`'s refetch command
    to print and for tests to assert on."""

    source_type: str
    dry_run: bool
    selected: int
    outcomes: list[RefetchOutcome] = field(default_factory=list)
    estimated_cost: float | None = None
    real_cost: float | None = None

    @property
    def recovered(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "recovered")

    @property
    def still_title_only(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "still_title_only")

    @property
    def failed(self) -> int:
        return sum(1 for o in self.outcomes if o.status == "failed")


def _iter_events(base_dir: Path) -> list[dict[str, Any]]:
    """Every parseable JSON object across every rotated observability file
    in `base_dir` -- the dry-run cost estimate looks at the whole log, not
    just today's file, so a fresh install with no history today still finds
    whatever the daemon logged on prior days."""
    events: list[dict[str, Any]] = []
    if not base_dir.exists():
        return events
    for log_file in sorted(base_dir.glob("*_events.jsonl")):
        for line in log_file.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                events.append(entry)
    return events


def upper_bound_cost_estimate(base_dir: Path) -> float:
    """Upper-bound per-item LLM cost (SC-3): the summed 99th-percentile real cost
    of a summarize, evaluate and classify_kind call across every observability
    file in `base_dir`. A median is not a bound: items a refetch recovers are the
    long ones (a full transcript), and the 2026-09-30 youtube backfill cost $0.0077
    per item against a $0.0030 median. An action with no successful, priced call
    on record contributes 0, so an unpriced service reports a $0 floor.
    """
    costs: dict[str, list[float]] = {action: [] for action in _COST_ACTIONS}
    for entry in _iter_events(base_dir):
        if (
            entry.get("event") == "llm.call"
            and entry.get("status") == "success"
            and entry.get("action") in costs
            and isinstance(entry.get("cost_usd"), (int, float))
        ):
            costs[entry["action"]].append(float(entry["cost_usd"]))

    return sum(_percentile_99(values) for values in costs.values() if values)


def _percentile_99(values: list[float]) -> float:
    ordered = sorted(values)
    return ordered[math.ceil(0.99 * len(ordered)) - 1]


def _real_cost_since(base_dir: Path, run_id: str) -> float:
    """Sum every successful llm.call cost_usd event stamped with this
    refetch run's id (SC-3's "summed real cost of the calls it made").

    Only today's file can carry this run's events -- `set_run_id` stamps them
    as they're written during this same process's run, and
    `ObservabilityLogger.log` always writes to today's date-named file.
    """
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = base_dir / f"{today}_events.jsonl"
    if not log_file.exists():
        return 0.0

    total = 0.0
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            entry.get("run_id") == run_id
            and entry.get("event") == "llm.call"
            and entry.get("status") == "success"
            and isinstance(entry.get("cost_usd"), (int, float))
        ):
            total += float(entry["cost_usd"])
    return total


def _reextract_item(
    orchestrator: DaemonOrchestrator, row: dict[str, Any]
) -> ContentItem:
    """Re-run `row`'s source type's own single-item extraction path and
    return an updated ContentItem carrying whatever content it found.

    Never raises for YouTube or RSS -- their extractors return None on
    failure, which becomes empty content here and is caught by the
    `is_readable` check downstream, same as today's fetch loop. Reddit's
    `refetch_one` can raise (the submission itself is unreachable); that
    propagates to the caller, which records it as a failed refetch rather
    than a still-unreadable one.
    """
    source_type = row["source_type"]

    if source_type == "youtube":
        result = orchestrator.youtube_fetcher.refetch_transcript(row["url"])
        content = result.text or ""
        fetch_outcome: dict[str, str] | None = result.as_fetch_outcome()
    elif source_type == "rss":
        result = extract_article(
            row["url"],
            allowed_private_hosts=orchestrator.config.fetch_allow_private_hosts,
        )
        content = result.text or ""
        fetch_outcome = result.as_fetch_outcome()
    elif source_type == "reddit":
        return orchestrator.reddit_fetcher.refetch_one(
            row["external_id"], row["source_id"]
        )
    else:
        raise ValueError(f"refetch does not support source type {source_type!r}")

    return ContentItem(
        source_id=row["source_id"],
        external_id=row["external_id"],
        title=row["title"],
        url=row["url"],
        content=content,
        analysis={
            **(row.get("analysis") or {}),
            **({"fetch_outcome": fetch_outcome} if fetch_outcome else {}),
        },
    )


def _refetch_one(
    orchestrator: DaemonOrchestrator,
    row: dict[str, Any],
    learned_preferences: str | None,
) -> RefetchOutcome:
    """Re-extract, and where it recovers re-analyse, one selected row.

    Wrapped in one try/except (mirrors fetch_source_content's own per-item
    handling): any failure -- extraction, summarization, storage -- is
    recorded as this item's outcome rather than aborting the whole batch.
    """
    external_id = row["external_id"]
    title = row["title"]
    try:
        item = _reextract_item(orchestrator, row)

        if is_readable(item.content):
            source = {"type": row["source_type"], "name": row.get("source_name") or ""}
            result = orchestrator.analyze_and_store_item(
                item, source, learned_preferences
            )
            if result is None:
                return RefetchOutcome(
                    external_id, title, "failed", "summarization returned nothing"
                )
            return RefetchOutcome(external_id, title, "recovered")

        analysis = dict(row.get("analysis") or {})
        fetch_outcome = (item.analysis or {}).get("fetch_outcome")
        reason = title_only_reason(item.content, fetch_outcome, None)
        analysis["title_only"] = reason is not None
        analysis["title_only_reason"] = reason
        if fetch_outcome:
            analysis["fetch_outcome"] = fetch_outcome
        orchestrator.storage.update_analysis(row["id"], analysis)
        return RefetchOutcome(external_id, title, "still_title_only")

    except Exception as e:
        # Per-item boundary: one item's failure is its own "failed" outcome.
        logger.warning(f"Refetch failed for '{title}': {e}", exc_info=True)
        return RefetchOutcome(external_id, title, "failed", str(e))


def run_refetch(
    orchestrator: DaemonOrchestrator,
    source_type: str,
    limit: int,
    dry_run: bool = False,
) -> RefetchReport:
    """Select, then (unless `dry_run`) re-extract and re-analyse, the
    not-readable stored items of `source_type` (SC-2, SC-3).

    Args:
        orchestrator: Already-wired DaemonOrchestrator -- its storage,
            youtube_fetcher and reddit_fetcher are the ones this call uses.
        source_type: 'youtube', 'rss' or 'reddit'
        limit: Maximum number of items to select
        dry_run: When True, only counts the selection and estimates cost --
            no extraction, LLM call or write happens.

    Returns:
        A RefetchReport describing what was (or would be) done.

    Raises:
        ValueError: source_type is not one of REFETCH_TYPES
    """
    if source_type not in REFETCH_TYPES:
        raise ValueError(
            f"refetch does not support source type {source_type!r}, "
            f"expected one of {REFETCH_TYPES}"
        )

    rows = orchestrator.storage.get_unreadable_content(source_type, limit)

    if dry_run:
        per_item_cost = upper_bound_cost_estimate(get_obs_logger().base_dir)
        return RefetchReport(
            source_type=source_type,
            dry_run=True,
            selected=len(rows),
            estimated_cost=per_item_cost * len(rows),
        )

    # Sourced once per run, exactly like run_once (orchestrator.py's own
    # cycle), and threaded into every item's analyze_and_store_item call --
    # the same path fetch_source_content takes it through. Not critical:
    # a lookup failure falls back to None rather than failing the run.
    learned_preferences = None
    try:
        learned_preferences, total_votes = get_learned_preferences(orchestrator.storage)
        if learned_preferences:
            obs_log(
                "refetch.learned_preferences",
                source_type=source_type,
                total_votes=total_votes,
            )
    except Exception as e:
        logger.warning(f"Failed to fetch feedback statistics: {e}", exc_info=True)

    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    try:
        outcomes = []
        for row in rows:
            outcome = _refetch_one(orchestrator, row, learned_preferences)
            outcomes.append(outcome)
            obs_log(
                "refetch.item",
                source_type=source_type,
                external_id=outcome.external_id,
                status=outcome.status,
                detail=outcome.detail,
            )
        real_cost = _real_cost_since(get_obs_logger().base_dir, run_id)
    finally:
        set_run_id(None)

    return RefetchReport(
        source_type=source_type,
        dry_run=False,
        selected=len(rows),
        outcomes=outcomes,
        real_cost=real_cost,
    )
