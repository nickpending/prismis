"""Unit tests for ContentEvaluator logic functions."""

import json
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import (
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
)
from prismis_daemon.evaluator import ContentEvaluator, PriorityLevel

# ContentEvaluator takes an llm-core service name (evaluator.py's __init__); model
# choice and credentials are resolved by llm-core from services.toml, not from a
# config dict here.
SERVICE = "prismis-openai"

# dedup (cluster 11): evaluator.py no longer imports complete directly -- the call
# moved into the shared call_llm_with_circuit_breaker helper in llm_call.py.
_LLM_COMPLETE_MOCK = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock


def test_evaluator_initialization_with_service_name() -> None:
    """Test ContentEvaluator records the service it was constructed with."""
    evaluator = ContentEvaluator(SERVICE)

    assert evaluator.service_name == SERVICE


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


def test_parse_evaluation_response_refuses_matched_interests_that_is_not_a_list() -> None:
    """A malformed matched_interests is a failed reply, not an item with no interests.

    Coercing it to [] used to store a "high" reply as unprioritized, the same value a
    genuinely uninteresting item gets (constitution Principle II).
    """
    evaluator = ContentEvaluator(SERVICE)

    response = {
        "priority": "high",
        "matched_interests": "AI, Python",
    }

    with pytest.raises(ValueError, match="matched_interests"):
        evaluator._parse_evaluation_response(response)


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


# --- cluster 11 (dedup-triage.md): the LLM-call mechanics, including the
# circuit-breaker gating, now live in call_llm_with_circuit_breaker
# (prismis_daemon/llm_call.py). evaluator.py already gated on the circuit breaker
# before the extraction, but nothing here proved it -- these tests prove the
# consolidated caller still refuses, and still records success/failure, exactly as
# it did before the move. ---


def test_circuit_open_refuses_evaluate_content_without_hitting_the_llm() -> None:
    """An evaluate call is refused before complete() is ever invoked, once the
    service's circuit breaker is open."""
    reset_circuit_breaker(SERVICE)
    circuit = get_circuit_breaker(SERVICE)
    for _ in range(3):
        circuit.record_failure(RuntimeError("insufficient_quota"))
    assert circuit.check_can_proceed() is False, "setup: circuit must be open"

    evaluator = ContentEvaluator(SERVICE)
    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            with pytest.raises(RuntimeError, match="circuit breaker is open"):
                evaluator.evaluate_content(
                    content="c", title="t", url="u", context="ctx"
                )
            mock_complete.assert_not_called()
    finally:
        reset_circuit_breaker(SERVICE)


def test_evaluate_content_records_a_quota_failure_on_the_circuit_breaker() -> None:
    """Three quota-shaped failures from evaluate_content open the circuit -- the
    helper's record_failure call, not just a log line."""
    reset_circuit_breaker(SERVICE)
    evaluator = ContentEvaluator(SERVICE)
    try:
        with patch(
            _LLM_COMPLETE_MOCK, side_effect=RuntimeError("insufficient_quota")
        ):  # claudex-guard: allow-mock
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    evaluator.evaluate_content(
                        content="c", title="t", url="u", context="ctx"
                    )
        assert get_circuit_breaker(SERVICE).check_can_proceed() is False
    finally:
        reset_circuit_breaker(SERVICE)


def test_evaluate_content_records_success_and_closes_a_half_open_circuit() -> None:
    """A successful evaluate call closes a half-open circuit via record_success."""
    reset_circuit_breaker(SERVICE)
    circuit = get_circuit_breaker(SERVICE)
    circuit.state = CircuitState.HALF_OPEN
    evaluator = ContentEvaluator(SERVICE)

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = json.dumps(
        {"priority": "high", "matched_interests": ["AI"], "reasoning": "r"}
    )
    fake_result.tokens.input = 10
    fake_result.tokens.output = 5
    fake_result.cost = 0.0
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 1

    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            mock_complete.return_value = fake_result
            result = evaluator.evaluate_content(
                content="c", title="t", url="u", context="ctx"
            )
        assert result.priority == PriorityLevel.HIGH
        assert circuit.state == CircuitState.CLOSED
    finally:
        reset_circuit_breaker(SERVICE)
