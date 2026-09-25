"""Integration tests for concurrent-POST race fix on /api/entries/{id}/extract.

Invariants protected:
- INV-EXTRACT-RACE-1 (new): At most one extractor.extract() call per content_id
  across concurrent POST /api/entries/{id}/extract requests.  Two simultaneous
  first-extract POSTs must NOT both invoke the LLM and double-bill.
- INV-004 (preserved): After extraction is stored, repeated POST returns cached
  deep_extraction without invoking the extractor.  The lock must NOT regress
  this path.
- INV-EXTRACT-NONBLOCK-1 (task 1.5): In-flight extraction via asyncio.to_thread
  does NOT block other API routes.  GET /api/sources must return within ~100ms
  while a 2-second extraction is running in a worker thread.
- Per-key isolation: Concurrent POSTs for DIFFERENT content_ids must NOT
  serialize against each other -- per-content_id locks, not a global lock
  (verified structurally: two distinct lock objects are created, not one
  shared one).

Concurrency approach for INV-EXTRACT-RACE-1:
  asyncio.gather + httpx.AsyncClient(ASGITransport) schedules both requests
  as async tasks in the same event loop.  With asyncio.to_thread (task 1.5),
  extract() runs in a worker thread and the event loop is FREE while the thread
  sleeps.  The asyncio.Lock holds in coroutine-space across the to_thread await,
  so Task 2 still serializes behind Task 1 and re-reads the cached result.

  This is the correct semantics for asyncio.Lock + asyncio.to_thread:
  serialization is guaranteed because (a) the lock is acquired before extract()
  and released after write-back, and (b) the lock is coroutine-scoped so it is
  held by the awaiting coroutine across the entire to_thread call.

Per-key isolation is verified structurally: for two different content_ids,
  _get_extract_lock must create separate Lock objects (dict[str, Lock] keyed
  by content_id).  A global-lock implementation would use one object for all
  keys; the structural test catches that without relying on wall-clock timing.

Real collaborators throughout: app.state.deep_extractor is a real
ContentDeepExtractor talking to the local stub LLM server
(conftest.local_pipeline_stub), through its own real circuit breaker and real
llm_core.complete. A slow extraction is produced by the stub SERVER sleeping
before it answers -- driven by embedding
`f"{DEEP_EXTRACT_DELAY_PREFIX}<seconds>"` in the seeded content, which travels
through the real request path (content -> user prompt -> HTTP body) -- not by a
stand-in extractor's own time.sleep(). The server-side sleep is itself a plain
time.sleep() (see conftest.local_pipeline_stub): it runs inside the HTTP
handler's own thread, not a coroutine, so asyncio.sleep() would be the wrong
tool there too.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from prismis_daemon.api import _extract_locks, _get_extract_lock, app, get_storage
from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import (
    DEEP_EXTRACT_DELAY_PREFIX,
    DEEP_EXTRACT_STUB_SYNTHESIS,
    LOCAL_DEEP_SERVICE,
    TEST_API_KEY,
    add_new_content,
    configure_local_services,
)

_API_KEY = TEST_API_KEY


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None]:
    """Reset circuit breaker registry and lock registry before/after each test."""
    reset_circuit_breaker()
    _extract_locks.clear()
    yield
    reset_circuit_breaker()
    _extract_locks.clear()


@pytest.fixture
def real_deep_extractor(local_pipeline_stub: str) -> ContentDeepExtractor:
    """A real ContentDeepExtractor wired to the local stub LLM server.

    Not a stand-in: every call reaches the real ContentDeepExtractor.extract(), the
    real circuit breaker, and a real HTTP round trip to a server on loopback.
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    return ContentDeepExtractor(LOCAL_DEEP_SERVICE)


class _CountingExtractor:
    """Call-through spy around a real ContentDeepExtractor.  Records call count.

    Not a stand-in: `extract()` delegates every call to the real extractor and
    returns exactly what it returns.
    """

    def __init__(self, real: ContentDeepExtractor) -> None:
        self._real = real
        self.call_count = 0

    def extract(self, content: str, title: str = "", url: str = "") -> dict | None:
        self.call_count += 1
        return self._real.extract(content=content, title=title, url=url)


def _seed_entry(
    storage: Storage,
    analysis: dict | None = None,
    external_id: str = "race-test-001",
    content: str = "Article body text for race condition testing.",
) -> str:
    """Insert a content row and return its string ID."""
    source_id = storage.add_source("https://example.com/rss", "rss", "Race Test Feed")
    item = ContentItem(
        source_id=source_id,
        external_id=external_id,
        title="Race Test Article",
        url="https://example.com/race-test",
        content=content,
        summary="Light summary.",
        analysis=analysis,
        priority="high",
    )
    return add_new_content(storage, item)


def _override_storage(storage: Storage) -> None:
    """Install storage dependency override on the FastAPI app."""

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage


def _clear_overrides() -> None:
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# INV-EXTRACT-RACE-1 / SC-RACE-1: Concurrent POSTs invoke extractor exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_posts_same_id_invoke_extractor_once(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    INV-EXTRACT-RACE-1 / SC-RACE-1: Two concurrent POSTs for the same content_id
    must result in exactly one call to extractor.extract().

    Both tasks are scheduled via asyncio.gather + ASGITransport. Task 1 acquires
    the lock and awaits the real extraction over asyncio.to_thread; Task 2 cannot
    proceed until Task 1 exits the async with block. When Task 2 re-acquires the
    lock it finds deep_extraction populated and returns the cached result --
    call_count stays 1.

    BREAKS: Without the asyncio.Lock both tasks would pass the idempotency check
    before either writes and both would invoke extract() -- call_count == 2.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    spy = _CountingExtractor(real_deep_extractor)
    app.state.deep_extractor = spy
    _override_storage(storage)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                ),
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                ),
            )

        assert r1.status_code == 200, (
            f"r1 expected 200, got {r1.status_code}: {r1.text}"
        )
        assert r2.status_code == 200, (
            f"r2 expected 200, got {r2.status_code}: {r2.text}"
        )
        assert spy.call_count == 1, (
            f"INV-EXTRACT-RACE-1: extractor.extract() must be called exactly once "
            f"under concurrent first-extract POSTs; called {spy.call_count} times"
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# SC-RACE-1 supplement: both responses carry identical deep_extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_posts_both_return_identical_extraction(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    SC-RACE-1: Both concurrent POSTs must return the same deep_extraction.

    Task 1 performs the extraction and writes it.  Task 2 waits behind the lock,
    then re-reads the now-populated analysis and returns via the cached path.
    Both responses must carry the same synthesis value.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    app.state.deep_extractor = real_deep_extractor
    _override_storage(storage)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                ),
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                ),
            )

        assert r1.status_code == 200 and r2.status_code == 200, (
            f"Both POSTs must return 200; got {r1.status_code} and {r2.status_code}"
        )
        synth1 = r1.json()["data"]["deep_extraction"]["synthesis"]
        synth2 = r2.json()["data"]["deep_extraction"]["synthesis"]
        assert synth1 == synth2, (
            f"SC-RACE-1: both responses must carry identical synthesis; "
            f"got {synth1!r} vs {synth2!r}"
        )
        assert synth1 == DEEP_EXTRACT_STUB_SYNTHESIS
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# INV-004 / SC-RACE-2: Cached return path unaffected by the lock
# ---------------------------------------------------------------------------


def test_cached_return_post_lock_zero_extractor_calls(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    INV-004 / SC-RACE-2: POST on an entry with existing deep_extraction returns
    the cached value without invoking the extractor.

    The lock must not regress the cached path: lock acquisition when uncontended
    is cheap and the cached-return branch inside the critical section must fire
    before the extractor is consulted.

    BREAKS: If the async with wraps too little (excludes the idempotency check)
    the cached path still works -- but is unprotected.  If the async with
    breaks the cached path flow, INV-004 is violated.
    """
    storage = Storage(test_db)
    existing = {
        "synthesis": "Pre-existing synthesis -- must be returned unchanged.",
        "quotables": ["Pre-existing quote."],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-04-27T10:00:00+00:00",
    }
    content_id = _seed_entry(
        storage,
        analysis={"metrics": {"score": 80}, "deep_extraction": existing},
    )
    spy = _CountingExtractor(real_deep_extractor)
    app.state.deep_extractor = spy
    _override_storage(storage)

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
        )
        assert response.status_code == 200, (
            f"INV-004: cached POST must return 200, "
            f"got {response.status_code}: {response.text}"
        )
        body = response.json()
        assert body["success"] is True
        returned = body["data"]["deep_extraction"]
        assert returned["synthesis"] == existing["synthesis"], (
            "INV-004: must return existing synthesis unchanged"
        )
        # The distinguishing outcome: a real extraction would have overwritten this
        # with the stub server's canned synthesis, not left the seeded value in place.
        assert returned["synthesis"] != DEEP_EXTRACT_STUB_SYNTHESIS
        assert spy.call_count == 0, (
            f"INV-004: extractor must NOT be called when deep_extraction already "
            f"exists; call_count={spy.call_count}"
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# Per-key isolation: different content_ids use separate Lock objects
# ---------------------------------------------------------------------------


def test_different_content_ids_use_separate_lock_objects(test_db: Path) -> None:
    """
    Per-key isolation (structural): _get_extract_lock must return a distinct
    asyncio.Lock object for each content_id.

    BREAKS: A global-lock implementation -- one lock shared across all content
    ids -- would return the same object regardless of the key.  This causes all
    concurrent extractions to serialize globally, not just per-content_id.

    This test verifies the dict[str, asyncio.Lock] keyed-registry contract
    directly on the helper function, without relying on wall-clock timing.
    """
    id_a = "00000000-0000-0000-0000-000000000001"
    id_b = "00000000-0000-0000-0000-000000000002"

    lock_a = _get_extract_lock(id_a)
    lock_b = _get_extract_lock(id_b)

    assert lock_a is not lock_b, (
        "Per-key isolation: different content_ids must get different Lock objects; "
        "same object returned -- this is a global lock, not a per-key lock"
    )
    assert isinstance(lock_a, asyncio.Lock)
    assert isinstance(lock_b, asyncio.Lock)

    # Each key returns the same object on repeated calls (lazy-create is stable).
    assert _get_extract_lock(id_a) is lock_a, (
        "_get_extract_lock must return the same Lock for the same content_id"
    )


# ---------------------------------------------------------------------------
# Lock registry cleanup: registry bounded to in-flight content_ids only
# ---------------------------------------------------------------------------


def test_lock_registry_cleared_after_successful_extraction(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    Registry is bounded: after extraction completes, _extract_locks must NOT
    retain the content_id entry.

    BREAKS: If the pop() is absent or never reached, the registry grows without
    bound over the daemon's lifetime -- one Lock object per ever-extracted
    content_id.

    This test verifies the pop()-while-holding-lock cleanup in `extract_entry`
    (src/prismis_daemon/api.py).
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    spy = _CountingExtractor(real_deep_extractor)
    app.state.deep_extractor = spy
    _override_storage(storage)

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        assert spy.call_count == 1

        assert content_id not in _extract_locks, (
            f"Registry must not retain content_id after extraction completes; "
            f"keys present: {list(_extract_locks.keys())}"
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# INV-EXTRACT-NONBLOCK-1 / SC-NONBLOCK-1: In-flight extraction does not block
# other API routes (task 1.5 -- asyncio.to_thread fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nonblock_sources_returns_during_inflight_extraction(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    INV-EXTRACT-NONBLOCK-1 / SC-NONBLOCK-1: GET /api/sources must return within
    ~100ms while a POST /api/entries/{id}/extract is mid-extraction (2s sleep).

    Before task 1.5: extractor.extract() ran synchronously on the event-loop
    thread, blocking ALL other routes for the full LLM call duration.  GET
    /api/sources would block for ~2s.

    After task 1.5: asyncio.to_thread() offloads the blocking call to a worker
    thread.  The event loop is free to serve GET /api/sources immediately.
    Elapsed time for the GET must be well under 0.5s even though the POST is
    sleeping for 2s in its worker thread.

    The 2s sleep is produced by the local stub LLM server (see
    conftest.local_pipeline_stub), triggered by a delay marker embedded in the
    seeded content -- not by a stand-in extractor's own time.sleep().

    BREAKS: If asyncio.to_thread() is absent (reverted to direct sync call),
    the event loop is blocked during extract() and GET /api/sources cannot be
    served until the 2s sleep completes -- elapsed would be ~2s, far above 0.5s.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(
        storage,
        analysis={"metrics": {"score": 80}},
        external_id="race-nonblock-001",
        content=(
            "Article body text for race condition testing. "
            f"{DEEP_EXTRACT_DELAY_PREFIX}2.0"
        ),
    )

    app.state.deep_extractor = real_deep_extractor
    _override_storage(storage)

    get_elapsed: list[float] = []

    async def do_post(client: AsyncClient) -> None:
        await client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
            timeout=10.0,
        )

    async def do_get(client: AsyncClient, start: float) -> None:
        r = await client.get(
            "/api/sources",
            headers={"X-API-Key": _API_KEY},
            timeout=5.0,
        )
        # Measured from `start`, captured before gather() below, not from entry into
        # this coroutine: if the event loop is blocked, the 0.1s asyncio.sleep() that
        # schedules this call and the call itself both stall until the block clears,
        # so a t0 taken here would silently absorb the whole stall into "time not
        # measured" and this assertion would pass no matter how blocked the loop was.
        elapsed = time.monotonic() - start
        get_elapsed.append(elapsed)
        assert r.status_code == 200, (
            f"GET /api/sources expected 200, got {r.status_code}: {r.text}"
        )

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            start = time.monotonic()

            # Small delay on do_get so the POST is definitely in-flight (lock
            # acquired, worker thread sleeping) before the GET fires.
            async def delayed_get() -> None:
                await asyncio.sleep(0.1)
                await do_get(client, start)

            await asyncio.gather(do_post(client), delayed_get())

        assert len(get_elapsed) == 1
        assert get_elapsed[0] < 0.5, (
            f"INV-EXTRACT-NONBLOCK-1: GET /api/sources took {get_elapsed[0]:.3f}s "
            f"during in-flight extraction -- event loop appears blocked. "
            f"Expected < 0.5s. asyncio.to_thread() may be absent or reverted."
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# SC-RACE-PRESERVED-1: Lock serialization still holds after asyncio.to_thread
# wrap (task 1.5 regression check -- confirms INV-EXTRACT-RACE-1 unaffected)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_serializes_with_to_thread(
    test_db: Path,
    real_deep_extractor: ContentDeepExtractor,
) -> None:
    """
    SC-RACE-PRESERVED-1 / INV-EXTRACT-RACE-1: asyncio.to_thread() must NOT
    break the serialization guarantee -- two concurrent first-extract POSTs
    for the same content_id must still invoke extractor.extract() exactly once.

    With asyncio.to_thread, the event loop is FREE while the worker thread runs.
    This means both tasks CAN make progress concurrently in the event loop.
    Task 2 reaches the 'async with _get_extract_lock(content_id):' and blocks
    there until Task 1 finishes and releases the lock.  The lock is coroutine-
    scoped: it is held by Task 1's coroutine across the entire to_thread await.
    Task 2 then re-reads the now-populated analysis and returns via the cached
    path -- call_count stays 1.

    BREAKS: If the asyncio.Lock were removed (or accidentally made thread-scoped),
    both tasks would pass the idempotency check before either writes, and both
    would invoke extract() -- call_count == 2 (double billing).

    A short delay (0.2s), produced by the local stub LLM server, reliably opens
    the race window: the event loop is freed during the worker thread's wait on
    the stub's response, so Task 2 can advance to the lock acquisition point
    before Task 1 completes.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(
        storage,
        analysis={"metrics": {"score": 80}},
        external_id="race-to-thread-001",
        content=(
            "Article body text for race condition testing. "
            f"{DEEP_EXTRACT_DELAY_PREFIX}0.2"
        ),
    )

    spy = _CountingExtractor(real_deep_extractor)
    app.state.deep_extractor = spy
    _override_storage(storage)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            r1, r2 = await asyncio.gather(
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                    timeout=10.0,
                ),
                client.post(
                    f"/api/entries/{content_id}/extract",
                    headers={"X-API-Key": _API_KEY},
                    timeout=10.0,
                ),
            )

        assert r1.status_code == 200, (
            f"r1 expected 200, got {r1.status_code}: {r1.text}"
        )
        assert r2.status_code == 200, (
            f"r2 expected 200, got {r2.status_code}: {r2.text}"
        )
        assert spy.call_count == 1, (
            f"SC-RACE-PRESERVED-1 / INV-EXTRACT-RACE-1: extractor.extract() must "
            f"be called exactly once under concurrent first-extract POSTs even with "
            f"asyncio.to_thread; called {spy.call_count} times. "
            f"Lock serialization is broken or the lock was removed."
        )
        synth1 = r1.json()["data"]["deep_extraction"]["synthesis"]
        synth2 = r2.json()["data"]["deep_extraction"]["synthesis"]
        assert synth1 == synth2 == DEEP_EXTRACT_STUB_SYNTHESIS, (
            f"SC-RACE-PRESERVED-1: both responses must carry the same synthesis; "
            f"got {synth1!r} vs {synth2!r}"
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# Exception propagation through asyncio.to_thread (discovered invariant)
# The builder assessed exception propagation as LOW risk without testing it.
# Risk re-assessment: MEDIUM -- CircuitOpenError must survive the thread hop as
# its exact type, because api.py routes CircuitOpenError -> 503 reason=circuit_open
# and bare Exception -> 500. Type loss after to_thread would silently mis-route
# a retryable circuit-open to a 500, wasting user retries and obscuring billing
# protection status.  asyncio.to_thread() re-raises verbatim, but this must be
# verified -- not assumed.
# ---------------------------------------------------------------------------


def test_circuit_open_error_through_to_thread_returns_503(test_db: Path) -> None:
    """
    Discovered invariant: CircuitOpenError raised inside asyncio.to_thread()
    must propagate to the handler as CircuitOpenError, not be wrapped or
    swallowed, so that api.py routes it to 503 reason=circuit_open.

    If asyncio.to_thread() wrapped exceptions in a generic wrapper or if the
    except-clause ordering changed, a CircuitOpenError would fall through to the
    bare `except Exception` branch and return 500 instead of 503.  A 500 tells
    the caller 'unexpected server error'; a 503 tells it 'service unavailable,
    retry later'.  The distinction matters for client retry logic and for
    understanding whether the circuit breaker is working.

    Driven by a real ContentDeepExtractor whose circuit breaker (real, service-keyed
    registry) is opened with real quota failures beforehand -- the same
    `record_failure(RuntimeError("insufficient_quota"))` path the constitution's
    other circuit-open tests use (test_verify_chain_deep_circuit_integration.py),
    rather than a stand-in extractor that raises on command. No network call is
    made: extract() raises CircuitOpenError before it ever reaches complete().

    BREAKS: If CircuitOpenError's type were lost in transit (e.g., wrapped in
    ExceptionGroup or caught by the wrong except branch), the response would be
    500 with a generic error message instead of 503 with reason=circuit_open.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    breaker = get_circuit_breaker(LOCAL_DEEP_SERVICE)
    for _ in range(breaker.failure_threshold):
        breaker.record_failure(RuntimeError("insufficient_quota"))
    assert breaker.check_can_proceed() is False, "setup: the circuit must be open"

    real_extractor = ContentDeepExtractor(LOCAL_DEEP_SERVICE)
    app.state.deep_extractor = real_extractor
    _override_storage(storage)

    try:
        client = TestClient(app)
        response = client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
        )
        assert response.status_code == 503, (
            f"CircuitOpenError raised inside asyncio.to_thread must produce 503; "
            f"got {response.status_code}: {response.text}. "
            f"Exception type may have been lost in the thread hop."
        )
        body = response.json()
        assert body.get("data", {}).get("reason") == "circuit_open", (
            f"503 response must carry reason=circuit_open; "
            f"got reason={body.get('data', {}).get('reason')!r}. "
            f"Handler may have routed to the bare Exception branch instead."
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None
