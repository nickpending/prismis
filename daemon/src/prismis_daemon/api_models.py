"""Pydantic models for REST API requests and responses."""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, validator


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

    model_config = {
        "json_encoders": {datetime: lambda v: v.isoformat() + "Z" if v else None}
    }


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
        None, description="User feedback: 'up' for useful, 'down' for not useful, null to clear"
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
