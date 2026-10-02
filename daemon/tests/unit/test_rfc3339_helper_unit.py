"""Unit tests for the _rfc3339() helper and datetime serialization invariants.

INV-API-TS-1: every datetime response field MUST serialize as RFC3339.
INV-API-TS-3: boundaries.md MUST document the API <-> Consumers datetime contract.

Test considerations (from task 2.7 Test Considerations section):
- Tz-aware datetime -> _rfc3339 returns .isoformat() unchanged (no Z-append, no tzinfo
  branch: storage no longer holds naive datetimes, INV-STORAGE-TS-1)
- A non-UTC offset survives unchanged
- None input -> None returned
- SourceResponse.model_dump_json() uses _rfc3339 for last_fetched
- AudioBriefingResponse.model_dump_json() uses _rfc3339 for generated_at
- boundaries.md documents the RFC3339 contract (INV-API-TS-3, SC-28)
"""

import inspect
import json
import re
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path


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
# T1: _rfc3339() is a pass-through for tz-aware values, whatever their offset
# ---------------------------------------------------------------------------


def test_rfc3339_passes_non_utc_offset_through_unchanged() -> None:
    """INV-API-TS-1: the offset the value carries is the offset on the wire."""
    plus_five = datetime(
        2026, 5, 5, 23, 14, 53, 680336, tzinfo=timezone(timedelta(hours=5))
    )

    result = _rfc3339(plus_five)

    assert result == "2026-05-05T23:14:53.680336+05:00"
    assert result == plus_five.isoformat()
    assert_rfc3339(result)


# ---------------------------------------------------------------------------
# T2: _rfc3339() tz-aware UTC datetime emits "+00:00" (no double offset)
# ---------------------------------------------------------------------------


def test_rfc3339_aware_utc_no_double_offset() -> None:
    """INV-API-TS-1: tz-aware UTC must emit +00:00 and NOT produce malformed +00:00Z."""
    aware = datetime(2026, 5, 5, 23, 22, 34, 289113, tzinfo=UTC)
    assert aware.tzinfo is not None, "Precondition: input must be tz-aware"

    result = _rfc3339(aware)

    assert result == "2026-05-05T23:22:34.289113+00:00"
    assert result == aware.isoformat()
    assert_rfc3339(result)


# ---------------------------------------------------------------------------
# T2b: _rfc3339() has no Z-append and no tzinfo branch
# ---------------------------------------------------------------------------


def test_rfc3339_body_has_no_z_append_or_tzinfo_branch() -> None:
    """INV-STORAGE-TS-1 makes the naive case unrepresentable in storage, so the helper
    must be the bare pass-through; a branch here would re-guess what the data now states."""
    source = inspect.getsource(_rfc3339)

    assert '"Z"' not in source and "'Z'" not in source, source
    assert "tzinfo" not in source, source
    assert "isoformat()" in source


# ---------------------------------------------------------------------------
# T3: _rfc3339() None input returns None
# ---------------------------------------------------------------------------


def test_rfc3339_none_returns_none() -> None:
    """_rfc3339(None) must return None (nullable fields stay nullable on wire)."""
    assert _rfc3339(None) is None


# ---------------------------------------------------------------------------
# T4: SourceResponse.model_dump_json() serializes last_fetched as RFC3339
# ---------------------------------------------------------------------------


def test_source_response_aware_last_fetched_is_rfc3339() -> None:
    """INV-API-TS-1 (SourceResponse): tz-aware last_fetched emits valid RFC3339.

    Storage writes last_fetched_at with an explicit offset; SourceResponse's
    serializer passes it through _rfc3339 unchanged.
    """
    aware_ts = datetime(2026, 4, 30, 15, 49, 40, tzinfo=UTC)
    source = SourceResponse(
        id="test-uuid-1234",
        url="https://example.com/feed.xml",
        type="rss",
        name=None,
        active=True,
        last_fetched=aware_ts,
        error_count=0,
        last_error=None,
    )

    json_str = source.model_dump_json()

    data = json.loads(json_str)

    last_fetched = data["last_fetched"]
    assert isinstance(last_fetched, str), f"Expected string, got {type(last_fetched)}"
    assert_rfc3339(last_fetched)
    assert last_fetched == "2026-04-30T15:49:40+00:00"


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
# T5b: AudioBriefingResponse.model_dump(mode="json") — endpoint code path —
#      generates RFC3339 generated_at with explicit offset (SC-35b, INV-API-TS-1)
# ---------------------------------------------------------------------------


def test_audio_briefing_response_model_dump_json_mode_rfc3339() -> None:
    """SC-35b / INV-API-TS-1: model_dump(mode='json') emits RFC3339 generated_at.

    This exercises the EXACT call path used by the audio endpoint after task 2.10:
        AudioBriefingResponse(...).model_dump(mode="json")
    T5 covers model_dump_json() (str output). This covers model_dump(mode="json")
    (dict output, values already serialized) — the two paths share the same
    @field_serializer but test both consumer forms.
    """
    aware_ts = datetime(2026, 5, 11, 20, 25, 0, 373657, tzinfo=UTC)
    briefing = AudioBriefingResponse(
        file_path="/var/prismis/audio/briefing-2026-05-11.mp3",
        filename="briefing-2026-05-11.mp3",
        duration_estimate="2-5 minutes",
        generated_at=aware_ts,
        provider="openai",
        high_priority_count=0,
    )

    data = briefing.model_dump(mode="json")

    generated_at = data["generated_at"]
    assert isinstance(generated_at, str), (
        f"model_dump(mode='json') generated_at must be str, got {type(generated_at)}"
    )
    assert_rfc3339(generated_at)
    assert not generated_at.endswith("+00:00Z"), (
        f"Double-offset bug on model_dump(mode='json') path: {generated_at!r}"
    )


# ---------------------------------------------------------------------------
# T5c: AudioBriefingResponse.model_dump(mode="json") field set — SC-36
#      envelope data contains exactly 6 expected fields, no extra, no missing
# ---------------------------------------------------------------------------


def test_audio_briefing_response_model_dump_field_set() -> None:
    """SC-36: model_dump(mode='json') produces exactly the 6 expected data fields.

    INV-API-TS-4 requires structural enforcement via the model. This test verifies
    the serialized dict shape matches the contract so no field is silently dropped
    or added when the endpoint returns data.
    """
    aware_ts = datetime(2026, 5, 11, 20, 25, 0, tzinfo=UTC)
    briefing = AudioBriefingResponse(
        file_path="/var/prismis/audio/briefing.mp3",
        filename="briefing.mp3",
        duration_estimate="2-5 minutes",
        generated_at=aware_ts,
        provider="openai",
        high_priority_count=3,
    )

    data = briefing.model_dump(mode="json")

    expected_keys = {
        "file_path",
        "filename",
        "duration_estimate",
        "generated_at",
        "provider",
        "high_priority_count",
    }
    assert set(data.keys()) == expected_keys, (
        f"SC-36 field set mismatch. Expected {sorted(expected_keys)}, "
        f"got {sorted(data.keys())}"
    )


# ---------------------------------------------------------------------------
# T5d: AudioBriefingResponse.model_dump(mode="json") with duration_estimate=None
#      Tester-discovered: builder labeled this LOW and noted "always set" in the
#      endpoint, but the model declares Optional — a future omission would produce
#      null on wire. Test confirms null is present-but-null (not a missing key),
#      so SC-36 field-set invariant holds even for the null case.
# ---------------------------------------------------------------------------


def test_audio_briefing_response_model_dump_none_duration_estimate() -> None:
    """Tester-discovered: duration_estimate=None serializes as null (key present).

    The task file labels this LOW risk ("always set" in the endpoint). But
    AudioBriefingResponse.duration_estimate is Optional (str | None = Field(None)).
    If a future change omits duration_estimate, model_dump(mode='json') emits
    'duration_estimate': null — the key is present with a null value, not absent.
    This confirms SC-36 (all 6 keys present) holds even for the null path, and
    that the null value reaches the consumer as JSON null, not a missing field.
    """
    aware_ts = datetime(2026, 5, 11, 20, 25, 0, tzinfo=UTC)
    briefing = AudioBriefingResponse(
        file_path="/var/prismis/audio/briefing.mp3",
        filename="briefing.mp3",
        duration_estimate=None,
        generated_at=aware_ts,
        provider="openai",
        high_priority_count=3,
    )

    data = briefing.model_dump(mode="json")

    # Key must be present (SC-36), value must be null (not missing)
    assert "duration_estimate" in data, (
        "duration_estimate key must be present in model_dump output even when None "
        "(SC-36: all 6 fields must be present)"
    )
    assert data["duration_estimate"] is None, (
        f"duration_estimate=None must serialize as null, got: {data['duration_estimate']!r}"
    )
    # generated_at must still be RFC3339 (INV-API-TS-1 holds regardless of other fields)
    assert_rfc3339(data["generated_at"])


# ---------------------------------------------------------------------------
# T6: storage raw-dict path returns RFC3339 for rows storage writes
#
# Storage writes every datetime with an explicit offset (utc_now_iso, INV-STORAGE-TS-1)
# and init_db converts legacy naive rows, so the raw dict get_content_by_priority()
# returns is already RFC3339 before any API serializer touches it.
# ---------------------------------------------------------------------------


def test_fetched_at_in_raw_dict_path_is_rfc3339(test_db) -> None:
    """INV-API-TS-1: a row storage defaults fetched_at for reads back as RFC3339.

    The item carries no fetched_at, so storage supplies it; the value returned by
    the raw-dict path (the one /api/entries starts from) must already match RFC3339.
    """
    from prismis_daemon.models import ContentItem
    from prismis_daemon.storage import Storage

    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")
    item = ContentItem(
        source_id=src_id,
        external_id="defect-proof-001",
        title="Defect Proof Article",
        url="https://example.com/defect-proof",
        priority="high",
    )
    storage.add_content(item)

    results = storage.get_content_by_priority("high", limit=10)
    assert len(results) == 1, f"Expected 1 result, got {len(results)}"

    fetched_at_wire = results[0]["fetched_at"]
    assert RFC3339_RE.match(str(fetched_at_wire)), (
        f"fetched_at in the storage raw-dict path is not RFC3339: {fetched_at_wire!r}"
    )


# ---------------------------------------------------------------------------
# T7 (structural / INV-API-TS-3 / SC-28): boundaries.md documents the contract
# ---------------------------------------------------------------------------


def test_boundaries_md_documents_rfc3339_contract() -> None:
    """INV-API-TS-3: boundaries.md must document the API <-> Consumers datetime contract.

    This is a structural file-content test — no runtime behavior, pure doc invariant.

    Reads the in-repo copy. A test pointed outside the clone can only ever run on one
    machine, and it was skipping everywhere else — including CI — so the invariant it
    names was unguarded exactly where the gate runs.
    """
    boundaries_path = (
        Path(__file__).parents[3] / "docs" / "architecture" / "boundaries.md"
    ).resolve()
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
