"""Pydantic models for REST API requests and responses."""

from typing import Optional, Dict, Any, List, Literal
from pydantic import BaseModel, Field, validator
from datetime import datetime


class SourceRequest(BaseModel):
    """Request model for adding a content source."""

    url: str = Field(..., description="URL of the content source")
    type: Literal["rss", "reddit", "youtube", "file"] = Field(
        ..., description="Type of source"
    )
    name: Optional[str] = Field(None, description="Optional custom name for the source")

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
    data: Optional[Dict[str, Any]] = Field(None, description="Response data if any")


class SourceResponse(BaseModel):
    """Response model for a single source."""

    id: str = Field(..., description="Source UUID")
    url: str = Field(..., description="Source URL")
    type: str = Field(..., description="Source type")
    name: Optional[str] = Field(None, description="Source name")
    active: bool = Field(..., description="Whether source is active")
    last_fetched: Optional[datetime] = Field(None, description="Last fetch timestamp")
    error_count: int = Field(0, description="Number of consecutive errors")
    last_error: Optional[str] = Field(None, description="Last error message")


class SourceListResponse(BaseModel):
    """Response model for list of sources."""

    sources: List[SourceResponse] = Field(..., description="List of sources")
    total: int = Field(..., description="Total number of sources")


class ContentUpdateRequest(BaseModel):
    """Request model for updating content properties."""

    read: Optional[bool] = Field(None, description="Mark as read/unread")
    favorited: Optional[bool] = Field(None, description="Mark as favorite/unfavorite")


class AudioBriefingResponse(BaseModel):
    """Response model for audio briefing generation."""

    file_path: str = Field(..., description="Path to generated audio file")
    filename: str = Field(..., description="Filename of generated audio")
    duration_estimate: Optional[str] = Field(
        None, description="Estimated duration (e.g., '2-5 minutes')"
    )
    generated_at: datetime = Field(..., description="Generation timestamp")
    provider: str = Field(..., description="TTS provider used")
    high_priority_count: int = Field(..., description="Number of HIGH priority items")
