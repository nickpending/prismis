"""Unit tests for ContentEvaluator logic functions."""

from evaluator import ContentEvaluator, PriorityLevel


def test_evaluator_initialization_with_config() -> None:
    """Test ContentEvaluator initializes with provided config."""
    config = {
        "model": "gpt-4o",
    }

    evaluator = ContentEvaluator(config)

    assert evaluator.model == "gpt-4o"
    assert evaluator.config == config


def test_evaluator_initialization_with_defaults() -> None:
    """Test ContentEvaluator uses default model when minimal config provided."""
    config = {"model": "gpt-4.1-mini"}
    evaluator = ContentEvaluator(config)

    assert evaluator.model == "gpt-4.1-mini"
    assert evaluator.config == {"model": "gpt-4.1-mini"}


def test_parse_evaluation_response_with_valid_data() -> None:
    """Test parsing valid JSON response into ContentEvaluation."""
    config = {"model": "gpt-4.1-mini"}
    evaluator = ContentEvaluator(config)

    response = {
        "priority": "high",
        "matched_interests": ["AI", "LLM", "GPT"],
        "reasoning": "Directly relates to AI breakthroughs",
    }

    result = evaluator._parse_evaluation_response(response)

    assert result.priority == PriorityLevel.HIGH
    assert result.matched_interests == ["AI", "LLM", "GPT"]
    assert result.reasoning == "Directly relates to AI breakthroughs"


def test_parse_evaluation_response_normalizes_priority() -> None:
    """Test parsing normalizes priority values to lowercase."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    response = {
        "priority": "MEDIUM",  # Uppercase
        "matched_interests": [],
    }

    result = evaluator._parse_evaluation_response(response)

    assert result.priority == PriorityLevel.MEDIUM


def test_parse_evaluation_response_handles_invalid_priority() -> None:
    """Test parsing handles invalid priority with default."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    response = {
        "priority": "CRITICAL",  # Invalid value
        "matched_interests": ["test"],
    }

    result = evaluator._parse_evaluation_response(response)

    # Should default to MEDIUM for invalid priority
    assert result.priority == PriorityLevel.MEDIUM


def test_parse_evaluation_response_handles_missing_fields() -> None:
    """Test parsing handles missing optional fields."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    response = {
        "priority": "medium",
        # Missing matched_interests and reasoning
    }

    result = evaluator._parse_evaluation_response(response)

    assert result.priority == PriorityLevel.MEDIUM
    assert result.matched_interests == []
    assert result.reasoning is None


def test_parse_evaluation_response_validates_matched_interests() -> None:
    """Test parsing validates matched_interests is a list."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    # Test with non-list value
    response = {
        "priority": "high",
        "matched_interests": "AI, Python",  # String instead of list
    }

    result = evaluator._parse_evaluation_response(response)

    # Should convert to empty list
    assert result.matched_interests == []


def test_build_evaluation_prompt_includes_all_parts() -> None:
    """Test evaluation prompt includes content, context, and instructions."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    content = "This is AI content"
    title = "AI Article"
    url = "https://example.com"
    context = "High Priority: AI breakthroughs"

    messages = evaluator._build_evaluation_prompt(content, title, url, context)

    # Should have system and user messages
    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"

    # Check user prompt has all fields
    user_prompt = messages[1]["content"]
    assert "Title: AI Article" in user_prompt
    assert "URL: https://example.com" in user_prompt
    assert "This is AI content" in user_prompt
    assert "High Priority: AI breakthroughs" in user_prompt


def test_system_prompt_has_priority_guidelines() -> None:
    """Test system prompt includes priority evaluation guidelines."""
    evaluator = ContentEvaluator({"model": "gpt-4.1-mini"})

    messages = evaluator._build_evaluation_prompt("", "", "", "")
    system_prompt = messages[0]["content"]

    # Verify priority guidelines
    assert "high" in system_prompt
    assert "medium" in system_prompt
    assert "low" in system_prompt
    assert "matched_interests" in system_prompt
    assert "reasoning" in system_prompt
    assert "JSON" in system_prompt
