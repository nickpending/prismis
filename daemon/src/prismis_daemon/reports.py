"""Report generation for Prismis content."""

from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class ContentSummary:
    """Summary of a single content item for reports."""

    title: str
    source_name: str
    url: str
    summary: str
    published_at: datetime
    priority: str
    analysis: Optional[Dict[str, Any]] = None

    def time_ago(self) -> str:
        """Get human-readable time since publication."""
        # Use timezone-aware datetime
        now = datetime.now(timezone.utc)
        # Ensure published_at is timezone-aware
        if self.published_at.tzinfo is None:
            # Assume UTC if no timezone
            pub_time = self.published_at.replace(tzinfo=timezone.utc)
        else:
            pub_time = self.published_at
        delta = now - pub_time

        hours = int(delta.total_seconds() / 3600)
        if hours < 1:
            minutes = int(delta.total_seconds() / 60)
            return f"{minutes} minutes ago"
        elif hours < 24:
            return f"{hours} hour{'s' if hours != 1 else ''} ago"
        else:
            days = int(hours / 24)
            return f"{days} day{'s' if days != 1 else ''} ago"


@dataclass
class DailyReport:
    """Daily report containing prioritized content summaries."""

    generated_at: datetime
    period_hours: int
    high_priority: List[ContentSummary] = field(default_factory=list)
    medium_priority: List[ContentSummary] = field(default_factory=list)
    low_priority: List[ContentSummary] = field(default_factory=list)

    @property
    def total_items(self) -> int:
        """Total number of items in report."""
        return (
            len(self.high_priority) + len(self.medium_priority) + len(self.low_priority)
        )

    @property
    def top_sources(self) -> List[tuple[str, int]]:
        """Get top sources by item count."""
        source_counts: dict[str, int] = {}
        for item in self.high_priority + self.medium_priority + self.low_priority:
            source_counts[item.source_name] = source_counts.get(item.source_name, 0) + 1

        # Sort by count descending, take top 3
        from operator import itemgetter

        sorted_sources = sorted(source_counts.items(), key=itemgetter(1), reverse=True)
        return sorted_sources[:3]

    @property
    def key_themes(self) -> List[str]:
        """Extract key themes from high priority items."""
        # Simple keyword extraction from titles
        # In production, would use LLM or more sophisticated NLP
        themes = set()
        keywords = [
            "rust",
            "ai",
            "llm",
            "sqlite",
            "python",
            "javascript",
            "react",
            "database",
            "security",
            "performance",
        ]

        for item in self.high_priority:
            title_lower = item.title.lower()
            for keyword in keywords:
                if keyword in title_lower:
                    themes.add(keyword.upper())

        return list(themes)[:3]  # Top 3 themes

    @property
    def top_3_must_reads(self) -> List[ContentSummary]:
        """Get top 3 must-read items using interest-based ranking.

        Implements algorithm from task 3.1 design:
        - Primary ranking: matched_interests count (DESC)
        - Tiebreaker: published_at (DESC - newest first)
        - Fallback: Show honest count (don't pad with medium priority)

        Returns:
            List of 0-3 ContentSummary items, ranked by relevance
        """
        if not self.high_priority:
            return []

        # Enrich items with ranking metadata
        ranked_items: list[dict[str, Any]] = []
        for item in self.high_priority:
            # Extract matched_interests from analysis, handle missing/null
            matched_interests = []
            if item.analysis and isinstance(item.analysis, dict):
                matched_interests = item.analysis.get("matched_interests", [])
                if not isinstance(matched_interests, list):
                    matched_interests = []

            interest_count = len(matched_interests)

            # Handle null published_at (treat as epoch - oldest possible)
            published_at = item.published_at
            if published_at is None:
                published_at = datetime(1970, 1, 1, tzinfo=timezone.utc)

            ranked_items.append(
                {
                    "item": item,
                    "interest_count": interest_count,
                    "published_at": published_at,
                }
            )

        # Sort by interest_count DESC, then published_at DESC
        ranked_items.sort(
            key=lambda x: (x["interest_count"], x["published_at"]), reverse=True
        )

        # Take top 3 (or fewer if <3 available)
        top_3 = [x["item"] for x in ranked_items[:3]]

        return top_3


class ReportGenerator:
    """Generate reports from content data."""

    def __init__(self, storage):
        """Initialize with storage instance.

        Args:
            storage: Storage instance for database access
        """
        self.storage = storage

    def generate_daily_report(self, hours: int = 24) -> DailyReport:
        """Generate a daily report from recent content.

        Args:
            hours: Number of hours to look back (default 24)

        Returns:
            DailyReport with prioritized content
        """
        # Get content from last N hours
        since_dt = datetime.now(timezone.utc) - timedelta(hours=hours)
        items = self.storage.get_content_since(since=since_dt)

        # Group by priority
        high = []
        medium = []
        low = []

        for item in items:
            summary = ContentSummary(
                title=item["title"],
                source_name=item.get("source_name", "Unknown"),
                url=item["url"],
                summary=item.get("summary", ""),
                published_at=datetime.fromisoformat(item["published_at"]),
                priority=item["priority"],
                analysis=item.get("analysis"),
            )

            if item["priority"] == "high":
                high.append(summary)
            elif item["priority"] == "medium":
                medium.append(summary)
            elif item["priority"] == "low":
                low.append(summary)

        return DailyReport(
            generated_at=datetime.now(timezone.utc),
            period_hours=hours,
            high_priority=high,
            medium_priority=medium,
            low_priority=low,
        )
