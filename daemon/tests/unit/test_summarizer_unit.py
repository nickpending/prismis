"""Unit tests for ContentSummarizer logic functions."""

import dataclasses
import json
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.summarizer import ContentSummarizer, ContentSummary

# ContentSummarizer takes a llm-core service name (summarizer.py:39-46); model choice and
# credentials are resolved by llm-core from services.toml, not from a config dict here.
SERVICE = "prismis-openai"


def test_summarizer_initialization_with_service_name() -> None:
    """Test ContentSummarizer records the service it was constructed with."""
    summarizer = ContentSummarizer(SERVICE)

    assert summarizer.service_name == SERVICE


def test_build_prompt_includes_all_fields() -> None:
    """Test prompt building includes title, url, source type, and content."""
    summarizer = ContentSummarizer(SERVICE)

    content = "This is test content about AI."
    title = "Test Article"
    url = "https://example.com/article"
    source_type = "rss"

    prompt = summarizer._build_prompt(content, title, url, source_type, "", {})

    # Verify all fields are included
    assert "Title: Test Article" in prompt
    assert "Source Type: rss" in prompt
    assert "URL: https://example.com/article" in prompt
    assert "This is test content about AI." in prompt
    assert "CONTENT:" in prompt


def test_build_prompt_includes_source_name_and_metadata() -> None:
    """Test prompt building surfaces source name and metadata to the LLM.

    The prompt tells the LLM not to infer metadata, so anything it is allowed to use has
    to be passed through explicitly (summarizer.py:527-537).
    """
    summarizer = ContentSummarizer(SERVICE)

    prompt = summarizer._build_prompt(
        "body",
        "Title",
        "https://example.com",
        "reddit",
        "r/rust",
        {"author": "someone", "subreddit": "rust", "view_count": 1234},
    )

    assert "Source Name: r/rust" in prompt
    assert "Author: someone" in prompt
    assert "Subreddit: r/rust" in prompt
    assert "View Count: 1,234" in prompt


def test_build_prompt_handles_empty_fields() -> None:
    """Test prompt building handles empty optional fields gracefully."""
    summarizer = ContentSummarizer(SERVICE)

    content = "Minimal content"

    prompt = summarizer._build_prompt(content, "", "", "", "", {})

    # Should still have structure
    assert "Title: " in prompt
    assert "Source Type: " in prompt
    assert "URL: " in prompt
    assert "Minimal content" in prompt


def test_system_prompt_contains_required_instructions() -> None:
    """Test system prompt contains all required analysis instructions."""
    summarizer = ContentSummarizer(SERVICE)

    system_prompt = summarizer._get_system_prompt()

    # Verify key instructions present
    assert "400 chars max" in system_prompt  # Summary limit
    assert "reading_summary" in system_prompt  # Reading summary field
    assert "alpha_insights" in system_prompt  # Alpha insights
    assert "patterns" in system_prompt  # Patterns field
    assert "JSON" in system_prompt  # JSON format requirement
    assert "markdown" in system_prompt.lower()  # Markdown formatting
    assert "10-15%" in system_prompt  # Reading summary length guidance


# --- SC-1: entities/hashtag-style tags are gone from both prompts, from
# ContentSummary, and from what a parsed response requires. ---


def test_standard_system_prompt_has_no_entity_tags() -> None:
    """Test the standard/brief/detailed system prompt no longer asks for tags."""
    summarizer = ContentSummarizer(SERVICE)

    system_prompt = summarizer._get_system_prompt()

    assert "entities" not in system_prompt.lower()
    assert "hashtag" not in system_prompt.lower()


def test_diff_system_prompt_has_no_entity_tags() -> None:
    """Test the diff/changelog system prompt no longer asks for tags."""
    summarizer = ContentSummarizer(SERVICE)

    diff_prompt = summarizer._get_diff_system_prompt()

    assert "entities" not in diff_prompt.lower()


def test_content_summary_has_no_entity_tags_field() -> None:
    """Test ContentSummary no longer carries an entities field."""
    field_names = {f.name for f in dataclasses.fields(ContentSummary)}

    assert "entities" not in field_names


def test_summarize_with_analysis_parses_response_with_no_entity_tags_key() -> None:
    """Test a response with no entities key still parses into a complete ContentSummary.

    entities is not (and must not become) a required response field.
    """
    reset_circuit_breaker()
    summarizer = ContentSummarizer(SERVICE)

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = json.dumps(
        {
            "summary": "Test summary",
            "reading_summary": "# Test\n\nContent",
            "alpha_insights": ["insight"],
            "patterns": ["pattern"],
            "quotes": [],
            "tools": [],
            "urls": [],
        }
    )
    fake_result.tokens.input = 100
    fake_result.tokens.output = 50
    fake_result.cost = 0.001
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 500

    try:
        with patch(
            "prismis_daemon.summarizer.complete"
        ) as mock_complete:  # claudex-guard: allow-mock
            mock_complete.return_value = fake_result

            result = summarizer.summarize_with_analysis(
                content="Test article content about AI",
                title="AI Test",
                url="https://example.com",
                source_type="rss",
            )
    finally:
        reset_circuit_breaker()

    assert result is not None
    assert result.summary == "Test summary"
    assert result.alpha_insights == ["insight"]
    assert result.patterns == ["pattern"]
    assert result.quotes == []
    assert not hasattr(result, "entities")
