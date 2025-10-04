"""Unit tests for HTML report formatting invariants."""

import html
from datetime import datetime, timezone

from prismis_daemon.reports import ReportGenerator, DailyReport, ContentSummary


def test_html_escaping_integrity() -> None:
    """
    INVARIANT: All user-controlled content is HTML-escaped
    BREAKS: XSS attacks if malicious content is not escaped
    """
    # Create report with malicious content in all user-controlled fields
    malicious_script = "<script>alert('XSS')</script>"
    malicious_title = f"Dangerous Title {malicious_script}"
    malicious_source = f"Evil Source {malicious_script}"
    malicious_summary = f"Summary with {malicious_script} inside"
    malicious_url = f"https://evil.com?param={malicious_script}"

    content_item = ContentSummary(
        title=malicious_title,
        source_name=malicious_source,
        url=malicious_url,
        summary=malicious_summary,
        published_at=datetime.now(timezone.utc),
        priority="high",
        analysis={"matched_interests": ["evil", "malicious"]},
    )

    report = DailyReport(
        generated_at=datetime.now(timezone.utc),
        period_hours=24,
        high_priority=[content_item],
    )

    # Mock storage for ReportGenerator
    class MockStorage:
        pass

    generator = ReportGenerator(MockStorage())
    html_output = generator.format_as_html(report)

    # CRITICAL: No unescaped script tags should exist in output
    assert "<script>" not in html_output, (
        "Unescaped script tag found - XSS vulnerability!"
    )
    assert "alert('XSS')" not in html_output, (
        "Unescaped JavaScript found - XSS vulnerability!"
    )

    # Verify content is properly escaped but still present
    escaped_script = html.escape(malicious_script)
    assert escaped_script in html_output, "Content should be escaped but present"

    # Verify all user fields are escaped
    assert html.escape(malicious_title) in html_output
    assert html.escape(malicious_source) in html_output
    assert html.escape(malicious_summary) in html_output
    assert html.escape(malicious_url) in html_output


def test_html_structure_validity() -> None:
    """
    INVARIANT: Generated HTML has valid structure
    BREAKS: Email clients fail to render if HTML is malformed
    """
    content_item = ContentSummary(
        title="Valid Content",
        source_name="Test Source",
        url="https://example.com",
        summary="A normal summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
    )

    report = DailyReport(
        generated_at=datetime.now(timezone.utc),
        period_hours=24,
        high_priority=[content_item],
        medium_priority=[],
        low_priority=[],
    )

    class MockStorage:
        pass

    generator = ReportGenerator(MockStorage())
    html_output = generator.format_as_html(report)

    # CRITICAL: HTML structure must be valid
    assert html_output.startswith("<!DOCTYPE html>"), "Missing DOCTYPE declaration"
    assert '<html lang="en">' in html_output, "Missing html tag with lang attribute"
    assert "<head>" in html_output and "</head>" in html_output, "Missing head section"
    assert "<body>" in html_output and "</body>" in html_output, "Missing body section"
    assert "</html>" in html_output, "Missing closing html tag"

    # Verify essential meta tags for email compatibility
    assert '<meta charset="UTF-8">' in html_output, "Missing charset meta tag"
    assert '<meta name="viewport"' in html_output, "Missing viewport meta tag"

    # Count opening vs closing tags for critical elements
    div_open = html_output.count("<div")
    div_close = html_output.count("</div>")
    assert div_open == div_close, (
        f"Unmatched div tags: {div_open} open, {div_close} close"
    )

    # Verify no broken HTML entities
    assert "&lt;&gt;" not in html_output, "Double-encoded HTML entities found"


def test_data_consistency_html_vs_markdown() -> None:
    """
    INVARIANT: HTML and markdown formats contain same information
    BREAKS: Users see different data depending on format choice
    """
    # Create diverse report with all priority levels
    high_item = ContentSummary(
        title="High Priority Item",
        source_name="Source A",
        url="https://a.com",
        summary="High priority summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
    )

    medium_item = ContentSummary(
        title="Medium Priority Item",
        source_name="Source B",
        url="https://b.com",
        summary="Medium priority summary",
        published_at=datetime.now(timezone.utc),
        priority="medium",
    )

    low_item = ContentSummary(
        title="Low Priority Item",
        source_name="Source C",
        url="https://c.com",
        summary="Low priority summary",
        published_at=datetime.now(timezone.utc),
        priority="low",
    )

    report = DailyReport(
        generated_at=datetime.now(timezone.utc),
        period_hours=24,
        high_priority=[high_item],
        medium_priority=[medium_item],
        low_priority=[low_item],
    )

    class MockStorage:
        pass

    generator = ReportGenerator(MockStorage())
    html_output = generator.format_as_html(report)
    markdown_output = generator.format_as_markdown(report)

    # CRITICAL: Same content must appear in both formats

    # Verify all titles present in both
    assert (
        "High Priority Item" in html_output and "High Priority Item" in markdown_output
    )
    assert (
        "Medium Priority Item" in html_output
        and "Medium Priority Item" in markdown_output
    )
    assert "Low Priority Item" in html_output and "Low Priority Item" in markdown_output

    # Verify all source names present in both
    assert "Source A" in html_output and "Source A" in markdown_output
    assert "Source B" in html_output and "Source B" in markdown_output
    assert "Source C" in html_output and "Source C" in markdown_output

    # Verify all URLs present in both
    assert "https://a.com" in html_output and "https://a.com" in markdown_output
    assert "https://b.com" in html_output and "https://b.com" in markdown_output
    assert "https://c.com" in html_output and "https://c.com" in markdown_output

    # Verify summary section data consistency
    assert (
        "Total Items:</strong> 3" in html_output
        and "3 items analyzed" in markdown_output
    )
    assert "1 high" in html_output and "1 high priority" in markdown_output
    assert "1 medium" in html_output and "1 medium" in markdown_output
    assert "1 low" in html_output and "1 low" in markdown_output


def test_top_3_ranking_with_corrupt_data() -> None:
    """
    INVARIANT: Top 3 algorithm handles corrupt analysis data gracefully
    BREAKS: Ranking crashes or produces wrong results with bad data
    """
    # Create items with various levels of corrupt analysis data
    item_good = ContentSummary(
        title="Good Item",
        source_name="Source A",
        url="https://a.com",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
        analysis={"matched_interests": ["topic1", "topic2", "topic3"]},  # 3 matches
    )

    item_no_analysis = ContentSummary(
        title="No Analysis Item",
        source_name="Source B",
        url="https://b.com",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
        analysis=None,  # Corrupt: None instead of dict
    )

    item_bad_interests = ContentSummary(
        title="Bad Interests Item",
        source_name="Source C",
        url="https://c.com",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
        analysis={"matched_interests": "not a list"},  # Corrupt: string instead of list
    )

    item_missing_interests = ContentSummary(
        title="Missing Interests Item",
        source_name="Source D",
        url="https://d.com",
        summary="Summary",
        published_at=datetime.now(timezone.utc),
        priority="high",
        analysis={"other_field": "value"},  # Corrupt: missing matched_interests
    )

    item_null_date = ContentSummary(
        title="Null Date Item",
        source_name="Source E",
        url="https://e.com",
        summary="Summary",
        published_at=None,  # Corrupt: null date
        priority="high",
        analysis={"matched_interests": ["topic1"]},  # 1 match
    )

    report = DailyReport(
        generated_at=datetime.now(timezone.utc),
        period_hours=24,
        high_priority=[
            item_good,
            item_no_analysis,
            item_bad_interests,
            item_missing_interests,
            item_null_date,
        ],
    )

    # CRITICAL: Algorithm must not crash with corrupt data
    try:
        top_3 = report.top_3_must_reads
    except Exception as e:
        assert False, f"Top 3 algorithm crashed with corrupt data: {e}"

    # Verify it returns valid results
    assert isinstance(top_3, list), "Top 3 must return a list"
    assert len(top_3) <= 3, "Top 3 must return at most 3 items"
    assert len(top_3) <= len(report.high_priority), (
        "Top 3 cannot exceed high priority count"
    )

    # CRITICAL: Item with most interests should rank first (despite corruption)
    if len(top_3) > 0:
        assert top_3[0].title == "Good Item", (
            "Item with most interests should rank first"
        )

    # Verify corrupt items are handled gracefully (algorithm works with whatever data is available)
    titles = [item.title for item in top_3]

    # All items should be from our test set
    all_test_titles = {
        "Good Item",
        "No Analysis Item",
        "Bad Interests Item",
        "Missing Interests Item",
        "Null Date Item",
    }
    for title in titles:
        assert title in all_test_titles, f"Unexpected item in top 3: {title}"

    # Second item should be "Null Date Item" (1 interest, even with null date)
    if len(top_3) >= 2:
        assert top_3[1].title == "Null Date Item", (
            "Item with 1 interest should rank second despite null date"
        )
