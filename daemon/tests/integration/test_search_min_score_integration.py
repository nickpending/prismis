"""Integration tests for /api/search min_score parameter — API-level invariants.

Protects:
- API default invariant: /api/search without min_score uses 0.1 (not 0.0)
- Wiring invariant: explicit min_score param echoed in filters_applied response field
- Override invariant: min_score=0.0 is accepted and reflected in response
- Validation invariant: min_score outside [0.0, 1.0] returns 422 with correct shape
- Max-value invariant: min_score=1.0 is valid (le=1.0 constraint)

Uses real Embedder (local sentence-transformers model, no external API cost).
Storage-level filter correctness is covered by test_search_min_score_unit.py.
API tests focus on: default value, parameter wiring (filters_applied), and
validation boundaries — none of which require controlled relevance scores.

Builder risk challenge: builder labeled both changes LOW risk. Independent assessment:
- API default change is MEDIUM risk for existing callers relying on 0.0 behavior
  (confirmed: rust query returns 20→3 results, 17 sub-threshold items are dropped).
  Mitigated by explicit override path and CLI --min-score flag.
- 422 shape must include data:null to match global error contract (verified below).
- max-value 1.0 boundary is inclusive (>= not >); storage-level test covers this.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

API_KEY = "prismis-api-4d5e"  # Matches ~/.config/prismis/config.toml


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_search_client(test_db: Path) -> Generator[TestClient]:
    """TestClient with empty storage — sufficient for default/validation tests."""
    storage = Storage(test_db)

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()
    storage.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_api_default_min_score_is_point_one(empty_search_client: TestClient) -> None:
    """
    INVARIANT: /api/search with no min_score param applies 0.1 default.
    BREAKS: Every consumer (TUI, CLI, lore passthrough) gets noise results.

    Verification: response.data.filters_applied.min_score == 0.1
    The query returns 0 results (empty DB) but the filter value is still echoed.
    """
    response = empty_search_client.get(
        "/api/search?q=test",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200, f"Unexpected status: {response.text}"
    data = response.json()
    assert data["success"] is True

    filters = data["data"]["filters_applied"]
    assert filters["min_score"] == 0.1, (
        f"API default must be 0.1, got {filters['min_score']}. "
        "If this is 0.0, the default was reverted and every consumer gets noise."
    )


def test_api_explicit_min_score_echoed_in_response(
    empty_search_client: TestClient,
) -> None:
    """
    INVARIANT: Explicit min_score param is wired through to storage and echoed in response.
    BREAKS: The parameter is silently ignored and the default applies instead.

    Tests that the endpoint correctly receives and reflects the min_score parameter.
    """
    response = empty_search_client.get(
        "/api/search?q=test&min_score=0.5",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    data = response.json()
    filters = data["data"]["filters_applied"]
    assert filters["min_score"] == 0.5, (
        f"Explicit min_score=0.5 must be echoed in filters_applied, got {filters['min_score']}"
    )


def test_api_min_score_zero_override_accepted(
    empty_search_client: TestClient,
) -> None:
    """
    INVARIANT: min_score=0.0 is accepted and wired through (not silently dropped).
    BREAKS: Users who pass 0.0 to disable filtering get the 0.1 default instead.

    0.0 is falsy in Python — an incorrect `if min_score:` guard would drop it.
    The API uses `ge=0.0` so 0.0 is valid and must be passed through.
    """
    response = empty_search_client.get(
        "/api/search?q=test&min_score=0.0",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200
    data = response.json()
    filters = data["data"]["filters_applied"]
    assert filters["min_score"] == 0.0, (
        f"min_score=0.0 must be accepted and reflected, got {filters['min_score']}"
    )


def test_api_out_of_range_min_score_returns_422(
    empty_search_client: TestClient,
) -> None:
    """
    INVARIANT: min_score outside [0.0, 1.0] returns 422 with correct error shape.
    BREAKS: Out-of-range values reach storage and produce undefined behavior, OR the
            422 response deviates from the global error contract {success, message, data}.

    DISCOVERY APPLIED: task's 'Discovered During Implementation' note flagged
    "test-runner may want to verify 422 response shape". Verified shape is:
      {success: false, message: <validation detail>, data: null}

    FastAPI validates ge=0.0, le=1.0 before invoking the route — no embedder needed.
    """
    # Above 1.0
    response_high = empty_search_client.get(
        "/api/search?q=test&min_score=1.1",
        headers={"X-API-Key": API_KEY},
    )
    assert response_high.status_code == 422, (
        f"min_score=1.1 should return 422, got {response_high.status_code}"
    )
    body_high = response_high.json()
    assert body_high["success"] is False
    assert body_high["data"] is None, (
        f"422 error body must have data:null, got: {body_high['data']}"
    )
    assert "min_score" in body_high["message"].lower() or "1" in body_high["message"], (
        f"422 message should reference the invalid field, got: {body_high['message']}"
    )

    # Below 0.0
    response_low = empty_search_client.get(
        "/api/search?q=test&min_score=-0.1",
        headers={"X-API-Key": API_KEY},
    )
    assert response_low.status_code == 422, (
        f"min_score=-0.1 should return 422, got {response_low.status_code}"
    )
    body_low = response_low.json()
    assert body_low["success"] is False
    assert body_low["data"] is None, (
        f"422 error body must have data:null, got: {body_low['data']}"
    )


def test_api_max_value_min_score_is_valid(empty_search_client: TestClient) -> None:
    """
    INVARIANT: min_score=1.0 is accepted (le=1.0 is inclusive upper bound).
    BREAKS: max-boundary value causes 422, preventing users from filtering to near-perfect matches.
    """
    response = empty_search_client.get(
        "/api/search?q=test&min_score=1.0",
        headers={"X-API-Key": API_KEY},
    )

    assert response.status_code == 200, (
        f"min_score=1.0 is valid (le=1.0 inclusive), should not 422. "
        f"Got: {response.status_code}"
    )
    data = response.json()
    assert data["success"] is True
    filters = data["data"]["filters_applied"]
    assert filters["min_score"] == 1.0
