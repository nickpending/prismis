"""Unit tests for SourceRequest.validate_url — task 2.12 invariants.

Protects:
- INV-VAL-1 (behavioral): validate_url rejects empty/whitespace-only URLs and
  strips leading/trailing whitespace from valid URLs. Wire behavior is unchanged
  from the @validator form; @field_validator(mode='before') preserves semantics.
- INV-VAL-2 (structural): api_models.py contains zero @validator references.
  @validator is a Pydantic V1 deprecated mechanism scheduled for removal in V3.
  Task 2.12 migrated validate_url to @field_validator(mode='before'); if @validator
  ever reappears, URL validation silently becomes a no-op on Pydantic V3 upgrade.

No external dependencies. No mocks. Direct SourceRequest instantiation.
"""

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from prismis_daemon.api_models import SourceRequest

# ---------------------------------------------------------------------------
# T-A: Empty URL rejected (INV-VAL-1 happy-path boundary)
# ---------------------------------------------------------------------------


def test_source_request_empty_url_rejected() -> None:
    """INV-VAL-1: empty string URL raises ValidationError.

    validate_url strips then checks non-empty. An empty string after strip
    must raise ValueError("URL cannot be empty"), surfaced as ValidationError.
    """
    with pytest.raises(ValidationError) as exc_info:
        SourceRequest(url="", type="rss")

    error_str = str(exc_info.value)
    assert "URL cannot be empty" in error_str, (
        f"ValidationError must explain empty URL; got: {error_str!r}"
    )


# ---------------------------------------------------------------------------
# T-B: Whitespace-only URL rejected (INV-VAL-1 edge case)
# ---------------------------------------------------------------------------


def test_source_request_whitespace_only_url_rejected() -> None:
    """INV-VAL-1: whitespace-only URL raises ValidationError after strip.

    validate_url strips input first; a string of spaces becomes ''.
    Must raise the same ValidationError as empty string.
    """
    with pytest.raises(ValidationError) as exc_info:
        SourceRequest(url="   ", type="rss")

    error_str = str(exc_info.value)
    assert "URL cannot be empty" in error_str, (
        f"ValidationError must explain empty URL after strip; got: {error_str!r}"
    )


# ---------------------------------------------------------------------------
# T-C: Leading/trailing whitespace stripped from valid URL (INV-VAL-1 happy path)
# ---------------------------------------------------------------------------


def test_source_request_url_whitespace_stripped() -> None:
    """INV-VAL-1: leading and trailing whitespace stripped from valid URL.

    validate_url's v.strip() must normalize URLs before returning them.
    The stored url must be the stripped form, not the raw padded input.
    """
    padded = "  https://example.com/feed.xml  "
    request = SourceRequest(url=padded, type="rss")

    assert request.url == "https://example.com/feed.xml", (
        f"URL must be stripped of leading/trailing whitespace; got: {request.url!r}"
    )


# ---------------------------------------------------------------------------
# T-D: Structural — zero @validator references in api_models.py (INV-VAL-2)
# ---------------------------------------------------------------------------


def test_api_models_zero_v1_validator_decorator() -> None:
    """INV-VAL-2 / SC-40b: api_models.py contains zero @validator references.

    @validator is a Pydantic V1 compat mechanism deprecated since V2.0, scheduled
    for removal in V3. When V3 ships and is pulled in via routine dependency update,
    @validator silently stops applying — URL validation becomes a no-op, allowing
    empty and whitespace-only URLs into the database.

    Task 2.12 migrated validate_url to @field_validator(mode='before'). This
    structural test guards against regression: if @validator reappears in
    api_models.py, this test fails immediately — before Pydantic V3 ships.
    """
    api_models_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "api_models.py"
    )
    assert api_models_path.exists(), f"api_models.py not found at {api_models_path}"

    content = api_models_path.read_text(encoding="utf-8")

    # Zero @validator( occurrences (the deprecated V1 decorator form)
    validator_count = len(re.findall(r"@validator\(", content))
    assert validator_count == 0, (
        f"api_models.py must have zero '@validator(' references (Pydantic V1 deprecated, "
        f"removed in V3). Found {validator_count} occurrence(s). "
        "Task 2.12 migrated SourceRequest.validate_url to @field_validator(mode='before'). "
        "Restore the @field_validator form."
    )

    # field_validator is imported (V2 native mechanism present)
    assert "field_validator" in content, (
        "api_models.py must import 'field_validator' from pydantic (V2 native mechanism). "
        "Task 2.12 added this import to replace the deprecated @validator decorator."
    )

    # @field_validator is used with mode='before' for the url field
    field_validator_pattern = re.compile(
        r"@field_validator\([^)]*['\"]url['\"][^)]*mode=['\"]before['\"]"
    )
    assert field_validator_pattern.search(content), (
        "api_models.py must have @field_validator('url', mode='before') on SourceRequest. "
        "mode='before' is required to replicate @validator's pre-coercion semantics for "
        "the url string field. Without mode='before', the field may be coerced to None "
        "before validation runs."
    )
