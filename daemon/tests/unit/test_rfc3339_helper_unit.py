"""Unit tests for the _rfc3339() helper and datetime serialization invariants.

INV-API-TS-1: every datetime response field MUST serialize as RFC3339.
INV-API-TS-3: boundaries.md MUST document the API <-> Consumers datetime contract.

Test considerations (from task 2.7 Test Considerations section):
- Naive datetime -> _rfc3339 appends "Z" (no double-offset)
- Tz-aware UTC datetime -> _rfc3339 uses .isoformat() directly (e.g., "+00:00")
- None input -> None returned
- SourceResponse.model_dump_json() uses _rfc3339 for last_fetched
- AudioBriefingResponse.model_dump_json() uses _rfc3339 for generated_at
- boundaries.md documents the RFC3339 contract (INV-API-TS-3, SC-28)
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest

# _rfc3339 and response models are pure Pydantic — no I/O, no DB, no config needed.
from prismis_daemon.api_models import (
    AudioBriefingResponse,
    SourceResponse,
    _rfc3339,
)

# RFC3339 pattern: T separator, explicit offset (Z or ±HH:MM), optional fractional seconds.
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def assert_rfc3339(value: str) -> None:
    """Assert that a string value matches the RFC3339 wire format."""
    assert RFC3339_RE.match(value), (
        f"Expected RFC3339 format, got: {value!r}\n"
        "RFC3339 requires T separator and explicit offset (Z or ±HH:MM)."
    )


# ---------------------------------------------------------------------------
# T1: _rfc3339() naive datetime appends "Z"
# ---------------------------------------------------------------------------


def test_rfc3339_naive_appends_z() -> None:
    """INV-API-TS-1: naive datetime must become valid RFC3339 with Z suffix."""
    naive = datetime(2026, 5, 5, 23, 14, 53, 680336)
    assert naive.tzinfo is None, "Precondition: input must be naive"

    result = _rfc3339(naive)

    assert result is not None
    assert result.endswith("Z"), (
        f"Expected Z suffix for naive datetime, got: {result!r}"
    )
    assert_rfc3339(result)


# ---------------------------------------------------------------------------
# T2: _rfc3339() tz-aware UTC datetime emits "+00:00" (no double offset)
# ---------------------------------------------------------------------------


def test_rfc3339_aware_utc_no_double_offset() -> None:
    """INV-API-TS-1: tz-aware UTC must emit +00:00 and NOT produce malformed +00:00Z."""
    aware = datetime(2026, 5, 5, 23, 22, 34, 289113, tzinfo=UTC)
    assert aware.tzinfo is not None, "Precondition: input must be tz-aware"

    result = _rfc3339(aware)

    assert result is not None
    # Must NOT produce the old malformed "+00:00Z" bug (pre-task-2.7)
    assert not result.endswith("+00:00Z"), (
        f"Double-offset bug: tz-aware isoformat() + 'Z' produces malformed string: {result!r}"
    )
    assert_rfc3339(result)


# ---------------------------------------------------------------------------
# T3: _rfc3339() None input returns None
# ---------------------------------------------------------------------------


def test_rfc3339_none_returns_none() -> None:
    """_rfc3339(None) must return None (nullable fields stay nullable on wire)."""
    assert _rfc3339(None) is None


# ---------------------------------------------------------------------------
# T4: SourceResponse.model_dump_json() serializes last_fetched as RFC3339
# ---------------------------------------------------------------------------


def test_source_response_naive_last_fetched_is_rfc3339() -> None:
    """INV-API-TS-1 (SourceResponse): naive last_fetched emits valid RFC3339.

    Storage CURRENT_TIMESTAMP writes produce naive datetimes for last_fetched.
    SourceResponse's json_encoders must normalize them to RFC3339 via _rfc3339.
    """
    naive_ts = datetime(2026, 4, 30, 15, 49, 40)
    source = SourceResponse(
        id="test-uuid-1234",
        url="https://example.com/feed.xml",
        type="rss",
        active=True,
        last_fetched=naive_ts,
    )

    json_str = source.model_dump_json()

    data = json.loads(json_str)

    last_fetched = data["last_fetched"]
    assert isinstance(last_fetched, str), f"Expected string, got {type(last_fetched)}"
    assert_rfc3339(last_fetched)
    # Naive input must have Z suffix
    assert last_fetched.endswith("Z"), (
        f"Naive last_fetched must end with Z, got: {last_fetched!r}"
    )


# ---------------------------------------------------------------------------
# T5: AudioBriefingResponse.model_dump_json() serializes generated_at as RFC3339
# ---------------------------------------------------------------------------


def test_audio_briefing_response_tz_aware_generated_at_is_rfc3339() -> None:
    """INV-API-TS-1 (AudioBriefingResponse): tz-aware generated_at emits valid RFC3339.

    api.py:1333 now uses datetime.now(UTC) — the resulting tz-aware datetime
    must flow through _rfc3339 and arrive on the wire as valid RFC3339.
    """

    aware_ts = datetime(2026, 5, 5, 23, 22, 34, 289113, tzinfo=UTC)
    briefing = AudioBriefingResponse(
        file_path="/var/prismis/audio/briefing.mp3",
        filename="briefing.mp3",
        duration_estimate="2-5 minutes",
        generated_at=aware_ts,
        provider="openai",
        high_priority_count=3,
    )

    json_str = briefing.model_dump_json()
    data = json.loads(json_str)

    generated_at = data["generated_at"]
    assert isinstance(generated_at, str), f"Expected string, got {type(generated_at)}"
    assert_rfc3339(generated_at)
    # Must NOT be the malformed pre-fix form "+00:00Z"
    assert not generated_at.endswith("+00:00Z"), f"Double-offset bug: {generated_at!r}"


# ---------------------------------------------------------------------------
# T6 (defect proof): storage raw-dict path leaks naive fetched_at to wire
#
# This test PROVES the production defect that task 2.8 must fix.
# INV-API-TS-1 requires every datetime on the wire be RFC3339.
# /api/entries returns content_items as raw dicts from storage — no Pydantic
# encoder fires. When fetched_at was written as a naive datetime (e.g., from
# fetchers using datetime.utcnow()), storage.get_content_by_priority() returns
# the raw isoformat() string with no timezone offset.
#
# Per test-runner role: write a failing test proving the bug, report to
# orchestrator. This test must FAIL until task 2.8 routes /api/entries through
# a Pydantic ContentResponse model (or equivalent normalization).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason="task 2.8 SC-29 — passes when /api/entries routes through ContentResponse with _rfc3339 encoder; remove this decorator at that point",
)
def test_fetched_at_in_raw_dict_path_is_not_rfc3339(test_db) -> None:
    """DEFECT PROOF (task 2.8 gate): naive fetched_at leaks through storage raw-dict path.

    When fetchers call datetime.utcnow() (deprecated, naive), storage writes
    a tz-less ISO string. get_content_by_priority() returns that string
    unchanged in the dict — no RFC3339 normalization fires because the
    /api/entries endpoint bypasses Pydantic response models entirely.

    This test MUST FAIL today and MUST PASS after task 2.8 ships a fix
    (either ContentResponse Pydantic model or wire-time normalization).

    INV-API-TS-1: every datetime on the wire MUST be RFC3339.
    """
    from datetime import datetime  # naive — no UTC, simulating datetime.utcnow()

    from prismis_daemon.models import ContentItem
    from prismis_daemon.storage import Storage

    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Seed with a naive fetched_at — this is what fetchers produce via utcnow()
    naive_fetched = datetime(2026, 5, 5, 23, 14, 53, 680336)  # no tzinfo
    item = ContentItem(
        source_id=src_id,
        external_id="defect-proof-001",
        title="Defect Proof Article",
        url="https://example.com/defect-proof",
        priority="high",
        fetched_at=naive_fetched,
    )
    storage.add_content(item)

    # Retrieve via the same path /api/entries uses: get_content_by_priority()
    results = storage.get_content_by_priority("high", limit=10)
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"

    fetched_at_wire = results[0]["fetched_at"]

    # This assertion FAILS today (proving the defect):
    # The raw dict returns "2026-05-05T23:14:53.680336" — no offset, not RFC3339.
    # It will PASS after task 2.8 adds a ContentResponse model or normalization.
    assert RFC3339_RE.match(str(fetched_at_wire)), (
        f"DEFECT (task 2.8): fetched_at in storage raw-dict path is not RFC3339. "
        f"Got: {fetched_at_wire!r}. "
        "This value reaches /api/entries wire unchanged — TUI parser fails on it. "
        "Fix: route /api/entries through ContentResponse Pydantic model with _rfc3339 encoder."
    )


# ---------------------------------------------------------------------------
# T7 (structural / INV-API-TS-3 / SC-28): boundaries.md documents the contract
# ---------------------------------------------------------------------------


def test_boundaries_md_documents_rfc3339_contract() -> None:
    """INV-API-TS-3: boundaries.md must document the API <-> Consumers datetime contract.

    This is a structural file-content test — no runtime behavior, pure doc invariant.
    """
    boundaries_path = Path(
        "/Users/rudy/obsidian/projects/prismis/architecture/boundaries.md"
    )
    assert boundaries_path.exists(), f"boundaries.md not found at {boundaries_path}"

    content = boundaries_path.read_text(encoding="utf-8")

    assert "RFC3339" in content, (
        "boundaries.md must mention RFC3339 (INV-API-TS-3 / SC-28)"
    )
    # The specific entry added by task 2.7
    assert "API" in content and "Consumers" in content, (
        "boundaries.md must have an API <-> Consumers section (INV-API-TS-3)"
    )
    assert "datetime" in content.lower(), (
        "boundaries.md must reference datetime contract (INV-API-TS-3)"
    )
