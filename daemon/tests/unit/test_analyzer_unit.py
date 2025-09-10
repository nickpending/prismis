"""Unit tests for Analyzer logic functions."""

from analyzer import Analyzer


def test_validate_result_with_complete_data() -> None:
    """Test result validation with all fields present and valid."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Create complete valid result
    raw_result = {
        "summary": "Test summary of the content",
        "priority": "HIGH",  # Uppercase to test normalization
        "topics": ["ai", "python", "testing"],
        "relevance_score": 0.85,
    }

    validated = analyzer._validate_result(raw_result)

    # Verify all fields normalized correctly
    assert validated["summary"] == "Test summary of the content"
    assert validated["priority"] == "high"  # Should be normalized to lowercase
    assert validated["topics"] == ["ai", "python", "testing"]
    assert validated["relevance_score"] == 0.85


def test_validate_result_with_missing_fields() -> None:
    """Test result validation handles missing fields with defaults."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Create result with missing fields
    raw_result = {"priority": "medium"}

    validated = analyzer._validate_result(raw_result)

    # Verify defaults applied
    assert validated["summary"] == "No summary available"
    assert validated["priority"] == "medium"
    assert validated["topics"] == []
    assert validated["relevance_score"] == 0.5  # Default


def test_validate_result_with_invalid_priority() -> None:
    """Test result validation handles invalid priority values."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Create result with invalid priority
    raw_result = {
        "summary": "Test summary",
        "priority": "CRITICAL",  # Invalid priority
        "topics": ["test"],
        "relevance_score": 0.9,
    }

    validated = analyzer._validate_result(raw_result)

    # Should default to 'low' for invalid priority
    assert validated["priority"] == "low"
    assert validated["summary"] == "Test summary"


def test_validate_result_clamps_relevance_score() -> None:
    """Test result validation clamps relevance score to 0-1 range."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Test score above 1.0
    result_high = {"relevance_score": 1.5}
    validated_high = analyzer._validate_result(result_high)
    assert validated_high["relevance_score"] == 1.0

    # Test score below 0.0
    result_low = {"relevance_score": -0.3}
    validated_low = analyzer._validate_result(result_low)
    assert validated_low["relevance_score"] == 0.0

    # Test valid score
    result_valid = {"relevance_score": 0.7}
    validated_valid = analyzer._validate_result(result_valid)
    assert validated_valid["relevance_score"] == 0.7


def test_validate_result_handles_non_list_topics() -> None:
    """Test result validation converts non-list topics to empty list."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Test with string instead of list
    raw_result = {"topics": "ai, python"}
    validated = analyzer._validate_result(raw_result)
    assert validated["topics"] == []

    # Test with None
    raw_result = {"topics": None}
    validated = analyzer._validate_result(raw_result)
    assert validated["topics"] == []


def test_build_prompt_with_normal_content() -> None:
    """Test prompt building with normal-length content."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    content = "This is test content about AI developments."
    context = "High Priority: AI breakthroughs"

    prompt = analyzer._build_prompt(content, context)

    # Verify content and context are included
    assert content in prompt
    assert context in prompt
    assert "CONTENT TO ANALYZE:" in prompt
    assert "USER'S INTERESTS AND PRIORITIES:" in prompt
    assert "JSON object" in prompt


def test_build_prompt_truncates_long_content() -> None:
    """Test prompt building truncates content over 3000 chars."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Create content longer than 3000 chars
    long_content = "A" * 3500
    context = "High Priority: Testing"

    prompt = analyzer._build_prompt(long_content, context)

    # Should be truncated with ellipsis
    assert "AAA..." in prompt
    assert (
        len(prompt) < len(long_content) + len(context) + 1000
    )  # Much shorter than original


def test_fallback_parse_extracts_high_priority() -> None:
    """Test fallback parsing extracts 'high' priority from text."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    response_text = "This content is HIGH priority and very important."

    result = analyzer._fallback_parse(response_text)

    assert result["priority"] == "high"
    assert result["summary"] == response_text  # Full text since < 200 chars
    assert result["topics"] == []
    assert result["relevance_score"] == 0.5


def test_fallback_parse_extracts_medium_priority() -> None:
    """Test fallback parsing extracts 'medium' priority from text."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    response_text = "This content has medium importance for the user."

    result = analyzer._fallback_parse(response_text)

    assert result["priority"] == "medium"


def test_fallback_parse_defaults_to_low_priority() -> None:
    """Test fallback parsing defaults to 'low' when no priority found."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    response_text = "This content doesn't mention any priority levels."

    result = analyzer._fallback_parse(response_text)

    assert result["priority"] == "low"


def test_fallback_parse_truncates_long_summary() -> None:
    """Test fallback parsing truncates summary if response > 200 chars."""
    analyzer = Analyzer({"model": "gpt-4.1-mini"})

    # Create response longer than 200 chars
    long_response = "B" * 250

    result = analyzer._fallback_parse(long_response)

    # Should be truncated to 200 chars
    assert len(result["summary"]) == 200
    assert result["summary"] == "B" * 200
