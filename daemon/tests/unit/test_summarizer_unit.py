"""Unit tests for ContentSummarizer logic functions."""

from summarizer import ContentSummarizer


def test_summarizer_initialization_with_config() -> None:
    """Test ContentSummarizer initializes with provided config."""
    config = {
        "model": "gpt-4o",
    }

    summarizer = ContentSummarizer(config)

    assert summarizer.model == "gpt-4o"
    assert summarizer.config == config


def test_summarizer_initialization_with_defaults() -> None:
    """Test ContentSummarizer uses default model when minimal config provided."""
    config = {"model": "gpt-4.1-mini"}
    summarizer = ContentSummarizer(config)

    assert summarizer.model == "gpt-4.1-mini"
    assert summarizer.config == {"model": "gpt-4.1-mini"}


def test_summarizer_handles_env_api_key() -> None:
    """Test ContentSummarizer handles env: prefix for API keys."""
    import os

    # Set environment variable with actual value for testing
    original_key = os.environ.get("OPENAI_API_KEY")
    os.environ["TEST_API_KEY"] = "test-value"

    config = {
        "model": "gpt-4.1-mini",
        "api_key": "env:TEST_API_KEY",
    }

    ContentSummarizer(config)  # This sets the environment variable as a side effect

    # Should extract from environment
    assert os.environ.get("OPENAI_API_KEY") == "test-value"

    # Cleanup
    del os.environ["TEST_API_KEY"]
    if original_key:
        os.environ["OPENAI_API_KEY"] = original_key
    elif "OPENAI_API_KEY" in os.environ:
        del os.environ["OPENAI_API_KEY"]


def test_build_prompt_includes_all_fields() -> None:
    """Test prompt building includes title, url, source type, and content."""
    summarizer = ContentSummarizer({"model": "gpt-4.1-mini"})

    content = "This is test content about AI."
    title = "Test Article"
    url = "https://example.com/article"
    source_type = "rss"

    prompt = summarizer._build_prompt(content, title, url, source_type)

    # Verify all fields are included
    assert "Title: Test Article" in prompt
    assert "Source: rss" in prompt
    assert "URL: https://example.com/article" in prompt
    assert "This is test content about AI." in prompt
    assert "CONTENT:" in prompt


def test_build_prompt_handles_empty_fields() -> None:
    """Test prompt building handles empty optional fields gracefully."""
    summarizer = ContentSummarizer({"model": "gpt-4.1-mini"})

    content = "Minimal content"

    prompt = summarizer._build_prompt(content, "", "", "")

    # Should still have structure
    assert "Title: " in prompt
    assert "Source: " in prompt
    assert "URL: " in prompt
    assert "Minimal content" in prompt


def test_system_prompt_contains_required_instructions() -> None:
    """Test system prompt contains all required analysis instructions."""
    summarizer = ContentSummarizer({"model": "gpt-4.1-mini"})

    system_prompt = summarizer._get_system_prompt()

    # Verify key instructions present
    assert "400 characters" in system_prompt  # Summary limit
    assert "reading_summary" in system_prompt  # Reading summary field
    assert "alpha_insights" in system_prompt  # Alpha insights
    assert "patterns" in system_prompt  # Patterns field
    assert "entities" in system_prompt  # Entities field
    assert "JSON" in system_prompt  # JSON format requirement
    assert "markdown" in system_prompt.lower()  # Markdown formatting
    assert "10-15%" in system_prompt  # Reading summary length guidance


def test_system_prompt_has_entity_guidelines() -> None:
    """Test system prompt includes entity extraction guidelines."""
    summarizer = ContentSummarizer({"model": "gpt-4.1-mini"})

    system_prompt = summarizer._get_system_prompt()

    # Verify entity guidelines
    assert "TOP 5" in system_prompt  # Exactly 5 entities
    assert "NEVER INCLUDE" in system_prompt  # Exclusion rules
    assert "file names" in system_prompt.lower()  # Don't include files
    assert "searchable" in system_prompt.lower()  # Focus on searchability
