"""Unit tests for ContentItemModel, ContentResponse, and @field_serializer migration.

Protects:
- INV-API-TS-1: every datetime field on ContentItemModel serializes through
  _rfc3339 via @field_serializer (naive → Z, tz-aware → explicit offset).
- INV-API-TS-4 structural: api_models.py contains zero json_encoders references;
  all datetime fields across all three response models have @field_serializer.
- SC-32: ContentResponse and ContentItemModel classes exist; field_serializer
  imported; zero legacy json_encoders in file.
- SC-33: boundaries.md documents INV-API-TS-4.

Task 2.8 gate: T6 xfail in test_rfc3339_helper_unit.py MUST remain in place —
it asserts on storage.get_content_by_priority() raw-dict path, which task 2.8
does NOT fix. Removing that decorator would cause an outright test failure.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from prismis_daemon.api_models import (
    ContentItemModel,
)

# RFC3339 pattern: T separator, explicit offset (Z or ±HH:MM), optional fractional seconds.
RFC3339_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def assert_rfc3339(value: str) -> None:
    """Assert that a string matches the RFC3339 wire format."""
    assert RFC3339_RE.match(value), (
        f"Expected RFC3339 format (T separator + explicit offset), got: {value!r}"
    )


def _make_minimal_item(**overrides) -> dict:
    """Return a minimal dict matching ContentItemModel required fields."""
    base = {
        "id": "test-uuid-001",
        "title": "Test Article",
        "url": "https://example.com/article",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# T-A: ContentItemModel naive fetched_at → RFC3339 with Z suffix
# ---------------------------------------------------------------------------


def test_content_item_model_naive_fetched_at_is_rfc3339() -> None:
    """INV-API-TS-1: naive fetched_at emits RFC3339 with Z suffix via @field_serializer.

    Storage writes naive datetimes for fetched_at (datetime.utcnow() convention in
    fetchers). ContentItemModel's @field_serializer must normalize via _rfc3339:
    naive → "2026-05-05T23:14:53.680336Z" (Z suffix).
    """
    naive_fetched = datetime(2026, 5, 5, 23, 14, 53, 680336)
    assert naive_fetched.tzinfo is None, "Precondition: input must be naive"

    item = ContentItemModel(**_make_minimal_item(fetched_at=naive_fetched))

    wire = json.loads(item.model_dump_json())
    fetched_at_wire = wire["fetched_at"]

    assert isinstance(fetched_at_wire, str), (
        f"Expected string on wire, got {type(fetched_at_wire)}"
    )
    assert fetched_at_wire.endswith("Z"), (
        f"Naive fetched_at must have Z suffix on wire, got: {fetched_at_wire!r}"
    )
    assert_rfc3339(fetched_at_wire)


# ---------------------------------------------------------------------------
# T-B: ContentItemModel tz-aware published_at → RFC3339, no double offset
# ---------------------------------------------------------------------------


def test_content_item_model_aware_published_at_is_rfc3339() -> None:
    """INV-API-TS-1: tz-aware published_at emits RFC3339 without double offset.

    A tz-aware UTC datetime must emit "+00:00" (not "+00:00Z" — the pre-2.7
    double-offset bug). _rfc3339()'s branch: tz-aware → .isoformat() directly.
    """
    aware_published = datetime(2026, 5, 5, 23, 22, 34, 289113, tzinfo=UTC)
    assert aware_published.tzinfo is not None, "Precondition: input must be tz-aware"

    item = ContentItemModel(**_make_minimal_item(published_at=aware_published))

    wire = json.loads(item.model_dump_json())
    published_at_wire = wire["published_at"]

    assert isinstance(published_at_wire, str)
    # Must NOT produce the malformed "+00:00Z" form
    assert not published_at_wire.endswith("+00:00Z"), (
        f"Double-offset bug: tz-aware isoformat() + 'Z' = {published_at_wire!r}"
    )
    assert_rfc3339(published_at_wire)


# ---------------------------------------------------------------------------
# T-C: ContentItemModel None datetime fields → None on wire (nullable edge case)
# ---------------------------------------------------------------------------


def test_content_item_model_none_datetimes_stay_none() -> None:
    """INV-API-TS-1 edge case: None datetime fields remain None on the wire.

    Both published_at and fetched_at are Optional[datetime] — when the storage
    row has no timestamp (existing NULL rows), the wire must emit null not 'None'.
    """
    item = ContentItemModel(
        **_make_minimal_item(published_at=None, fetched_at=None, archived_at=None)
    )

    wire = json.loads(item.model_dump_json())

    assert wire["published_at"] is None, (
        f"None published_at must be null on wire, got: {wire['published_at']!r}"
    )
    assert wire["fetched_at"] is None, (
        f"None fetched_at must be null on wire, got: {wire['fetched_at']!r}"
    )
    assert wire["archived_at"] is None, (
        f"None archived_at must be null on wire, got: {wire['archived_at']!r}"
    )


# ---------------------------------------------------------------------------
# T-G: SC-32 structural — api_models.py has zero json_encoders, @field_serializer
#       on all 4 datetime fields (last_fetched, generated_at, published_at, fetched_at)
# ---------------------------------------------------------------------------


def test_api_models_zero_json_encoders_and_field_serializers_present() -> None:
    """SC-32 / INV-API-TS-4: api_models.py uses @field_serializer only; no json_encoders.

    json_encoders is a Pydantic V1 compat mechanism deprecated in V2, scheduled
    for removal in V3. When V3 ships, it silently stops applying _rfc3339 with
    no error. Task 2.8 migrated all three models to @field_serializer decorators.
    This structural test verifies the migration is complete and no regression
    re-introduces the deprecated mechanism.
    """
    api_models_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "api_models.py"
    )
    assert api_models_path.exists(), f"api_models.py not found at {api_models_path}"

    content = api_models_path.read_text(encoding="utf-8")

    # Zero json_encoders references (the deprecated V1-compat mechanism)
    json_encoders_count = content.count("json_encoders")
    assert json_encoders_count == 0, (
        f"api_models.py must have zero 'json_encoders' references (Pydantic V1 compat, "
        f"deprecated in V2, removed in V3). Found {json_encoders_count} occurrence(s). "
        "Task 2.8 must have migrated all models to @field_serializer."
    )

    # field_serializer is imported
    assert "field_serializer" in content, (
        "api_models.py must import 'field_serializer' from pydantic (V2 native mechanism)"
    )

    # ContentResponse and ContentItemModel classes exist
    assert "class ContentItemModel" in content, (
        "ContentItemModel class must exist in api_models.py"
    )
    assert "class ContentResponse" in content, (
        "ContentResponse class must exist in api_models.py"
    )

    # All four datetime fields have @field_serializer decorators.
    # Each required field name must appear inside a @field_serializer(...) call.
    required_fields = ["last_fetched", "generated_at", "published_at", "fetched_at"]
    for field in required_fields:
        field_pattern = re.compile(
            rf"@field_serializer\([^)]*[\'\"]{re.escape(field)}[\'\"]"
        )
        assert field_pattern.search(content), (
            f"No @field_serializer for '{field}' found in api_models.py. "
            f"INV-API-TS-4 requires all datetime fields covered by @field_serializer + _rfc3339."
        )

    # _rfc3339 is referenced as the serializer function
    assert "_rfc3339" in content, (
        "api_models.py must reference _rfc3339 as the datetime serializer function"
    )


# ---------------------------------------------------------------------------
# T-H: SC-33 — boundaries.md documents INV-API-TS-4
# ---------------------------------------------------------------------------


def test_boundaries_md_documents_inv_api_ts4() -> None:
    """SC-33 / INV-API-TS-4: boundaries.md must contain INV-API-TS-4 sub-clause.

    Task 2.8 extended boundaries.md's 'API <-> Consumers' entry with INV-API-TS-4:
    'Every API list/detail endpoint that returns content data MUST flow through a
    Pydantic response model.' This test verifies the contract is documented.
    """
    boundaries_path = Path(
        "/Users/rudy/obsidian/projects/prismis/architecture/boundaries.md"
    )
    assert boundaries_path.exists(), f"boundaries.md not found at {boundaries_path}"

    content = boundaries_path.read_text(encoding="utf-8")

    assert "INV-API-TS-4" in content, (
        "boundaries.md must contain INV-API-TS-4 sub-clause (task 2.8 extension). "
        "This invariant requires every content-returning endpoint to flow through "
        "a Pydantic response model — no raw-dict pass-through."
    )

    # The specific claim: Pydantic-routed enforcement
    assert "Pydantic" in content, (
        "boundaries.md INV-API-TS-4 entry must name Pydantic as the enforcement mechanism"
    )

    # ContentResponse named as the model
    assert "ContentResponse" in content, (
        "boundaries.md must name ContentResponse as the contract-enforcing model "
        "for /api/entries and /api/search"
    )


# ---------------------------------------------------------------------------
# DEFECT PROOF: AudioBriefingResponse not used by the audio endpoint (INV-API-TS-4)
#
# Discovered during independent api.py review: api.py's import list does NOT
# include AudioBriefingResponse (grep confirmed: it is absent from the
# `from .api_models import (...)` block). The audio briefing endpoint
# (generate_audio_briefing) returns a raw dict literal — it never constructs
# AudioBriefingResponse. INV-API-TS-4 requires every endpoint that returns
# content data to flow through a Pydantic response model.
#
# This test MUST FAIL today (proving the structural gap) and MUST PASS after
# api.py imports and uses AudioBriefingResponse in the audio endpoint.
# It is a structural (static-analysis) test: no API call needed.
# ---------------------------------------------------------------------------


def test_audio_briefing_endpoint_uses_pydantic_model() -> None:
    """DEFECT PROOF (INV-API-TS-4 gap): AudioBriefingResponse not imported/used in api.py.

    AudioBriefingResponse exists in api_models.py and was migrated to @field_serializer
    in task 2.8. But api.py never imports it — the audio endpoint returns a raw dict
    literal with 'generated_at': datetime.now(UTC).isoformat().

    Wire is RFC3339-correct by coincidence (datetime.now(UTC).isoformat() → +00:00),
    not by contract. INV-API-TS-4 requires structural enforcement.

    This test FAILS today and PASSES after the fix.
    """
    api_py_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "api.py"
    )
    assert api_py_path.exists(), f"api.py not found at {api_py_path}"

    content = api_py_path.read_text(encoding="utf-8")

    # AudioBriefingResponse must appear in the import block from api_models
    assert "AudioBriefingResponse" in content, (
        "DEFECT (INV-API-TS-4): AudioBriefingResponse is not imported in api.py. "
        "The audio endpoint bypasses Pydantic — generated_at on the wire is "
        "RFC3339-correct by accident, not by contract. "
        "Fix: add AudioBriefingResponse to the api_models import and route "
        "generate_audio_briefing() through it."
    )
