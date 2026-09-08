"""End-to-end pipeline chain for `prismis-daemon verify --chain`.

Drives the production orchestrator over one named source against a throwaway
temp-XDG database and reports what each link did, mined from the observability
JSONL the run itself writes.

This module reimplements no link. Fetch, summarize, evaluate, deep-extract,
store and embed all happen inside `DaemonOrchestrator.fetch_source_content`,
which is called once with a synthetic source dict.
"""

import dataclasses
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

from .config import Config
from .database import init_db
from .deep_extractor import ContentDeepExtractor
from .embeddings import Embedder
from .evaluator import ContentEvaluator
from .fetchers.file import FileFetcher
from .fetchers.reddit import RedditFetcher
from .fetchers.rss import RSSFetcher
from .fetchers.youtube import YouTubeFetcher
from .notifier import Notifier
from .observability import get_logger, set_run_id
from .orchestrator import DaemonOrchestrator
from .storage import Storage
from .summarizer import ContentSummarizer

VALID_SOURCE_TYPES = ("rss", "reddit", "youtube", "file")

# The substring both the summarizer and the evaluator raise when the shared circuit is
# open. That raise happens before either method reaches its own observability call, so
# a refused item leaves no llm.call event; the orchestrator's per-item error entry is
# the only record that the item was turned away, which is what this counts. Whether the
# circuit opened at all is answered by an event, not by this string — see
# the helper that counts circuit-open refusals below.
CIRCUIT_OPEN_MARKER = "circuit breaker is open"

# The two error prefixes the orchestrator itself writes into stats["errors"].
FETCH_FAILURE_MARKER = "Failed to fetch from"
ITEM_FAILURE_MARKER = "Failed to analyze item"

# Statuses that make the run a failure for the caller's exit code.
FAILING_STATUSES = ("error", "circuit-open")


@dataclass
class LinkStatus:
    """One pipeline link's outcome in the rendered report.

    name: "fetch" | "dedup" | "summarize" | "evaluate" | "deep_extract"
          | "store" | "embed" | "notify"
    status: "ran" | "empty" | "never-reached" | "skipped-by-flag"
            | "skipped" | "circuit-open" | "error"
    """

    name: str
    status: str
    duration_ms: int | None
    detail: str
    cost_usd: float | None = None
    model: str | None = None


def build_source(storage: Storage, url: str, source_type: str) -> dict[str, Any]:
    """Insert one real row into the temp database's sources table and return the
    source dict `fetch_source_content` expects.

    The row is required by the content table's FOREIGN KEY on source_id; it is setup,
    not a read of the operator's source list.

    Raises:
        ValueError: If source_type is not one of VALID_SOURCE_TYPES.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"Invalid source type: {source_type}")

    name = f"verify-chain {source_type}"
    source_id = storage.add_source(url=url, source_type=source_type, name=name)
    return {"id": source_id, "url": url, "name": name, "type": source_type}


def setup_isolated_run(url: str, source_type: str) -> tuple[Storage, dict[str, Any]]:
    """Initialize the schema, then construct Storage, then insert the source row.

    The ordering is load-bearing: Storage's constructor opens a test connection
    immediately, so against a fresh XDG_DATA_HOME with no database file it raises
    unless the schema has already been created.
    """
    if source_type not in VALID_SOURCE_TYPES:
        raise ValueError(f"Invalid source type: {source_type}")

    init_db()
    storage = Storage()
    source = build_source(storage, url, source_type)
    return storage, source


def read_run_events(base_dir: Path, run_id: str) -> list[dict[str, Any]]:
    """Return every event in today's JSONL file carrying this run's id.

    Events written without a run id — every event the long-running daemon emits, since
    nothing sets one in its process — carry no run_id key and are therefore excluded by
    construction, not by a timestamp window.
    """
    log_file = base_dir / f"{datetime.now().strftime('%Y-%m-%d')}_events.jsonl"
    if not log_file.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and entry.get("run_id") == run_id:
            events.append(entry)
    return events


def _errors_matching(stats: dict[str, Any], marker: str) -> list[str]:
    return [e for e in stats.get("errors", []) if marker in e]


def _circuit_opened_during_run(events: list[dict[str, Any]]) -> bool:
    """Whether the circuit crossed its threshold inside this run, rather than arriving
    already open.

    The breaker logs the transition itself at the moment it decides, and the event is
    stamped with this run's id like every other. Both cases are real and the operator
    acts on them differently — one says this run burned the quota, the other says it
    was already gone — so this decides wording, never whether the circuit was open.
    Classification is the refusal count below: an item that was actually turned away is
    the fact that matters, and it is equally true either way.
    """
    return any(
        e.get("event") == "circuit_breaker.state" and e.get("state") == "open"
        for e in events
    )


def _circuit_open_refusals(stats: dict[str, Any]) -> int:
    """How many items an open circuit turned away without attempting them.

    The orchestrator's per-item error entry is the whole signal, and it is unambiguous
    on its own. It cannot come from the deep service's separate breaker, whose
    CircuitOpenError the orchestrator swallows under INV-002 and never records in the
    returned stats, and it cannot come from another process, because the stats are this
    run's own return value.

    Deliberately NOT also requiring this run to have logged the open transition. A
    circuit that was already open when the run started refuses every item and logs no
    transition, and reporting that as a plain error is precisely the collapse the
    circuit-open state exists to prevent.

    Zero means either the circuit was never open, or it opened on the run's last
    failure and no item was ever refused because of it.
    """
    return len(_errors_matching(stats, CIRCUIT_OPEN_MARKER))


def _llm_events(events: list[dict[str, Any]], action: str) -> list[dict[str, Any]]:
    return [
        e for e in events if e.get("event") == "llm.call" and e.get("action") == action
    ]


def _sum_durations(events: list[dict[str, Any]]) -> int | None:
    values = [e["duration_ms"] for e in events if isinstance(e.get("duration_ms"), int)]
    return sum(values) if values else None


def _sum_costs(events: list[dict[str, Any]]) -> float | None:
    values = [
        e["cost_usd"] for e in events if isinstance(e.get("cost_usd"), (int, float))
    ]
    return sum(values) if values else None


def _models_seen(events: list[dict[str, Any]]) -> str | None:
    names = sorted({str(e["model"]) for e in events if e.get("model")})
    return ", ".join(names) if names else None


def _llm_link(
    name: str,
    events: list[dict[str, Any]],
    stats: dict[str, Any],
    action: str,
    circuit_open_status: str,
    circuit_open_detail: str,
    circuit_preempts: bool = False,
) -> LinkStatus:
    """Classify one LLM-backed link from its own llm.call events plus the stats.

    circuit_preempts is set only for the link that raises when the circuit is open.
    That link's own error events are the failures that opened the circuit, so it always
    has some, and reporting them as a plain error would bury the fact that every
    remaining item was then refused unattempted. A downstream link is different: if it
    logged calls of its own they happened before the circuit opened and are worth
    reporting on their own terms, so the circuit only explains a link that logged
    nothing at all.
    """
    if stats.get("items_processed", 0) == 0:
        return LinkStatus(name, "never-reached", None, "no items reached this link")

    matching = _llm_events(events, action)
    refused = _circuit_open_refusals(stats)
    failures = [e for e in matching if e.get("status") == "error"]
    successes = [e for e in matching if e.get("status") == "success"]

    if refused and (circuit_preempts or not matching):
        when = (
            "opened during this run"
            if _circuit_opened_during_run(events)
            else "was already open when the run started"
        )
        detail = f"{circuit_open_detail} {when}; {refused} item(s) refused unattempted"
        if failures:
            detail += f"; {len(failures)} call(s) failed before it opened"
        return LinkStatus(
            name,
            circuit_open_status,
            _sum_durations(matching),
            detail,
            _sum_costs(successes),
            _models_seen(matching),
        )

    if not matching:
        item_errors = _errors_matching(stats, ITEM_FAILURE_MARKER)
        if item_errors:
            return LinkStatus(name, "error", None, item_errors[0])
        return LinkStatus(
            name, "empty", None, "items processed but this link produced no call"
        )

    if failures:
        detail = (
            f"{len(failures)} of {len(matching)} calls failed: "
            f"{failures[0].get('error', 'no error text')}"
        )
        if _circuit_opened_during_run(events):
            detail += " (the circuit opened on the last failure; no item was refused)"
        return LinkStatus(
            name,
            "error",
            _sum_durations(matching),
            detail,
            _sum_costs(successes),
            _models_seen(matching),
        )
    return LinkStatus(
        name,
        "ran",
        _sum_durations(matching),
        f"{len(matching)} call(s)",
        _sum_costs(matching),
        _models_seen(matching),
    )


def _fetch_link(
    events: list[dict[str, Any]], stats: dict[str, Any], source_id: str
) -> LinkStatus:
    fetch_events = [
        e
        for e in events
        if e.get("event") in ("fetcher.complete", "fetcher.error")
        and e.get("source_id") == source_id
    ]

    errors = [e for e in fetch_events if e.get("event") == "fetcher.error"]
    if errors:
        return LinkStatus(
            "fetch",
            "error",
            errors[0].get("duration_ms"),
            str(errors[0].get("error", "fetch failed")),
        )

    completes = [e for e in fetch_events if e.get("event") == "fetcher.complete"]
    if completes:
        count = int(completes[0].get("items_count", 0) or 0)
        return LinkStatus(
            "fetch",
            "ran" if count > 0 else "empty",
            completes[0].get("duration_ms"),
            f"{count} item(s) fetched",
        )

    fetch_errors = _errors_matching(stats, FETCH_FAILURE_MARKER)
    if fetch_errors:
        return LinkStatus("fetch", "error", None, fetch_errors[0])
    return LinkStatus("fetch", "never-reached", None, "no fetcher event recorded")


def _dedup_link(stats: dict[str, Any]) -> LinkStatus:
    fetched = stats.get("items_fetched", 0)
    if fetched == 0:
        return LinkStatus(
            "dedup", "never-reached", None, "fetch returned no items to filter"
        )
    processed = stats.get("items_processed", 0)
    filtered = fetched - processed
    return LinkStatus(
        "dedup",
        "ran",
        None,
        f"{filtered} already-seen filtered, {processed} new "
        "(inferred from returned stats; the dedup query emits no event)",
    )


def _store_link(events: list[dict[str, Any]], stats: dict[str, Any]) -> LinkStatus:
    if stats.get("items_processed", 0) == 0:
        return LinkStatus("store", "never-reached", None, "no items reached this link")

    matching = [
        e
        for e in events
        if e.get("event") == "db.insert"
        and e.get("operation") == "create_or_update_content"
    ]
    failures = [e for e in matching if e.get("status") == "error"]
    if failures:
        return LinkStatus(
            "store",
            "error",
            _sum_durations(matching),
            str(failures[0].get("error", "store failed")),
        )

    created = [e for e in matching if e.get("status") == "created"]
    updated = [e for e in matching if e.get("status") == "updated"]
    if created or updated:
        return LinkStatus(
            "store",
            "ran",
            _sum_durations(matching),
            f"{len(created)} created, {len(updated)} updated",
        )

    item_errors = _errors_matching(stats, ITEM_FAILURE_MARKER)
    if _circuit_open_refusals(stats):
        return LinkStatus(
            "store",
            "never-reached",
            None,
            "every item failed before reaching storage (open circuit)",
        )
    if item_errors:
        return LinkStatus("store", "error", None, item_errors[0])
    return LinkStatus(
        "store", "empty", None, "items processed but nothing reached storage"
    )


def _embed_link(events: list[dict[str, Any]], stats: dict[str, Any]) -> LinkStatus:
    if stats.get("items_processed", 0) == 0:
        return LinkStatus("embed", "never-reached", None, "no items reached this link")

    matching = [e for e in events if e.get("event") == "embedding.generate"]
    failures = [e for e in matching if e.get("status") == "error"]
    if failures:
        return LinkStatus(
            "embed",
            "error",
            _sum_durations(matching),
            str(failures[0].get("error", "embedding failed")),
            None,
            _models_seen(matching),
        )
    if matching:
        return LinkStatus(
            "embed",
            "ran",
            _sum_durations(matching),
            f"{len(matching)} embedding(s)",
            None,
            _models_seen(matching),
        )
    if _circuit_open_refusals(stats):
        return LinkStatus(
            "embed",
            "never-reached",
            None,
            "every item failed before reaching embedding (open circuit)",
        )
    return LinkStatus(
        "embed", "empty", None, "items processed but no embedding was generated"
    )


def _notify_link(
    events: list[dict[str, Any]], stats: dict[str, Any], full: bool
) -> LinkStatus:
    if not full:
        return LinkStatus("notify", "skipped-by-flag", None, "run with --full to send")

    if not stats.get("new_high_priority_items"):
        return LinkStatus(
            "notify",
            "empty",
            None,
            "no HIGH priority items — production gates the call the same way",
        )

    matching = [e for e in events if e.get("event") == "notification.send"]
    failures = [e for e in matching if e.get("status") == "error"]
    if failures:
        return LinkStatus(
            "notify",
            "error",
            _sum_durations(matching),
            str(failures[0].get("error") or "notification command failed"),
        )
    skipped = [e for e in matching if e.get("status") == "skipped"]
    if skipped:
        return LinkStatus(
            "notify", "skipped", None, str(skipped[0].get("reason", "skipped"))
        )
    if matching:
        return LinkStatus(
            "notify",
            "ran",
            _sum_durations(matching),
            f"{matching[0].get('count', 0)} item(s) notified",
        )
    return LinkStatus("notify", "empty", None, "notifier produced no event")


def render_report(
    stats: dict[str, Any],
    events: list[dict[str, Any]],
    source_id: str,
    full: bool,
    deep_service: str | None = None,
) -> list[LinkStatus]:
    """Turn the returned stats plus this run's events into one LinkStatus per link.

    Pure aggregation — no I/O and no collaborators. Link 8 (serve) is out of scope and
    is not reported.
    """
    if not full:
        deep = LinkStatus(
            "deep_extract", "skipped-by-flag", None, "run with --full to extract"
        )
    elif deep_service is None:
        deep = LinkStatus(
            "deep_extract", "skipped", None, "deep service not configured"
        )
    else:
        deep = _llm_link(
            "deep_extract",
            events,
            stats,
            "deep_extract",
            "never-reached",
            "deep extraction never attempted, the light circuit",
        )
        deep.detail += " (--full forces auto_extract=all, overriding your config)"

    return [
        _fetch_link(events, stats, source_id),
        _dedup_link(stats),
        _llm_link(
            "summarize",
            events,
            stats,
            "summarize",
            "circuit-open",
            "the shared LLM circuit",
            circuit_preempts=True,
        ),
        _llm_link(
            "evaluate",
            events,
            stats,
            "evaluate",
            "never-reached",
            "summarize hit the open circuit first; evaluate never attempted, the circuit",
        ),
        deep,
        _store_link(events, stats),
        _embed_link(events, stats),
        _notify_link(events, stats, full),
    ]


_STATUS_STYLES = {
    "ran": "green",
    "empty": "cyan",
    "never-reached": "dim",
    "skipped-by-flag": "blue",
    "skipped": "yellow",
    "circuit-open": "magenta",
    "error": "red",
}


def print_report(console: Console, links: list[LinkStatus]) -> None:
    """Render the per-link report as a Rich table."""
    table = Table(title="verify --chain", show_lines=False)
    table.add_column("link")
    table.add_column("status")
    table.add_column("duration", justify="right")
    table.add_column("cost", justify="right")
    table.add_column("model")
    table.add_column("detail")

    for link in links:
        style = _STATUS_STYLES.get(link.status, "white")
        table.add_row(
            link.name,
            f"[{style}]{link.status}[/{style}]",
            f"{link.duration_ms} ms" if link.duration_ms is not None else "—",
            f"${link.cost_usd:.6f}" if link.cost_usd is not None else "—",
            link.model or "—",
            link.detail,
        )

    console.print(table)


def build_orchestrator(
    config: Config, storage: Storage, console: Console
) -> DaemonOrchestrator:
    """Construct the production orchestrator with real collaborators.

    Mirrors the wiring the daemon's own --once path uses. Kept as one function so a
    test can drive the exact object the CLI drives instead of a second copy of this
    wiring that could drift away from it.
    """
    deep_extractor = None
    if config.llm_deep_service:
        deep_extractor = ContentDeepExtractor(config.llm_deep_service)

    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=RSSFetcher(config=config),
        reddit_fetcher=RedditFetcher(config=config),
        youtube_fetcher=YouTubeFetcher(config=config),
        file_fetcher=FileFetcher(config=config, storage=storage),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(
            {
                "high_priority_only": config.high_priority_only,
                "command": config.notification_command,
            }
        ),
        config=config,
        console=console,
        embedder=Embedder(),
        deep_extractor=deep_extractor,
    )


def run_chain(source_url: str, source_type: str, full: bool, console: Console) -> int:
    """Drive one source through the real pipeline and print the per-link report.

    Returns the process exit code: 1 if any in-scope link errored or hit an open
    circuit, otherwise 0.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    try:
        try:
            config = Config.from_file()
        except Exception as e:
            console.print(f"[red]✗ config: {e}[/red]")
            return 1

        if full:
            # The deep service itself stays real; only the priority threshold is
            # overridden, so --full's promise is deterministic instead of depending
            # on whatever auto_extract the operator has set.
            config = dataclasses.replace(config, auto_extract="all")

        storage, source = setup_isolated_run(source_url, source_type)
        orchestrator = build_orchestrator(config, storage, console)

        stats = orchestrator.fetch_source_content(source)

        if full and stats["new_high_priority_items"]:
            orchestrator.notifier.notify_new_content(stats["new_high_priority_items"])
    finally:
        set_run_id(None)

    events = read_run_events(get_logger().base_dir, run_id)
    links = render_report(
        stats, events, source["id"], full, deep_service=config.llm_deep_service
    )
    print_report(console, links)

    return 1 if any(link.status in FAILING_STATUSES for link in links) else 0
