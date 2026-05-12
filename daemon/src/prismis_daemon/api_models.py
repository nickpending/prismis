"""Pydantic models for REST API requests and responses."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_serializer, validator


def _rfc3339(v: datetime | None) -> str | None:
    # Storage emits both naive UTC strings (CURRENT_TIMESTAMP rows) and tz-aware
    # UTC datetimes (datetime.now(UTC).isoformat() call sites). RFC3339 requires
    # an explicit offset; append "Z" only when the value is naive so tz-aware
    # values don't get a malformed double offset like "+00:00Z".
    if v is None:
        return None
    return v.isoformat() if v.tzinfo else v.isoformat() + "Z"


class SourceRequest(BaseModel):
    """Request model for adding a content source."""

    url: str = Field(..., description="URL of the content source")
    type: Literal["rss", "reddit", "youtube", "file"] = Field(
        ..., description="Type of source"
    )
    name: str | None = Field(None, description="Optional custom name for the source")

    @validator("url")
    def validate_url(cls, v: str) -> str:
        """Basic URL validation and cleanup."""
        v = v.strip()
        if not v:
            raise ValueError("URL cannot be empty")
        return v


class APIResponse(BaseModel):
    """Standard API response format."""

    success: bool = Field(..., description="Whether the operation succeeded")
    message: str = Field(..., description="Human-readable message")
    data: dict[str, Any] | None = Field(None, description="Response data if any")


class SourceResponse(BaseModel):
    """Response model for a single source."""

    id: str = Field(..., description="Source UUID")
    url: str = Field(..., description="Source URL")
    type: str = Field(..., description="Source type")
    name: str | None = Field(None, description="Source name")
    active: bool = Field(..., description="Whether source is active")
    last_fetched: datetime | None = Field(None, description="Last fetch timestamp")
    error_count: int = Field(0, description="Number of consecutive errors")
    last_error: str | None = Field(None, description="Last error message")

    @field_serializer("last_fetched")
    def _serialize_last_fetched(self, v: datetime | None) -> str | None:
        return _rfc3339(v)


class SourceListResponse(BaseModel):
    """Response model for list of sources."""

    sources: list[SourceResponse] = Field(..., description="List of sources")
    total: int = Field(..., description="Total number of sources")


class ContentUpdateRequest(BaseModel):
    """Request model for updating content properties."""

    read: bool | None = Field(None, description="Mark as read/unread")
    favorited: bool | None = Field(None, description="Mark as favorite/unfavorite")
    interesting_override: bool | None = Field(
        None, description="Flag for context analysis"
    )
    user_feedback: Literal["up", "down"] | None = Field(
        None,
        description="User feedback: 'up' for useful, 'down' for not useful, null to clear",
    )


class AudioBriefingResponse(BaseModel):
    """Response model for audio briefing generation."""

    file_path: str = Field(..., description="Path to generated audio file")
    filename: str = Field(..., description="Filename of generated audio")
    duration_estimate: str | None = Field(
        None, description="Estimated duration (e.g., '2-5 minutes')"
    )
    generated_at: datetime = Field(..., description="Generation timestamp")
    provider: str = Field(..., description="TTS provider used")
    high_priority_count: int = Field(..., description="Number of HIGH priority items")

    @field_serializer("generated_at")
    def _serialize_generated_at(self, v: datetime) -> str:
        return _rfc3339(v)


class ContentItemModel(BaseModel):
    """Per-item response model for content endpoints.

    INV-API-TS-1: every datetime field serializes through `_rfc3339` via
    `@field_serializer` decorators so consumers see RFC3339 with explicit
    timezone offsets (naive storage rows get a `Z` suffix; tz-aware values
    keep their `+HH:MM`).
    """

    id: str
    source_id: str | None = None
    source_name: str | None = None
    source_type: str | None = None
    external_id: str | None = None
    title: str
    url: str
    content: str | None = None
    summary: str | None = None
    analysis: dict[str, Any] | None = None
    priority: str | None = None
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    read: bool = False
    favorited: bool = False
    interesting_override: bool = False
    user_feedback: str | None = None
    notes: str | None = None
    archived_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    # Search-only fields (None for non-search paths)
    relevance_score: float | None = None
    # Deduplication fields (None when dedup not applied)
    duplicate_count: int | None = None
    duplicate_sources: list[str] | None = None

    @field_serializer("published_at", "fetched_at", "archived_at")
    def _serialize_published_fetched_archived(self, v: datetime | None) -> str | None:
        return _rfc3339(v)

    @field_serializer("created_at", "updated_at")
    def _serialize_created_updated(self, v: datetime | None) -> str | None:
        return _rfc3339(v)


class ContentResponseData(BaseModel):
    """Data envelope for content list responses."""

    items: list[ContentItemModel]
    total: int
    query: str | None = None  # Only set by /api/search
    filters_applied: dict[str, Any] = Field(default_factory=dict)


class ContentResponse(BaseModel):
    """Response model for /api/entries and /api/search.

    INV-API-TS-4: every API list/detail endpoint that returns content data
    flows through this model (or a sibling model that applies `_rfc3339`
    via `@field_serializer`). No raw-dict pass-through on response paths
    that emit datetime fields.
    """

    success: bool
    message: str
    data: ContentResponseData
