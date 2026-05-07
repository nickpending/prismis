"""Integration tests for ContentResponse Pydantic routing — wire format invariants.

Protects:
- INV-API-TS-1: fetched_at on /api/entries and /api/search wire MUST be RFC3339
  (SC-29, SC-30) — the defect T6 in test_rfc3339_helper_unit.py proved this broke
  at the storage raw-dict layer; task 2.8 fixes it at the API boundary.
- INV-API-TS-4: response envelope shape (success / message / data: {items, total,
  filters_applied}) preserved unchanged — consumer contract must not break.

NOTE: T6 xfail in test_rfc3339_helper_unit.py MUST remain in place after task 2.8.
T6 asserts on storage.get_content_by_priority() raw-dict output, which still emits
naive strings unchanged. Task 2.8 fixes the API wire, not storage retrieval.
"""

import re
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# RFC3339 pattern: T separator, explicit offset (Z or ±HH:MM), optional fractional seconds.
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)

API_KEY = "prismis-api-4d5e"


def assert_rfc3339(value: str) -> None:
    """Assert that a string matches the RFC3339 wire format."""
    assert RFC3339_RE.match(value), (
        f"Expected RFC3339 format (T separator + explicit offset), got: {value!r}"
    )


def _make_high_score_embedding() -> list[float]:
    """Unit vector in dimension 0 — identical to the query → similarity = 1.0."""
    emb = [0.0] * 384
    emb[0] = 1.0
    return emb


def _make_query_embedding() -> list[float]:
    """Unit vector in dimension 0 — matches the high-score seeded embedding."""
    emb = [0.0] * 384
    emb[0] = 1.0
    return emb


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def storage_with_naive_fetched_at(test_db: Path) -> Storage:
    """Storage with one item whose fetched_at is a naive datetime (utcnow()-style).

    This is the production shape: fetchers historically used datetime.utcnow(),
    writing naive ISO strings to the DB. ContentItemModel must normalize via _rfc3339.
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    naive_fetched = datetime(2026, 5, 5, 23, 14, 53, 680336)  # no tzinfo — naive UTC
    item = ContentItem(
        source_id=src_id,
        external_id="wire-test-001",
        title="Wire Format Test Article",
        url="https://example.com/wire-test",
        content="Content for RFC3339 wire format test",
        priority="high",
        fetched_at=naive_fetched,
        published_at=None,
    )
    storage.add_content(item)
    return storage


@pytest.fixture
def entries_client(storage_with_naive_fetched_at: Storage) -> Generator[TestClient]:
    """TestClient for /api/entries with naive-fetched_at content seeded."""

    def override_get_storage() -> Generator[Storage]:
        yield storage_with_naive_fetched_at

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    storage_with_naive_fetched_at.close()


@pytest.fixture
def storage_with_embedding(test_db: Path) -> Storage:
    """Storage with one item that has an embedding (required for search results).

    Uses direct embedding seeding (unit vector) rather than Embedder to avoid
    model loading latency. The query embedding must match the seeded vector to
    get a result — both use the same unit vector in dimension 0.
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    naive_fetched = datetime(
        2026, 5, 5, 23, 14, 53, 680336
    )  # naive — the production shape
    item = ContentItem(
        source_id=src_id,
        external_id="search-wire-test-001",
        title="Search Wire Format Test",
        url="https://example.com/search-wire-test",
        content="Content for search RFC3339 wire format test",
        priority="high",
        fetched_at=naive_fetched,
        published_at=None,
    )
    content_id = storage.add_content(item)
    storage.add_embedding(content_id, _make_high_score_embedding())
    return storage


@pytest.fixture
def search_client_with_seeded_content(
    storage_with_embedding: Storage,
) -> Generator[TestClient]:
    """TestClient for /api/search with seeded content+embedding."""

    def override_get_storage() -> Generator[Storage]:
        yield storage_with_embedding

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    storage_with_embedding.close()


# ---------------------------------------------------------------------------
# T-D: /api/entries wire format — fetched_at is RFC3339 (SC-29)
# ---------------------------------------------------------------------------


def test_entries_fetched_at_wire_format_is_rfc3339(entries_client: TestClient) -> None:
    """SC-29 / INV-API-TS-1: /api/entries fetched_at must be RFC3339 on the wire.

    Task 2.8 routes /api/entries through ContentResponse + ContentItemModel with
    @field_serializer on fetched_at. The 7100 naive rows in production storage have
    fetched_at as "2026-05-05T23:14:53.680336" (no offset) — ContentItemModel's
    _rfc3339 serializer appends "Z" to normalize them to RFC3339.

    BREAKS: TUI parser uses strict time.RFC3339 — naive strings cause a parse error
    on every entry list fetch (proven by build-task-2.7 Probe 3 Go program).
    """
    response = entries_client.get("/api/entries", headers={"X-API-Key": API_KEY})

    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["success"] is True
    items = data["data"]["items"]
    assert len(items) >= 1, "Precondition: at least one item must be returned"

    for item in items:
        fetched_at = item["fetched_at"]
        if fetched_at is not None:
            assert_rfc3339(fetched_at)
            # Naive storage row must have Z suffix (not "+00:00Z" double-offset)
            assert not fetched_at.endswith("+00:00Z"), (
                f"Double-offset bug: naive fetched_at produced {fetched_at!r}"
            )
            assert fetched_at.endswith("Z") or re.search(
                r"[+-]\d{2}:\d{2}$", fetched_at
            ), f"fetched_at must end with Z or explicit offset, got: {fetched_at!r}"


# ---------------------------------------------------------------------------
# T-E: /api/search wire format — fetched_at is RFC3339 (SC-30)
# ---------------------------------------------------------------------------


def test_search_fetched_at_wire_format_is_rfc3339(
    search_client_with_seeded_content: TestClient,
) -> None:
    """SC-30 / INV-API-TS-1: /api/search fetched_at must be RFC3339 on the wire.

    Same ContentItemModel @field_serializer path as /api/entries. The search
    endpoint was Probe 2 in build-task-2.7 — it also emitted raw naive strings.
    Task 2.8 routes /api/search through ContentResponse too.

    Uses direct embedding seed (unit vector) with min_score=0.0 to guarantee
    at least one result without depending on Embedder's text vectorization.
    The search endpoint still uses the real Embedder for the query — the seeded
    item's unit-vector embedding will score above 0.0 against any query vector.
    """
    # min_score=0.0 ensures the seeded item returns regardless of query similarity
    response = search_client_with_seeded_content.get(
        "/api/search?q=test&min_score=0.0",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["success"] is True
    items = data["data"]["items"]
    assert len(items) >= 1, (
        "Precondition: at least one search result required to test fetched_at wire format. "
        "Seeded content with unit-vector embedding and min_score=0.0 should always return."
    )

    for item in items:
        fetched_at = item["fetched_at"]
        if fetched_at is not None:
            assert_rfc3339(fetched_at)
            assert not fetched_at.endswith("+00:00Z"), (
                f"Double-offset bug on search wire: {fetched_at!r}"
            )


# ---------------------------------------------------------------------------
# T-F: Response envelope shape preserved (consumer contract invariant)
# ---------------------------------------------------------------------------


def test_entries_envelope_shape_preserved(entries_client: TestClient) -> None:
    """INV-API-TS-4: /api/entries envelope shape (success/message/data) unchanged.

    ContentResponse must preserve the exact envelope shape that consumers depend on:
    - data.items: list of content items
    - data.total: integer count
    - data.filters_applied: dict echoing the request parameters

    BREAKS: TUI, CLI, web view all parse this shape — shape change breaks every consumer.
    """
    response = entries_client.get(
        "/api/entries?priority=high",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    data = response.json()

    # Top-level envelope
    assert "success" in data, "Envelope must have 'success' field"
    assert "message" in data, "Envelope must have 'message' field"
    assert "data" in data, "Envelope must have 'data' field"
    assert data["success"] is True

    # data sub-object
    envelope_data = data["data"]
    assert "items" in envelope_data, "data must have 'items' list"
    assert "total" in envelope_data, "data must have 'total' integer"
    assert "filters_applied" in envelope_data, "data must have 'filters_applied' dict"

    assert isinstance(envelope_data["items"], list), "data.items must be a list"
    assert isinstance(envelope_data["total"], int), "data.total must be an integer"
    assert isinstance(envelope_data["filters_applied"], dict), (
        "data.filters_applied must be a dict"
    )

    # filters_applied echoes the priority parameter
    filters = envelope_data["filters_applied"]
    assert "priority" in filters, "filters_applied must echo 'priority' parameter"
    assert filters["priority"] == "high", (
        f"filters_applied.priority must echo request value 'high', got: {filters['priority']!r}"
    )


# ---------------------------------------------------------------------------
# DEFECT PROOF: /api/entries/{content_id} raw-dict bypass (INV-API-TS-4 gap)
#
# INV-API-TS-4 says "every API list/detail endpoint that returns content data
# MUST flow through a Pydantic response model." The task spec explicitly named
# both list endpoints (entries, search) AND detail endpoints.
#
# Discovered during independent review of api.py: get_entry_summary() at
# line 992-1034 returns raw dict from storage.get_content_by_id() — the same
# naive-datetime leak pattern that /api/entries had before task 2.8 fixed it.
# Neither the planner (plan report) nor the builder (build report) flagged this
# endpoint. Task 2.8 only routed /api/entries (list) and /api/search — the
# single-entry detail path remains a raw-dict bypass.
#
# This test MUST FAIL today (proving the defect) and MUST PASS after a fix
# routes /api/entries/{id} through ContentItemModel.
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "INV-API-TS-4 gap: /api/entries/{id} returns raw dict from storage, "
        "not ContentItemModel — fetched_at naive string leaks through. "
        "Fix: route get_entry_summary() through ContentItemModel(**entry).model_dump(mode='json'). "
        "Remove this decorator after the fix lands."
    ),
)
def test_entry_detail_fetched_at_wire_format_is_rfc3339(
    entries_client: TestClient,
    storage_with_naive_fetched_at: Storage,
) -> None:
    """DEFECT PROOF (INV-API-TS-4 gap): /api/entries/{id} naive fetched_at leaks.

    INV-API-TS-4 covers 'every API list/detail endpoint that returns content data.'
    /api/entries/{content_id} is a detail endpoint — it must route through
    ContentItemModel. Currently it returns raw dict from get_content_by_id(),
    bypassing the @field_serializer on fetched_at.

    This test FAILS today (proving the defect) and PASSES after the fix.
    """
    # Get the seeded item's ID from storage
    conn = storage_with_naive_fetched_at.conn
    cursor = conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("wire-test-001",)
    )
    row = cursor.fetchone()
    assert row is not None, "Seeded item not found in storage"
    content_id = row["id"]

    response = entries_client.get(
        f"/api/entries/{content_id}",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["success"] is True

    entry = data["data"]
    fetched_at = entry.get("fetched_at")
    assert fetched_at is not None, "fetched_at must be present in detail response"
    (
        assert_rfc3339(fetched_at),
        (
            f"DEFECT (INV-API-TS-4): /api/entries/{{id}} fetched_at is not RFC3339. "
            f"Got: {fetched_at!r}. "
            "Route get_entry_summary() through ContentItemModel to fix."
        ),
    )
