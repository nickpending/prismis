"""Integration tests for concurrent-POST race fix on /api/entries/{id}/extract.

Invariants protected:
- INV-EXTRACT-RACE-1 (new): At most one extractor.extract() call per content_id
  across concurrent POST /api/entries/{id}/extract requests.  Two simultaneous
  first-extract POSTs must NOT both invoke the LLM and double-bill.
- INV-004 (preserved): After extraction is stored, repeated POST returns cached
  deep_extraction without invoking the extractor.  The lock must NOT regress
  this path.
- Per-key isolation: Concurrent POSTs for DIFFERENT content_ids must NOT
  serialize against each other -- per-content_id locks, not a global lock
  (verified structurally: two distinct lock objects are created, not one
  shared one).

Concurrency approach for INV-EXTRACT-RACE-1:
  asyncio.gather + httpx.AsyncClient(ASGITransport) schedules both requests
  as async tasks in the same event loop.  extractor.extract() is a synchronous
  blocking call inside the async handler, so the event loop is blocked while
  Task 1 holds the lock.  Task 2 cannot make progress until Task 1 exits the
  async with block.  After Task 1 completes, Task 2 re-acquires the (now
  fresh) lock, re-reads the entry, finds deep_extraction populated, and
  returns via the cached path.  extractor.extract() is called exactly once.

  This is the correct semantics for asyncio.Lock with a synchronous extractor:
  serialization is guaranteed because (a) the lock is acquired before extract()
  and released after write-back, and (b) the blocking sync call prevents any
  interleaving during the critical section.

Per-key isolation is verified structurally: for two different content_ids,
  _get_extract_lock must create separate Lock objects (dict[str, Lock] keyed
  by content_id).  A global-lock implementation would use one object for all
  keys; the structural test catches that without relying on wall-clock timing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from prismis_daemon.api import _extract_locks, _get_extract_lock, app, get_storage
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

_API_KEY = "prismis-api-4d5e"


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


class _StubExtractor:
    """Controlled deep extractor stub.  Records call count."""

    def __init__(self, result: dict | None) -> None:
        self._result = result
        self.call_count = 0

    def extract(self, content: str, title: str = "", url: str = "") -> dict | None:
        self.call_count += 1
        return self._result


def _seed_entry(
    storage: Storage,
    analysis: dict | None = None,
    external_id: str = "race-test-001",
) -> str:
    """Insert a content row and return its string ID."""
    source_id = storage.add_source("https://example.com/rss", "rss", "Race Test Feed")
    item = ContentItem(
        source_id=source_id,
        external_id=external_id,
        title="Race Test Article",
        url="https://example.com/race-test",
        content="Article body text for race condition testing.",
        summary="Light summary.",
        analysis=analysis,
        priority="high",
    )
    return storage.add_content(item)


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
) -> None:
    """
    INV-EXTRACT-RACE-1 / SC-RACE-1: Two concurrent POSTs for the same content_id
    must result in exactly one call to extractor.extract().

    Both tasks are scheduled via asyncio.gather + ASGITransport.  extractor.extract()
    is synchronous and blocking: Task 1 holds the lock and blocks the event loop
    during extract().  Task 2 cannot proceed until Task 1 exits the async with
    block.  When Task 2 re-acquires the lock it finds deep_extraction populated
    and returns the cached result -- call_count stays 1.

    BREAKS: Without the asyncio.Lock both tasks would pass the idempotency check
    before either writes and both would invoke extract() -- call_count == 2.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    canned = {
        "synthesis": "Race test synthesis.",
        "quotables": ["Key quote."],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-05-21T10:00:00+00:00",
    }
    stub = _StubExtractor(result=canned)
    app.state.deep_extractor = stub
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
        assert stub.call_count == 1, (
            f"INV-EXTRACT-RACE-1: extractor.extract() must be called exactly once "
            f"under concurrent first-extract POSTs; called {stub.call_count} times"
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
) -> None:
    """
    SC-RACE-1: Both concurrent POSTs must return the same deep_extraction.

    Task 1 performs the extraction and writes it.  Task 2 waits (event-loop
    blocked by Task 1's sync extract call), then re-reads the now-populated
    analysis and returns via the cached path.  Both responses must carry the
    same synthesis value.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    canned = {
        "synthesis": "Identical extraction result.",
        "quotables": ["Shared quote."],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-05-21T10:00:00+00:00",
    }
    stub = _StubExtractor(result=canned)
    app.state.deep_extractor = stub
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
        assert synth1 == canned["synthesis"]
    finally:
        _clear_overrides()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# INV-004 / SC-RACE-2: Cached return path unaffected by the lock
# ---------------------------------------------------------------------------


def test_cached_return_post_lock_zero_extractor_calls(test_db: Path) -> None:
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
    stub = _StubExtractor(result={"synthesis": "SHOULD NOT APPEAR"})
    app.state.deep_extractor = stub
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
        assert stub.call_count == 0, (
            f"INV-004: extractor must NOT be called when deep_extraction already "
            f"exists; call_count={stub.call_count}"
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


def test_lock_registry_cleared_after_successful_extraction(test_db: Path) -> None:
    """
    Registry is bounded: after extraction completes, _extract_locks must NOT
    retain the content_id entry.

    BREAKS: If the pop() is absent or never reached, the registry grows without
    bound over the daemon's lifetime -- one Lock object per ever-extracted
    content_id.

    This test verifies the pop()-while-holding-lock cleanup at api.py:1180.
    """
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})

    canned = {
        "synthesis": "Registry cleanup test.",
        "quotables": [],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-05-21T10:00:00+00:00",
    }
    stub = _StubExtractor(result=canned)
    app.state.deep_extractor = stub
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
        assert stub.call_count == 1

        assert content_id not in _extract_locks, (
            f"Registry must not retain content_id after extraction completes; "
            f"keys present: {list(_extract_locks.keys())}"
        )
    finally:
        _clear_overrides()
        app.state.deep_extractor = None
