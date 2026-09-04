"""Unit tests for ContentSummarizer logic functions."""

from prismis_daemon.summarizer import ContentSummarizer

# ContentSummarizer takes a llm-core service name (summarizer.py:39-46); model choice and
# credentials are resolved by llm-core from services.toml, not from a config dict here.
SERVICE = "prismis-openai"


def test_summarizer_initialization_with_service_name() -> None:
    """Test ContentSummarizer records the service it was constructed with."""
    summarizer = ContentSummarizer(SERVICE)

    assert summarizer.service_name == SERVICE
    assert summarizer.temperature == 0.3


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
    assert "entities" in system_prompt  # Entities field
    assert "JSON" in system_prompt  # JSON format requirement
    assert "markdown" in system_prompt.lower()  # Markdown formatting
    assert "10-15%" in system_prompt  # Reading summary length guidance


def test_system_prompt_has_entity_guidelines() -> None:
    """Test system prompt includes entity extraction guidelines."""
    summarizer = ContentSummarizer(SERVICE)

    system_prompt = summarizer._get_system_prompt()

    # Verify entity guidelines
    assert "EXTRACT HASHTAG-STYLE TAGS" in system_prompt  # The extraction step
    assert "Extract 3-5 essential tags" in system_prompt  # Tag count guidance
    assert "SIMPLIFICATION RULES" in system_prompt  # Exclusion/reduction rules
    assert "searchable" in system_prompt.lower()  # Focus on searchability
