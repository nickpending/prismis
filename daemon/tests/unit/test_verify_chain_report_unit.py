"""Unit tests for the per-link report renderer.

Invariants protected:
  - ran-and-produced-nothing, skipped-by-flag and
    never-reached-because-an-earlier-link-failed render as three different statuses,
    never one blank cell
  - The circuit-open string the renderer counts refusals with is the one real code
    raises, captured from a real forced-open circuit rather than written from memory
  - The chain module reimplements no link: it imports none of the libraries a link
    would need and never talks to an LLM itself (SC-1's mechanical check)

SC-3's fourth state, skipped-because-the-circuit-was-open, is proven in
tests/integration/test_verify_chain_circuit_open_integration.py against a real
orchestrator run. It is not proven here because the input it needs cannot be
assembled honestly by hand: an open circuit always leaves the failures that opened
it behind in the same run, so an events list without them is one no run produces.

Success criteria covered:
  SC-1, SC-3, SC-6

Real collaborators: a real CircuitBreaker driven to open by real failure records, and a
real ContentSummarizer call that raises against it. render_report itself is pure, so it
is exercised directly.
"""

import ast
from pathlib import Path

import pytest

from prismis_daemon import verify_chain
from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.summarizer import ContentSummarizer
from prismis_daemon.verify_chain import CIRCUIT_OPEN_MARKER, LinkStatus, render_report

_SOURCE_ID = "source-under-test"
_SERVICE = "verify-chain-test-service"

# Every absolute import the chain is allowed to hold. An allowlist rather than a list
# of banned libraries, because a denylist only catches the reimplementations somebody
# thought to name: a link rebuilt on urllib, requests, socket or hand-rolled parsing
# would pass a denylist untouched, which puts the judgment back in "is the list
# complete" — the exact judgment call SC-1 says it must not rest on.
#
# The walk below skips relative imports, so every real collaborator the chain drives
# (the orchestrator, storage, the fetchers, the summarizer, evaluator, embedder,
# notifier, config, database, observability, deep extractor) sits outside this set by
# construction and needs no entry here.
_ALLOWED_IMPORTS = {
    "dataclasses",
    "datetime",
    "json",
    "pathlib",
    "rich",
    "typing",
    "uuid",
}


def _stats(**overrides) -> dict:
    stats = {
        "items_fetched": 0,
        "items_processed": 0,
        "items_new": 0,
        "items_updated": 0,
        "errors": [],
        "new_high_priority_items": [],
    }
    stats.update(overrides)
    return stats


def _by_name(links: list[LinkStatus]) -> dict[str, LinkStatus]:
    return {link.name: link for link in links}


def _fetch_complete(count: int) -> dict:
    return {
        "event": "fetcher.complete",
        "source_id": _SOURCE_ID,
        "items_count": count,
        "duration_ms": 12,
        "status": "success",
    }


def _llm_success(action: str, cost: float = 0.01) -> dict:
    return {
        "event": "llm.call",
        "action": action,
        "model": "gpt-test",
        "cost_usd": cost,
        "duration_ms": 100,
        "status": "success",
    }


# --- The circuit-open string, captured from real code --------------------------------


@pytest.fixture
def real_circuit_open_error() -> str:
    """The message a real summarizer raises when its circuit is open.

    Driven through the real CircuitBreaker: three real quota failures open it, and the
    real summarizer then raises before it ever reaches an LLM client or its own
    observability call. Nothing is mocked and nothing goes over the network — which is
    the whole reason this state leaves no event behind.
    """
    reset_circuit_breaker(_SERVICE)
    circuit = get_circuit_breaker(_SERVICE)
    for _ in range(3):
        circuit.record_failure(RuntimeError("insufficient_quota"))
    assert circuit.check_can_proceed() is False, "the circuit must actually be open"

    summarizer = ContentSummarizer(_SERVICE)
    with pytest.raises(RuntimeError) as exc_info:
        summarizer.summarize_with_analysis(content="some real content", title="A title")

    reset_circuit_breaker(_SERVICE)
    return str(exc_info.value)


def test_renderer_matches_the_string_real_code_raises(real_circuit_open_error) -> None:
    """
    SC-3: the substring the renderer keys on is the one production actually raises.
    BREAKS: the renderer silently reports "empty" for every quota-exhausted run,
    which is the state SC-3 exists to make visible.
    """
    assert CIRCUIT_OPEN_MARKER in real_circuit_open_error


# --- Per-link states ------------------------------------------------------------------


def test_fetch_error_leaves_every_later_link_never_reached() -> None:
    """
    SC-3: a link that never ran because an earlier one failed says so.
    BREAKS: a fetch failure reads as "summarize produced nothing", hiding the cause.
    """
    events = [
        {
            "event": "fetcher.error",
            "source_id": _SOURCE_ID,
            "error": "Request URL is missing an 'http://' or 'https://' protocol.",
            "duration_ms": 3,
            "status": "error",
        }
    ]
    stats = _stats(errors=["Failed to fetch from not-a-real-url: boom"])

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["fetch"].status == "error"
    assert "protocol" in links["fetch"].detail
    for name in ("dedup", "summarize", "evaluate", "store", "embed"):
        assert links[name].status == "never-reached", name


def test_fetch_that_returned_nothing_is_empty_not_error() -> None:
    """
    SC-3: a fetch that succeeded and produced zero items is its own answer.
    BREAKS: an empty feed is reported as a failure, or as a success that ran.
    """
    links = _by_name(
        render_report(_stats(), [_fetch_complete(0)], _SOURCE_ID, full=False)
    )

    assert links["fetch"].status == "empty"
    assert links["fetch"].duration_ms == 12


def test_dedup_is_inferred_from_the_returned_stats() -> None:
    """
    D-REPORT's one remaining inference: the dedup query emits no event, so the filtered
    count is derived from the difference the orchestrator itself returned.

    BREAKS: the report claims a dedup number that does not match what the run did.
    """
    stats = _stats(items_fetched=10, items_processed=3)

    links = _by_name(
        render_report(stats, [_fetch_complete(10)], _SOURCE_ID, full=False)
    )

    assert links["dedup"].status == "ran"
    assert "7 already-seen filtered, 3 new" in links["dedup"].detail
    assert links["dedup"].duration_ms is None


def test_llm_links_report_cost_and_model_from_their_own_events() -> None:
    """
    SC-3: the LLM links carry the cost and the model that actually answered.
    BREAKS: the operator cannot tell which model ran or what the run cost.
    """
    events = [
        _fetch_complete(2),
        _llm_success("summarize", cost=0.02),
        _llm_success("summarize", cost=0.03),
        _llm_success("evaluate", cost=0.01),
    ]
    stats = _stats(items_fetched=2, items_processed=2)

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["summarize"].status == "ran"
    assert links["summarize"].cost_usd == pytest.approx(0.05)
    assert links["summarize"].model == "gpt-test"
    assert links["summarize"].duration_ms == 200
    assert links["evaluate"].cost_usd == pytest.approx(0.01)


def test_llm_link_with_processed_items_but_no_call_is_empty() -> None:
    """
    SC-3: items were processed and this link still made no call — for instance every
    item had blank content, which the summarizer returns None for before calling out.

    BREAKS: "ran and produced nothing" collapses into "never reached".
    """
    stats = _stats(items_fetched=1, items_processed=1)

    links = _by_name(
        render_report(stats, [_fetch_complete(1)], _SOURCE_ID, full=False)
    )

    assert links["summarize"].status == "empty"


def test_llm_call_error_event_makes_the_link_an_error() -> None:
    """
    SC-3: a failed LLM call is an error, not an empty result.
    BREAKS: a failing model reads as a model that had nothing to say.
    """
    events = [
        _fetch_complete(1),
        {
            "event": "llm.call",
            "action": "summarize",
            "model": "gpt-test",
            "status": "error",
            "error": "upstream refused",
        },
    ]
    stats = _stats(items_fetched=1, items_processed=1)

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["summarize"].status == "error"
    assert "upstream refused" in links["summarize"].detail


def test_store_reports_created_and_updated_counts() -> None:
    """
    SC-3: the store link is reported from its own events, with a real duration.
    BREAKS: the one default link reported by inference while every other is evented.
    """
    events = [
        _fetch_complete(2),
        {
            "event": "db.insert",
            "operation": "create_or_update_content",
            "status": "created",
            "duration_ms": 4,
        },
        {
            "event": "db.insert",
            "operation": "create_or_update_content",
            "status": "updated",
            "duration_ms": 6,
        },
    ]
    stats = _stats(items_fetched=2, items_processed=2)

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["store"].status == "ran"
    assert links["store"].detail == "1 created, 1 updated"
    assert links["store"].duration_ms == 10


def test_store_error_event_makes_the_link_an_error() -> None:
    """
    SC-3: a write that the database refused is its own state.
    BREAKS: a failed write is indistinguishable from a write that never happened.
    """
    events = [
        _fetch_complete(1),
        {
            "event": "db.insert",
            "operation": "create_or_update_content",
            "status": "error",
            "error": "FOREIGN KEY constraint failed",
            "duration_ms": 1,
        },
    ]
    stats = _stats(items_fetched=1, items_processed=1)

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["store"].status == "error"
    assert "FOREIGN KEY" in links["store"].detail


def test_embed_link_reports_its_own_events() -> None:
    """
    SC-5 rendered: the embed link now has events to report from.
    BREAKS: the embed link stays a blank cell.
    """
    events = [
        _fetch_complete(1),
        {
            "event": "embedding.generate",
            "model": "all-MiniLM-L6-v2",
            "dimension": 384,
            "duration_ms": 9,
            "status": "success",
        },
    ]
    stats = _stats(items_fetched=1, items_processed=1)

    links = _by_name(render_report(stats, events, _SOURCE_ID, full=False))

    assert links["embed"].status == "ran"
    assert links["embed"].model == "all-MiniLM-L6-v2"
    assert links["embed"].duration_ms == 9


# --- --full gating (SC-6) -------------------------------------------------------------


def test_default_run_reports_deep_and_notify_as_skipped_by_flag() -> None:
    """
    SC-6: without --full those two links are skipped-by-flag, not absent and not empty.
    BREAKS: the operator cannot tell "I did not ask for this" from "this produced
    nothing", which is the distinction SC-6 names.
    """
    links = _by_name(
        render_report(_stats(), [_fetch_complete(0)], _SOURCE_ID, full=False)
    )

    assert links["deep_extract"].status == "skipped-by-flag"
    assert links["notify"].status == "skipped-by-flag"
    assert [link.name for link in render_report(_stats(), [], _SOURCE_ID, False)] == [
        "fetch",
        "dedup",
        "summarize",
        "evaluate",
        "deep_extract",
        "store",
        "embed",
        "notify",
    ]


def test_full_without_a_deep_service_is_skipped_not_skipped_by_flag() -> None:
    """
    SC-6: --full asked for deep extraction and the install has no deep service — a
    different answer from "you did not ask for it".

    BREAKS: an unconfigured deep service looks like a deliberate opt-out.
    """
    links = _by_name(
        render_report(
            _stats(items_fetched=1, items_processed=1),
            [_fetch_complete(1)],
            _SOURCE_ID,
            full=True,
            deep_service=None,
        )
    )

    assert links["deep_extract"].status == "skipped"
    assert links["deep_extract"].detail == "deep service not configured"


def test_full_with_a_deep_service_reports_the_deep_calls() -> None:
    """
    SC-6: --full includes deep extraction, and the report says the flag overrode the
    operator's own auto_extract threshold.

    BREAKS: the report reads as proof the operator's real config triggers deep
    extraction, which --full does not establish.
    """
    events = [_fetch_complete(1), _llm_success("deep_extract", cost=0.4)]
    stats = _stats(items_fetched=1, items_processed=1)

    links = _by_name(
        render_report(stats, events, _SOURCE_ID, full=True, deep_service="deep-svc")
    )

    assert links["deep_extract"].status == "ran"
    assert links["deep_extract"].cost_usd == pytest.approx(0.4)
    assert "auto_extract=all" in links["deep_extract"].detail


def test_full_notify_with_no_high_priority_items_is_empty() -> None:
    """
    SC-6 / Principle II: production only calls the notifier when there is something to
    send, so the chain reports that as empty rather than as a notification that failed.

    BREAKS: a run with nothing HIGH looks like a broken notifier.
    """
    stats = _stats(items_fetched=1, items_processed=1, new_high_priority_items=[])

    links = _by_name(
        render_report(
            stats, [_fetch_complete(1)], _SOURCE_ID, full=True, deep_service=None
        )
    )

    assert links["notify"].status == "empty"
    assert "no HIGH priority items" in links["notify"].detail


def test_full_notify_reports_the_notification_event() -> None:
    """
    SC-6: with something to send, the notify link reports the real event.
    BREAKS: the notify link is blank on the runs where it actually did something.
    """
    events = [
        _fetch_complete(1),
        {
            "event": "notification.send",
            "status": "success",
            "count": 2,
            "duration_ms": 15,
        },
    ]
    stats = _stats(
        items_fetched=1,
        items_processed=1,
        new_high_priority_items=[{"title": "a"}, {"title": "b"}],
    )

    links = _by_name(
        render_report(stats, events, _SOURCE_ID, full=True, deep_service=None)
    )

    assert links["notify"].status == "ran"
    assert links["notify"].detail == "2 item(s) notified"


# --- Attribution guard ----------------------------------------------------------------


def test_fetch_events_from_another_source_are_ignored() -> None:
    """
    SC-8b, defence in depth: fetcher events carry a source id, so a fetch event for a
    different source is not this run's fetch even if it somehow shares the run id.

    The guard is reachable — the sibling tests above pass the matching source id and
    get a populated fetch row from the very same event shape.

    BREAKS: the wrong source's result is reported as this run's.
    """
    foreign = dict(_fetch_complete(5), source_id="a-different-source")

    links = _by_name(render_report(_stats(), [foreign], _SOURCE_ID, full=False))

    assert links["fetch"].status == "never-reached"
    assert links["fetch"].detail == "no fetcher event recorded"


# --- SC-1: the chain reimplements no link ---------------------------------------------


def test_chain_module_imports_nothing_outside_the_allowlist() -> None:
    """
    SC-1 (mechanical, not reviewer judgment): every absolute import the chain holds is
    one of a fixed set that cannot fetch, call a model, or reach a store.

    Stated as an allowlist so the check does not depend on anyone having predicted which
    library a reimplementation would reach for — a link rebuilt on urllib or a raw
    socket trips this, where a list of named offenders would wave it through.

    Parsed rather than grepped so a mention in prose cannot pass or fail this, and so a
    `from x import y` form is caught as surely as `import x`.

    BREAKS: the chain grows its own fetch/summarize/store logic and the whole
    instrument starts testing itself instead of the product.
    """
    source_path = Path(verify_chain.__file__)
    tree = ast.parse(source_path.read_text())

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert imported, "the parse must have found imports, or this proves nothing"
    assert imported.issubset(_ALLOWED_IMPORTS), (
        f"chain imports outside the allowlist: {sorted(imported - _ALLOWED_IMPORTS)}"
    )


def test_chain_drives_the_orchestrator_through_fetch_source_content() -> None:
    """
    SC-1: the positive half — the whole pipeline is behind one orchestrator call.
    BREAKS: the chain calls fetchers or the summarizer itself, link by link, and stops
    exercising the composition the daemon actually runs.
    """
    source = Path(verify_chain.__file__).read_text()

    assert "orchestrator.fetch_source_content(source)" in source
    assert "DaemonOrchestrator(" in source
