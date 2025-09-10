"""Data models for Prismis daemon."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Dict, Any


@dataclass
class ContentItem:
    """Represents a content item fetched from a source.

    Matches the content table schema for easy storage/retrieval.
    """

    source_id: str  # UUID of the source
    external_id: str  # Unique ID from the source for deduplication
    title: str
    url: str
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # Generate UUID
    content: Optional[str] = None  # Full article/transcript text
    summary: Optional[str] = None  # LLM-generated summary
    analysis: Optional[Dict[str, Any]] = None  # Full LLM analysis
    priority: Optional[str] = None  # 'high', 'medium', 'low'
    published_at: Optional[datetime] = None
    fetched_at: Optional[datetime] = None
    read: bool = False
    favorited: bool = False
    notes: Optional[str] = None

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "source_id": self.source_id,
            "external_id": self.external_id,
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "summary": self.summary,
            "analysis": self.analysis,
            "priority": self.priority,
            "published_at": self.published_at,
            "fetched_at": self.fetched_at,
            "read": self.read,
            "favorited": self.favorited,
            "notes": self.notes,
        }


@dataclass
class Source:
    """Represents a content source (RSS feed, Reddit sub, YouTube channel).

    Matches the sources table schema.
    """

    url: str
    type: str  # 'rss', 'reddit', 'youtube'
    name: Optional[str] = None
    active: bool = True
    error_count: int = 0
    last_error: Optional[str] = None
    last_fetched_at: Optional[datetime] = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))  # Generate UUID

    def to_dict(self) -> dict:
        """Convert to dictionary for database storage."""
        return {
            "id": self.id,
            "url": self.url,
            "type": self.type,
            "name": self.name,
            "active": self.active,
            "error_count": self.error_count,
            "last_error": self.last_error,
            "last_fetched_at": self.last_fetched_at,
        }
