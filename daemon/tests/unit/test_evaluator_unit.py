"""Unit tests for ContentEvaluator logic functions."""

from prismis_daemon.evaluator import ContentEvaluator, PriorityLevel

# ContentEvaluator takes an llm-core service name (evaluator.py:40-47); model choice and
# credentials are resolved by llm-core from services.toml, not from a config dict here.
SERVICE = "prismis-openai"


def test_evaluator_initialization_with_service_name() -> None:
    """Test ContentEvaluator records the service it was constructed with."""
    evaluator = ContentEvaluator(SERVICE)

    assert evaluator.service_name == SERVICE
    assert evaluator.temperature == 0.3


def test_parse_evaluation_response_with_valid_data() -> None:
    """Test parsing valid JSON response into ContentEvaluation."""
    evaluator = ContentEvaluator(SERVICE)

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
    evaluator = ContentEvaluator(SERVICE)

    response = {
        "priority": "MEDIUM",  # Uppercase
        "matched_interests": ["AI"],
    }

    result = evaluator._parse_evaluation_response(response)

    assert result.priority == PriorityLevel.MEDIUM


def test_parse_evaluation_response_handles_invalid_priority() -> None:
    """Test parsing handles invalid priority with default."""
    evaluator = ContentEvaluator(SERVICE)

    response = {
        "priority": "CRITICAL",  # Invalid value
        "matched_interests": ["test"],
    }

    result = evaluator._parse_evaluation_response(response)

    # Should default to MEDIUM for invalid priority
    assert result.priority == PriorityLevel.MEDIUM


def test_parse_evaluation_response_handles_missing_fields() -> None:
    """Test parsing handles missing optional fields.

    A response with no matched_interests carries no priority: the evaluator maps it to
    None rather than inventing MEDIUM, so unmatched content stays unranked.
    """
    evaluator = ContentEvaluator(SERVICE)

    response = {
        "priority": "medium",
        # Missing matched_interests and reasoning
    }

    result = evaluator._parse_evaluation_response(response)

    assert result.priority is None
    assert result.matched_interests == []
    assert result.reasoning is None


def test_parse_evaluation_response_validates_matched_interests() -> None:
    """Test parsing validates matched_interests is a list."""
    evaluator = ContentEvaluator(SERVICE)

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
    evaluator = ContentEvaluator(SERVICE)

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
    evaluator = ContentEvaluator(SERVICE)

    messages = evaluator._build_evaluation_prompt("", "", "", "")
    system_prompt = messages[0]["content"]

    # Verify priority guidelines
    assert "high" in system_prompt
    assert "medium" in system_prompt
    assert "low" in system_prompt
    assert "matched_interests" in system_prompt
    assert "reasoning" in system_prompt
    assert "JSON" in system_prompt
