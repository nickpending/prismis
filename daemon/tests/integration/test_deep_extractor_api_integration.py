"""Integration tests for POST /api/entries/{id}/extract endpoint -- task 1.2.

Invariants protected:
- INV-004: Second call when analysis.deep_extraction exists returns existing
  data without an LLM call (idempotency).
- SC-3: POST creates extraction and DB row is updated with deep_extraction.
- SC-4: Idempotency verified at the API integration level (duplicate of INV-004).

Mocking strategy:
- app.state.deep_extractor is replaced with a controlled stub.
- LLM (complete()) is NOT called in these tests -- the stub short-circuits it.
- auth.py calls Config.from_file() for the real API key from
  ~/.config/prismis/config.toml -- real key "prismis-api-4d5e" is used.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

_API_KEY = "prismis-api-4d5e"

# Patch targets -- allow-mock marker for claudex-guard scanner
_PATCH_CONFIG_FROM_FILE = (
    "prismis_daemon.auth.Config.from_file"  # claudex-guard: allow-mock
)


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> None:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _seed_entry(
    storage: Storage,
    analysis: dict | None = None,
    content: str = "Article body text for testing deep extraction.",
) -> str:
    """Insert a content row and return its ID."""
    source_id = storage.add_source(
        "https://example.com/rss", "rss", "Integration Test Feed"
    )
    item = ContentItem(
        source_id=source_id,
        external_id="deep-api-test-001",
        title="Test Deep Extraction Article",
        url="https://example.com/test-article",
        content=content,
        summary="Light summary of the article.",
        analysis=analysis,
        priority="high",
    )
    content_id = storage.add_content(item)
    return content_id


@pytest.fixture
def storage_with_entry(test_db: Path) -> tuple[Storage, str]:
    """Storage containing one entry WITHOUT deep_extraction."""
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis={"metrics": {"score": 80}})
    return storage, content_id


@pytest.fixture
def storage_with_extracted_entry(test_db: Path) -> tuple[Storage, str, dict]:
    """Storage containing one entry WITH deep_extraction already present."""
    existing_extraction = {
        "synthesis": "Counterintuitive finding: the obvious conclusion is wrong.",
        "quotables": ["Key verbatim quote from the article."],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-04-27T10:00:00+00:00",
    }
    analysis = {
        "metrics": {"score": 80},
        "deep_extraction": existing_extraction,
    }
    storage = Storage(test_db)
    content_id = _seed_entry(storage, analysis=analysis)
    return storage, content_id, existing_extraction


class _StubExtractor:
    """Controlled deep extractor stub -- returns a canned extraction dict.

    Records call count so tests can assert whether extract() was invoked.
    """

    def __init__(self, result: dict | None) -> None:
        self._result = result
        self.call_count = 0

    def extract(self, content: str, title: str = "", url: str = "") -> dict | None:
        self.call_count += 1
        return self._result


def _make_api_client(storage: Storage) -> Generator[TestClient]:
    """Create TestClient with storage dependency overridden."""

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    return client


# ---------------------------------------------------------------------------
# SC-3: POST creates extraction and DB row is updated
# ---------------------------------------------------------------------------


def test_sc3_post_creates_extraction_and_updates_db(
    storage_with_entry: tuple[Storage, str],
) -> None:
    """
    SC-3: POST /api/entries/{id}/extract on an entry with no deep_extraction
    returns HTTP 200 with the extraction dict, and the DB row is updated.

    BREAKS: On-demand extraction endpoint exists but forgets to call
    storage.update_analysis(), so the extraction is returned in the response
    but never persisted -- next GET shows no deep_extraction.
    """
    storage, content_id = storage_with_entry

    canned_extraction = {
        "synthesis": "**Counterintuitive:** the market shrank despite headline growth.",
        "quotables": ["Revenue grew 22% but profitability fell."],
        "model": "gpt-5-mini-2025-08-07",
        "extracted_at": "2026-04-27T12:00:00+00:00",
    }
    stub = _StubExtractor(result=canned_extraction)
    app.state.deep_extractor = stub

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        client = TestClient(app)

        response = client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["success"] is True
        assert "deep_extraction" in data["data"], (
            "Response must contain deep_extraction"
        )
        returned_extraction = data["data"]["deep_extraction"]
        assert returned_extraction["synthesis"] == canned_extraction["synthesis"]
        assert returned_extraction["model"] == canned_extraction["model"]

        # SC-3: Verify the DB row was actually updated
        stored = storage.get_content_by_id(content_id)
        assert stored is not None
        stored_analysis = stored.get("analysis") or {}
        assert "deep_extraction" in stored_analysis, (
            "SC-3: DB row must have deep_extraction after POST"
        )
        assert (
            stored_analysis["deep_extraction"]["synthesis"]
            == canned_extraction["synthesis"]
        )

        # Extractor was called exactly once
        assert stub.call_count == 1
    finally:
        app.dependency_overrides.clear()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# INV-004 / SC-4: Second POST returns existing data without LLM call
# ---------------------------------------------------------------------------


def test_inv004_sc4_idempotency_returns_existing_without_llm_call(
    storage_with_extracted_entry: tuple[Storage, str, dict],
) -> None:
    """
    INV-004 / SC-4: POST when analysis.deep_extraction already exists returns
    the existing extraction WITHOUT calling extractor.extract().

    BREAKS: Idempotency check is absent or fires after the LLM call --
    every repeated POST burns money and adds latency even though the
    extraction is already stored.
    """
    storage, content_id, existing_extraction = storage_with_extracted_entry

    # Stub that would record a call if extract() is invoked
    stub = _StubExtractor(result={"synthesis": "NEW synthesis -- should not appear"})
    app.state.deep_extractor = stub

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        client = TestClient(app)

        response = client.post(
            f"/api/entries/{content_id}/extract",
            headers={"X-API-Key": _API_KEY},
        )

        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert data["success"] is True

        # INV-004: existing extraction must be returned unchanged
        returned = data["data"]["deep_extraction"]
        assert returned["synthesis"] == existing_extraction["synthesis"], (
            "INV-004: must return existing synthesis, not re-run extraction"
        )
        assert returned["model"] == existing_extraction["model"]

        # INV-004: extractor must NOT have been called
        assert stub.call_count == 0, (
            f"INV-004: extractor.extract() must not be called when "
            f"deep_extraction already exists; was called {stub.call_count} times"
        )
    finally:
        app.dependency_overrides.clear()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# 404 for unknown entry ID
# ---------------------------------------------------------------------------


def test_extract_endpoint_returns_404_for_unknown_id(test_db: Path) -> None:
    """
    POST with an ID that doesn't exist returns 404.
    BREAKS: Calling update_analysis() on a non-existent ID silently succeeds
    (rowcount=0 is not checked); caller gets 200 with empty data.
    """
    storage = Storage(test_db)
    stub = _StubExtractor(result={"synthesis": "irrelevant"})
    app.state.deep_extractor = stub

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        client = TestClient(app)

        response = client.post(
            "/api/entries/00000000-0000-0000-0000-000000000000/extract",
            headers={"X-API-Key": _API_KEY},
        )

        assert response.status_code == 404, (
            f"Expected 404 for unknown ID, got {response.status_code}"
        )
        data = response.json()
        assert data["success"] is False
    finally:
        app.dependency_overrides.clear()
        app.state.deep_extractor = None


# ---------------------------------------------------------------------------
# 503 when extractor not configured
# ---------------------------------------------------------------------------


def test_extract_endpoint_returns_503_when_not_configured(test_db: Path) -> None:
    """
    POST when app.state.deep_extractor is None returns 503.
    BREAKS: Endpoint returns 500 (unhandled AttributeError) instead of the
    documented 503 "not configured" response; callers can't distinguish
    config-missing from server error.
    """
    storage = Storage(test_db)
    app.state.deep_extractor = None

    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        client = TestClient(app)

        response = client.post(
            "/api/entries/any-id/extract",
            headers={"X-API-Key": _API_KEY},
        )

        assert response.status_code == 503, (
            f"Expected 503 when extractor not configured, got {response.status_code}"
        )
        data = response.json()
        assert data["success"] is False
    finally:
        app.dependency_overrides.clear()
