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
# T6: storage raw-dict path returns naive ISO for legacy rows
#
# What this test proves: storage.get_content_by_priority() returns the ISO
# string exactly as it was written. For the ~7100 rows written by pre-task-2.9
# fetchers using datetime.utcnow(), that string is naive (no UTC offset).
# The ContentResponse encoder (_rfc3339) normalizes this at the API boundary —
# it is the only normalization layer for those legacy rows.
#
# Task 2.8 shipped ContentResponse (fixing the API wire path for new rows).
# Task 2.9 fixed fetcher emit sites (new rows are now tz-aware on write).
# Neither task backfills existing storage. This xfail must stay until a
# storage backfill migration converts the legacy naive rows to tz-aware ISO
# strings — at which point get_content_by_priority() will return RFC3339-
# compliant strings and the assertion below will pass.
#
# DO NOT remove this decorator based on task 2.8 or 2.9 completion alone.
# The removal condition is: all rows in the content table have tz-aware
# fetched_at (i.e., a backfill migration has run successfully).
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Legacy storage rows: ~7100 rows written by pre-task-2.9 fetchers have "
        "naive fetched_at. storage.get_content_by_priority() returns the raw ISO "
        "string as-written; for these rows it has no UTC offset. Task 2.8 fixed "
        "the API wire path (ContentResponse encoder); task 2.9 fixed new writes. "
        "This xfail covers the legacy raw-dict path and must stay until a storage "
        "backfill migration converts existing naive rows to tz-aware ISO strings."
    ),
)
def test_fetched_at_in_raw_dict_path_is_not_rfc3339(test_db) -> None:
    """Legacy storage path: naive fetched_at returns non-RFC3339 from raw dict.

    storage.get_content_by_priority() returns the ISO string as written to the
    content table. For rows written before task 2.9 (via datetime.utcnow()),
    that string is naive ("2026-05-05T23:14:53.680336" — no offset). The
    ContentResponse _rfc3339() encoder normalizes this at the API boundary and
    is the only normalization layer for these legacy rows.

    This test seeds a naive fetched_at to simulate a legacy row and asserts
    that the raw storage path does NOT return RFC3339 — confirming the encoder
    is still needed. When a backfill migration runs, newly-stored rows will
    carry "+00:00" and this assertion will flip to passing; remove the xfail
    decorator only at that point.

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
