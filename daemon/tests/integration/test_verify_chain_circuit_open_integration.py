"""Integration tests for SC-3's circuit-open state, against a real orchestrator run.

Why this is not a unit test on render_report. Reaching an open circuit inside one run
requires three quota failures, and each of those goes through the summarizer's own
except clause, which records the failure and then logs an llm.call error event before
re-raising. So an open circuit always leaves its own failure events behind in the same
run, and a hand-built events list that omits them is an input no run can produce. The
state has to be reached, not described.

What the corrected design keys on. Classification is the orchestrator's own per-item
error entries: an item that was actually turned away by an open circuit is the fact
that matters, and it holds whether the circuit opened mid-run or was already open. The
breaker separately logs `circuit_breaker.state` with state="open" when it crosses its
threshold, through the same observability path every other link uses, so that event
carries this run's id — it decides only how the report words the state, never whether
the state is reported. Both shapes are covered below, because a design that demanded
the event would report a run against an already-dead quota as a plain error.

What this test cannot reproduce, stated plainly: a provider returning a genuine quota
error. The gate has no exhausted account and may not mock the provider. So the three
failures that open the breaker are recorded through the real CircuitBreaker's real
`record_failure`, classified as quota errors by its own `is_quota_error`, rather than
arriving from the network. Everything downstream of that — the open event, the
summarizer's early raise, the orchestrator's error entries, the rendered report — is
produced by production code executing here.

Success criteria covered:
  SC-3 (the circuit-open state), SC-8b (the events are this run's)

Real collaborators throughout: a real RSS feed served over a real socket on 127.0.0.1,
the real RSSFetcher, the real orchestrator built by the chain's own builder, the real
CircuitBreaker, the real ContentSummarizer, a real temp-XDG SQLite database. No mock,
no third party, no credentials.
"""

import dataclasses
import http.server
import threading
import uuid
from collections.abc import Iterator
from email.utils import format_datetime
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from rich.console import Console

from prismis_daemon.circuit_breaker import (
    CircuitBreaker,
    get_circuit_breaker,
    reset_circuit_breaker,
)
from prismis_daemon.config import Config
from prismis_daemon.observability import (
    get_logger,
    reset_logger,
    set_run_id,
)
from prismis_daemon.summarizer import ContentSummarizer
from prismis_daemon.verify_chain import (
    build_orchestrator,
    read_run_events,
    render_report,
    setup_isolated_run,
)

# A service name that resolves to nothing, so every call against it fails locally at
# service resolution and never opens a socket or spends a credential. The breaker this
# run opens is keyed on this same name, so one service tells the whole story.
_ABSENT_SERVICE = "prismis-verify-chain-absent-service"

_ITEM_COUNT = 4


@pytest.fixture(autouse=True)
def _fresh_observability():
    reset_logger()
    yield
    set_run_id(None)
    reset_circuit_breaker(_ABSENT_SERVICE)
    reset_logger()


def _feed_body() -> bytes:
    """A well-formed feed with four recent items.

    Recent matters: the fetcher drops entries older than max_days_lookback, and a feed
    of dropped entries would give zero processed items, which is a different state than
    the one under test.
    """
    now = datetime.now(UTC)
    items = "".join(
        f"""
  <item>
    <title>Item {n}</title>
    <link>http://127.0.0.1/item-{n}</link>
    <guid>verify-chain-item-{n}</guid>
    <description>Body text for item {n}, long enough to summarize.</description>
    <pubDate>{format_datetime(now - timedelta(hours=n))}</pubDate>
  </item>"""
        for n in range(1, _ITEM_COUNT + 1)
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Verify Chain Feed</title>
  <link>http://127.0.0.1/</link>
  <description>local</description>{items}
</channel></rss>""".encode()


@pytest.fixture
def local_feed_url() -> Iterator[str]:
    """A real RSS feed served over a real socket on the loopback interface."""
    body = _feed_body()

    class FeedHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # BaseHTTPRequestHandler dispatches on this name
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/feed.xml"
    finally:
        server.shutdown()
        server.server_close()


def _drive_real_run(
    feed_url: str, open_circuit_inside_run: bool
) -> tuple[dict[str, Any], list[dict]]:
    """Drive the real orchestrator over a real feed with the LLM circuit open.

    open_circuit_inside_run decides whether the breaker crosses its threshold while the
    run id is set — the difference between a run that burns the quota itself and one
    that starts against a quota already gone. Both are real; only the first leaves a
    circuit_breaker.state event behind, which is the whole point of covering both.

    Returns the stats the orchestrator itself returned and the events this run wrote.
    """
    run_id = str(uuid.uuid4())
    config = dataclasses.replace(Config.from_file(), llm_light_service=_ABSENT_SERVICE)

    def open_the_circuit() -> None:
        reset_circuit_breaker(_ABSENT_SERVICE)
        circuit = get_circuit_breaker(_ABSENT_SERVICE)
        for _ in range(3):
            circuit.record_failure(RuntimeError("insufficient_quota"))
        assert circuit.check_can_proceed() is False, "the circuit must actually be open"

    if not open_circuit_inside_run:
        open_the_circuit()

    set_run_id(run_id)
    if open_circuit_inside_run:
        open_the_circuit()

    storage, source = setup_isolated_run(feed_url, "rss")
    orchestrator = build_orchestrator(config, storage, Console(quiet=True))
    stats = orchestrator.fetch_source_content(source)

    set_run_id(None)
    return stats, read_run_events(get_logger().base_dir, run_id)


@pytest.fixture
def quota_exhausted_run(local_feed_url: str) -> tuple[dict[str, Any], list[dict]]:
    """A run that burns through the quota itself and opens the circuit as it goes."""
    return _drive_real_run(local_feed_url, open_circuit_inside_run=True)


@pytest.fixture
def already_open_run(local_feed_url: str) -> tuple[dict[str, Any], list[dict]]:
    """A run that starts against a circuit that was already open."""
    return _drive_real_run(local_feed_url, open_circuit_inside_run=False)


def test_the_run_really_reaches_the_state_under_test(quota_exhausted_run) -> None:
    """
    The precondition, asserted before anything is concluded from it: this run fetched
    real items, refused every one of them at the open circuit, wrote a real
    circuit_breaker.state open event, and made no LLM call.

    BREAKS: the classification tests below pass against a run that never reached the
    state, which is exactly the defect this file replaces.
    """
    stats, events = quota_exhausted_run

    assert stats["items_fetched"] == _ITEM_COUNT
    assert stats["items_processed"] == _ITEM_COUNT

    refusals = [e for e in stats["errors"] if "circuit breaker is open" in e]
    assert len(refusals) == _ITEM_COUNT, (
        f"every item must have been refused, got {stats['errors']}"
    )

    open_events = [
        e
        for e in events
        if e["event"] == "circuit_breaker.state" and e.get("state") == "open"
    ]
    assert len(open_events) == 1
    assert open_events[0]["reason"] == "threshold_exceeded"
    assert open_events[0]["failure_count"] == 3

    assert not [e for e in events if e["event"] == "llm.call"], (
        "an open circuit must raise before the LLM is called"
    )


def test_circuit_open_is_reported_from_a_real_run(quota_exhausted_run) -> None:
    """
    SC-3: a quota-exhausted run renders as circuit-open, distinct from every other
    state, and the downstream links say they were never reached rather than empty.

    BREAKS: a run that stopped because the operator's quota is gone is reported as a
    generic error or an empty result — the Principle II collapse SC-3 exists to
    prevent, and the one an operator would act on wrongly.
    """
    stats, events = quota_exhausted_run

    links = {
        link.name: link
        for link in render_report(stats, events, "unused-source-id", full=False)
    }

    assert links["summarize"].status == "circuit-open"
    assert f"{_ITEM_COUNT} item(s) refused unattempted" in links["summarize"].detail
    assert "opened during this run" in links["summarize"].detail
    assert links["evaluate"].status == "never-reached"
    assert links["store"].status == "never-reached"
    assert links["embed"].status == "never-reached"
    assert links["deep_extract"].status == "skipped-by-flag"

    distinct = {links[n].status for n in ("summarize", "evaluate", "deep_extract")}
    assert len(distinct) == 3, f"states must stay distinguishable, got {distinct}"


def test_a_circuit_already_open_at_the_start_is_still_circuit_open(
    already_open_run,
) -> None:
    """
    The circuit was open before this run began, so the run refuses every item and never
    logs the transition. It is still the circuit-open state, and the report says the
    quota was already gone rather than that this run spent it.

    BREAKS: the state is gated on an event the run had no occasion to write, so an
    operator whose quota died yesterday sees "error" and goes looking for a bug in the
    code. That is the same collapse, reintroduced from the other side.
    """
    stats, events = already_open_run

    assert not [
        e
        for e in events
        if e["event"] == "circuit_breaker.state" and e.get("state") == "open"
    ], "this run must not have logged the transition, or it proves the wrong thing"

    refusals = [e for e in stats["errors"] if "circuit breaker is open" in e]
    assert len(refusals) == _ITEM_COUNT

    links = {
        link.name: link
        for link in render_report(stats, events, "unused-source-id", full=False)
    }

    assert links["summarize"].status == "circuit-open"
    assert "was already open when the run started" in links["summarize"].detail
    assert links["store"].status == "never-reached"


def test_circuit_open_outranks_the_failures_that_opened_it(
    quota_exhausted_run,
) -> None:
    """
    SC-3 at the shape a real CLI run produces. A fresh process starts with the circuit
    closed, so a real quota-exhausted run always carries BOTH the llm.call error events
    from the calls that opened the circuit AND the refusals that followed. The link
    must report the circuit, not just the errors — the errors are the cause, the
    refusals are what the operator needs to act on.

    The error events here are real: a real ContentSummarizer call against the same
    absent service, failing through the same except clause that emits them in
    production, under this run's id.

    BREAKS: the state is unreachable again — every quota-exhausted run collapses back
    into "error" the moment it has failure events of its own, which it always does.
    """
    stats, events = quota_exhausted_run
    run_id = events[0]["run_id"]

    set_run_id(run_id)
    reset_circuit_breaker(_ABSENT_SERVICE)
    summarizer = ContentSummarizer(_ABSENT_SERVICE)
    for _ in range(3):
        with pytest.raises(Exception):  # noqa: B017 - any local failure emits the event
            summarizer.summarize_with_analysis(content="body text", title="t")
    set_run_id(None)

    events = read_run_events(get_logger().base_dir, run_id)
    failure_events = [
        e
        for e in events
        if e["event"] == "llm.call"
        and e.get("action") == "summarize"
        and e.get("status") == "error"
    ]
    assert len(failure_events) == 3, "the run must now carry its own failure events"

    links = {
        link.name: link
        for link in render_report(stats, events, "unused-source-id", full=False)
    }

    assert links["summarize"].status == "circuit-open"
    assert "3 call(s) failed before it opened" in links["summarize"].detail
    assert f"{_ITEM_COUNT} item(s) refused unattempted" in links["summarize"].detail


def test_a_recovering_circuit_is_not_read_as_one_that_opened_here() -> None:
    """
    The breaker logs its other transitions through the same event name. A run that finds
    an already-open circuit crossing into recovery writes circuit_breaker.state with
    state="half_open" and no open event, and that must still be worded as a circuit that
    was already open — this run did not spend the quota.

    Driven through a real CircuitBreaker with a zero recovery window, so the transition
    is the real one its own check_can_proceed makes rather than a hand-written line.

    BREAKS: matching the event name alone, so any transition — including a recovery, or
    a close — reads as "this run opened the circuit" and the operator is told the wrong
    thing about where their quota went.
    """
    breaker = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0)
    for _ in range(3):
        breaker.record_failure(RuntimeError("insufficient_quota"))

    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    assert breaker.check_can_proceed() is True, "a zero recovery window must half-open"
    set_run_id(None)

    events = read_run_events(get_logger().base_dir, run_id)
    states = [e for e in events if e["event"] == "circuit_breaker.state"]
    assert [e["state"] for e in states] == ["half_open"], (
        f"this run must carry a transition that is not an open, got {states}"
    )

    stats = {
        "items_fetched": _ITEM_COUNT,
        "items_processed": _ITEM_COUNT,
        "items_new": 0,
        "items_updated": 0,
        "errors": [
            f"Failed to analyze item 'Item {n}': LLM circuit breaker is open "
            "(quota exhausted). Recovery in 3600s"
            for n in range(1, _ITEM_COUNT + 1)
        ],
        "new_high_priority_items": [],
    }

    links = {
        link.name: link
        for link in render_report(stats, events, "unused-source-id", full=False)
    }

    assert links["summarize"].status == "circuit-open"
    assert "was already open when the run started" in links["summarize"].detail


def test_failures_that_never_opened_the_circuit_stay_an_error() -> None:
    """
    The other side of the same branch: calls that failed for a reason the breaker does
    not count leave no open event, so the link is an error and not circuit-open.

    Without this, moving the circuit check ahead of the events check could swallow every
    ordinary failure into "circuit-open" and the distinction would be gone again.

    BREAKS: a broken model or a bad prompt is reported as quota exhaustion, sending the
    operator to check billing for a bug in the code.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    reset_circuit_breaker(_ABSENT_SERVICE)

    summarizer = ContentSummarizer(_ABSENT_SERVICE)
    errors = []
    for _ in range(3):
        with pytest.raises(Exception) as exc_info:
            summarizer.summarize_with_analysis(content="body text", title="t")
        errors.append(f"Failed to analyze item 'An item': {exc_info.value}")
    set_run_id(None)

    assert get_circuit_breaker(_ABSENT_SERVICE).check_can_proceed() is True, (
        "a non-quota failure must not open the circuit, or this proves nothing"
    )

    events = read_run_events(get_logger().base_dir, run_id)
    assert not [
        e
        for e in events
        if e["event"] == "circuit_breaker.state" and e.get("state") == "open"
    ]

    stats = {
        "items_fetched": 3,
        "items_processed": 3,
        "items_new": 0,
        "items_updated": 0,
        "errors": errors,
        "new_high_priority_items": [],
    }

    links = {
        link.name: link
        for link in render_report(stats, events, "unused-source-id", full=False)
    }

    assert links["summarize"].status == "error"
    assert "3 of 3 calls failed" in links["summarize"].detail
