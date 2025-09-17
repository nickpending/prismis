"""Report generation for Prismis content."""

from datetime import datetime, timezone
from typing import List
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
        source_counts = {}
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
        items = self.storage.get_content_since(hours=hours)

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

    def format_as_markdown(self, report: DailyReport) -> str:
        """Format a report as markdown.

        Args:
            report: DailyReport to format

        Returns:
            Markdown-formatted string
        """
        lines = []

        # Title with date
        date_str = report.generated_at.strftime("%B %d, %Y")
        lines.append(f"# Daily Intelligence Brief - {date_str}")
        lines.append("")

        # High priority section
        if report.high_priority:
            lines.append(
                f"## 🔴 High Priority Developments ({len(report.high_priority)})"
            )
            lines.append("")

            for item in report.high_priority:
                lines.append(f"### {item.title}")
                lines.append(f"*{item.source_name} • {item.time_ago()}*  ")
                lines.append(f"[Read More]({item.url})")
                lines.append("")
                if item.summary:
                    lines.append(item.summary)
                lines.append("")

        # Medium priority section
        if report.medium_priority:
            lines.append(
                f"## 🟡 Medium Priority Updates ({len(report.medium_priority)})"
            )
            lines.append("")

            for item in report.medium_priority[:10]:  # Show up to 10 items
                lines.append(f"### {item.title}")
                lines.append(f"*{item.source_name} • {item.time_ago()}*  ")
                lines.append(f"[Read More]({item.url})")
                lines.append("")
                if item.summary:
                    lines.append(item.summary)  # Show full summary
                lines.append("")

            if len(report.medium_priority) > 10:
                lines.append(f"*... and {len(report.medium_priority) - 10} more items*")
            lines.append("")

        # Low priority section
        if report.low_priority:
            lines.append(f"## 🔵 Low Priority FYI ({len(report.low_priority)})")
            lines.append("")

            # More detailed for low priority items
            for item in report.low_priority[:15]:  # Show up to 15 items
                lines.append(f"**{item.title}**  ")
                lines.append(
                    f"*{item.source_name} • {item.time_ago()} • [Link]({item.url})*  "
                )
                if item.summary and len(item.summary) > 0:
                    # Show first 150 chars of summary for low priority
                    summary_text = (
                        item.summary[:150] + "..."
                        if len(item.summary) > 150
                        else item.summary
                    )
                    lines.append(f"{summary_text}  ")
                lines.append("")

            if len(report.low_priority) > 15:
                lines.append(f"*... and {len(report.low_priority) - 15} more items*")
            lines.append("")

        # Summary section
        lines.append("## 📊 Summary")
        lines.append(
            f"In the last {report.period_hours} hours: {report.total_items} items analyzed, "
            f"{len(report.high_priority)} high priority, "
            f"{len(report.medium_priority)} medium, "
            f"{len(report.low_priority)} low"
        )

        if report.top_sources:
            sources_str = ", ".join(
                [f"{name} ({count})" for name, count in report.top_sources]
            )
            lines.append(f"Top sources: {sources_str}")

        if report.key_themes:
            themes_str = ", ".join(report.key_themes)
            lines.append(f"Key themes: {themes_str}")

        return "\n".join(lines)
