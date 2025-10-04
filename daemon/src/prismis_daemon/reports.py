"""Report generation for Prismis content."""

from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from dataclasses import dataclass, field
import html


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
        ranked_items = []
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

    def format_as_html(self, report: DailyReport) -> str:
        """Format a report as HTML with inline CSS.

        Args:
            report: DailyReport to format

        Returns:
            HTML-formatted string with inline styles for email compatibility
        """
        date_str = report.generated_at.strftime("%B %d, %Y")

        # HTML escaping helper
        def esc(text: str) -> str:
            """Escape HTML special characters."""
            return html.escape(str(text)) if text else ""

        # Start building HTML
        html_parts = []

        # DOCTYPE and head with inline CSS
        html_parts.append(
            f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Daily Intelligence Brief - {esc(date_str)}</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        body {{
            font-family: 'JetBrains Mono', monospace;
            line-height: 1.6;
            color: #EEEEEE;
            max-width: 800px;
            margin: 0 auto;
            padding: 20px;
            background-color: #1a1a1a;
        }}
        .report-header {{
            background: linear-gradient(135deg, #00D9FF 0%, #9F4DFF 100%);
            color: white;
            padding: 16px 24px;
            border-radius: 6px;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            gap: 12px;
        }}
        .logo-icon {{
            width: 28px;
            height: 28px;
            flex-shrink: 0;
        }}
        .report-header-content {{
            flex: 1;
            display: flex;
            align-items: baseline;
            gap: 12px;
        }}
        .report-header h1 {{
            margin: 0;
            font-size: 18px;
            font-weight: 600;
            letter-spacing: 0.05em;
        }}
        .report-header-date {{
            font-size: 13px;
            opacity: 0.8;
        }}
        .section {{
            background: #222222;
            padding: 25px;
            border-radius: 8px;
            margin-bottom: 20px;
            border: 1px solid #333333;
        }}
        .section-title {{
            font-size: 14px;
            font-weight: 600;
            margin-bottom: 20px;
            padding-bottom: 10px;
            border-bottom: 1px solid #333333;
            color: #888888;
            text-transform: uppercase;
            letter-spacing: 0.1em;
        }}
        .must-read {{
            border-left: 4px solid #00D9FF;
        }}
        .must-read .section-title {{
            color: #00D9FF;
            border-bottom: 2px solid #00D9FF;
        }}
        .item {{
            padding: 15px;
            margin-bottom: 15px;
            border-radius: 6px;
            background: #2a2a2a;
            border: 1px solid #333333;
        }}
        .item-title {{
            font-size: 18px;
            font-weight: 600;
            margin-bottom: 8px;
            color: #EEEEEE;
        }}
        .item-title a {{
            color: #EEEEEE;
            text-decoration: none;
        }}
        .item-title a:hover {{
            color: #00D9FF;
        }}
        .item-meta {{
            font-size: 13px;
            color: #888888;
            margin-bottom: 10px;
        }}
        .item-summary {{
            font-size: 15px;
            color: #CCCCCC;
            line-height: 1.6;
        }}
        .priority-badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
            margin-right: 8px;
        }}
        .badge-high {{
            background-color: rgba(255, 0, 102, 0.2);
            color: #FF0066;
            border: 1px solid #FF0066;
        }}
        .badge-medium {{
            background-color: rgba(255, 136, 0, 0.2);
            color: #FF8800;
            border: 1px solid #FF8800;
        }}
        .badge-low {{
            background-color: rgba(102, 102, 102, 0.2);
            color: #666666;
            border: 1px solid #666666;
        }}
        .match-badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            background-color: rgba(230, 204, 255, 0.1);
            color: #E6CCFF;
            border: 1px solid #E6CCFF;
            margin-left: 8px;
        }}
        .summary-stats {{
            background: #2a2a2a;
            padding: 15px;
            border-radius: 6px;
            font-size: 14px;
            color: #CCCCCC;
            border: 1px solid #333333;
        }}
        .summary-stats strong {{
            color: #00D9FF;
        }}
        @media (max-width: 600px) {{
            body {{
                padding: 10px;
            }}
            .report-header {{
                padding: 20px;
            }}
            .section {{
                padding: 15px;
            }}
        }}
    </style>
</head>
<body>
    <div class="report-header">
        <svg class="logo-icon" viewBox="0 0 24 24" fill="none">
            <path d="M12 2L4 7v10l8 5 8-5V7l-8-5z" stroke="currentColor" stroke-width="2" fill="none"/>
            <path d="M12 2L8 12l4 10 4-10L12 2z" fill="currentColor" opacity="0.3"/>
            <path d="M12 2v20M4 7l8 5 8-5M4 17l8-5 8 5" stroke="currentColor" stroke-width="1" opacity="0.5"/>
        </svg>
        <div class="report-header-content">
            <h1>PRISMIS</h1>
            <span class="report-header-date">Daily Intelligence • {esc(date_str)}</span>
        </div>
    </div>"""
        )

        # Top 3 Must-Reads section
        top_3 = report.top_3_must_reads
        if top_3:
            html_parts.append('    <div class="section must-read">')
            count_text = (
                "Top 3 Must-Reads"
                if len(top_3) == 3
                else f"Top {len(top_3)} Must-Read{'s' if len(top_3) > 1 else ''}"
            )
            html_parts.append(
                f'        <h2 class="section-title">{esc(count_text)}</h2>'
            )

            for item in top_3:
                # Get matched interests count for badge
                matched_count = 0
                if item.analysis and isinstance(item.analysis, dict):
                    matched_interests = item.analysis.get("matched_interests", [])
                    if isinstance(matched_interests, list):
                        matched_count = len(matched_interests)

                html_parts.append('        <div class="item">')
                html_parts.append(
                    f'            <div class="item-title"><a href="{esc(item.url)}">{esc(item.title)}</a></div>'
                )
                html_parts.append(
                    f'            <div class="item-meta">'
                    f'<span class="priority-badge badge-high">HIGH</span>'
                    f"{esc(item.source_name)} • {esc(item.time_ago())}"
                )
                if matched_count > 0:
                    match_text = f"{matched_count} interest{'s' if matched_count > 1 else ''} matched"
                    html_parts.append(
                        f'<span class="match-badge">✨ {esc(match_text)}</span>'
                    )
                html_parts.append("</div>")
                if item.summary:
                    html_parts.append(
                        f'            <div class="item-summary">{esc(item.summary)}</div>'
                    )
                html_parts.append("        </div>")

            html_parts.append("    </div>")

        # High Priority section (remaining items after top 3)
        remaining_high = [item for item in report.high_priority if item not in top_3]
        if remaining_high:
            html_parts.append('    <div class="section">')
            html_parts.append(
                f'        <h2 class="section-title">More High Priority ({len(remaining_high)})</h2>'
            )

            for item in remaining_high:
                html_parts.append('        <div class="item">')
                html_parts.append(
                    f'            <div class="item-title"><a href="{esc(item.url)}">{esc(item.title)}</a></div>'
                )
                html_parts.append(
                    f'            <div class="item-meta">'
                    f'<span class="priority-badge badge-high">HIGH</span>'
                    f"{esc(item.source_name)} • {esc(item.time_ago())}</div>"
                )
                if item.summary:
                    html_parts.append(
                        f'            <div class="item-summary">{esc(item.summary)}</div>'
                    )
                html_parts.append("        </div>")

            html_parts.append("    </div>")

        # Medium Priority section
        if report.medium_priority:
            html_parts.append('    <div class="section">')
            html_parts.append(
                f'        <h2 class="section-title">Medium Priority ({len(report.medium_priority)})</h2>'
            )

            for item in report.medium_priority[:10]:  # Show up to 10
                html_parts.append('        <div class="item">')
                html_parts.append(
                    f'            <div class="item-title"><a href="{esc(item.url)}">{esc(item.title)}</a></div>'
                )
                html_parts.append(
                    f'            <div class="item-meta">'
                    f'<span class="priority-badge badge-medium">MEDIUM</span>'
                    f"{esc(item.source_name)} • {esc(item.time_ago())}</div>"
                )
                if item.summary:
                    html_parts.append(
                        f'            <div class="item-summary">{esc(item.summary)}</div>'
                    )
                html_parts.append("        </div>")

            if len(report.medium_priority) > 10:
                html_parts.append(
                    f'        <p style="color: #718096; font-size: 14px; font-style: italic;">... and {len(report.medium_priority) - 10} more medium priority items</p>'
                )

            html_parts.append("    </div>")

        # Low Priority section
        if report.low_priority:
            html_parts.append('    <div class="section">')
            html_parts.append(
                f'        <h2 class="section-title">Low Priority ({len(report.low_priority)})</h2>'
            )

            for item in report.low_priority[:15]:  # Show up to 15
                html_parts.append('        <div class="item">')
                html_parts.append(
                    f'            <div class="item-title"><a href="{esc(item.url)}">{esc(item.title)}</a></div>'
                )
                html_parts.append(
                    f'            <div class="item-meta">'
                    f'<span class="priority-badge badge-low">LOW</span>'
                    f"{esc(item.source_name)} • {esc(item.time_ago())}</div>"
                )
                if item.summary and len(item.summary) > 0:
                    # Truncate to 150 chars for low priority
                    summary_text = (
                        item.summary[:150] + "..."
                        if len(item.summary) > 150
                        else item.summary
                    )
                    html_parts.append(
                        f'            <div class="item-summary">{esc(summary_text)}</div>'
                    )
                html_parts.append("        </div>")

            if len(report.low_priority) > 15:
                html_parts.append(
                    f'        <p style="color: #718096; font-size: 14px; font-style: italic;">... and {len(report.low_priority) - 15} more low priority items</p>'
                )

            html_parts.append("    </div>")

        # Summary section
        html_parts.append('    <div class="section">')
        html_parts.append('        <h2 class="section-title">Summary</h2>')
        html_parts.append('        <div class="summary-stats">')
        html_parts.append(
            f"            <p><strong>Period:</strong> Last {report.period_hours} hours</p>"
        )
        html_parts.append(
            f"            <p><strong>Total Items:</strong> {report.total_items} analyzed "
            f"({len(report.high_priority)} high, {len(report.medium_priority)} medium, "
            f"{len(report.low_priority)} low)</p>"
        )

        if report.top_sources:
            sources_str = ", ".join(
                [f"{name} ({count})" for name, count in report.top_sources]
            )
            html_parts.append(
                f"            <p><strong>Top Sources:</strong> {esc(sources_str)}</p>"
            )

        if report.key_themes:
            themes_str = ", ".join(report.key_themes)
            html_parts.append(
                f"            <p><strong>Key Themes:</strong> {esc(themes_str)}</p>"
            )

        html_parts.append("        </div>")
        html_parts.append("    </div>")

        # Close body and html
        html_parts.append("</body>")
        html_parts.append("</html>")

        return "\n".join(html_parts)
