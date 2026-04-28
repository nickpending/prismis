"""Unit tests for ContentDeepExtractor and _should_deep_extract gate -- task 1.2.

Invariants protected:
- INV-001: deep_extractor calls get_circuit_breaker with "prismis-openai-deep"
- SC-2: _should_deep_extract(medium, "high") == False (medium item skips)
Error cases:
- Empty content -> extract() returns None without LLM call
- Malformed JSON from LLM -> extract() returns None
- Missing "synthesis" key -> extract() returns None
"""

from __future__ import annotations

import json
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.orchestrator import DaemonOrchestrator

# Patch targets -- external APIs that cost money or add latency
_PATCH_COMPLETE = "prismis_daemon.deep_extractor.complete"  # claudex-guard: allow-mock
_PATCH_CIRCUIT = (
    "prismis_daemon.deep_extractor.get_circuit_breaker"  # claudex-guard: allow-mock
)
_PATCH_OBS_LOG = "prismis_daemon.deep_extractor.obs_log"  # claudex-guard: allow-mock


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> None:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


# ---------------------------------------------------------------------------
# Lightweight fake objects — no MagicMock, no external deps
# ---------------------------------------------------------------------------


class _FakeTokens:
    input = 10
    output = 20


class _FakeResult:
    """Minimal stand-in for llm_core CompletionResult."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.model = "gpt-5-mini-test"
        self.tokens = _FakeTokens()
        self.cost = 0.001
        self.duration_ms = 100


class _FakeCircuitBreaker:
    """Circuit breaker that always allows proceeding."""

    def check_can_proceed(self) -> bool:
        return True

    def record_success(self) -> None:
        pass

    def record_failure(self, exc: Exception) -> None:
        pass

    def get_status(self) -> dict:
        return {}


# ---------------------------------------------------------------------------
# _should_deep_extract gate -- pure function, tests all branches (SC-2)
# ---------------------------------------------------------------------------


def test_should_deep_extract_high_with_high_threshold() -> None:
    """
    SC-2 / INV-gate: high item + auto_extract="high" -> should extract.
    BREAKS: HIGH items never get deep extraction despite being the primary use-case.
    """
    assert DaemonOrchestrator._should_deep_extract("high", "high") is True


def test_should_deep_extract_medium_skips_with_high_threshold() -> None:
    """
    SC-2: medium item + auto_extract="high" -> must NOT extract.
    BREAKS: Budget waste -- medium items run gpt-5-mini calls they should skip.
    """
    result = DaemonOrchestrator._should_deep_extract("medium", "high")
    assert result is False, (
        "_should_deep_extract must return False for medium when threshold is 'high'"
    )


def test_should_deep_extract_low_skips_with_high_threshold() -> None:
    """Low item + auto_extract="high" -> skip."""
    assert DaemonOrchestrator._should_deep_extract("low", "high") is False


def test_should_deep_extract_all_includes_all_priorities() -> None:
    """auto_extract="all" -> extract for high, medium, low."""
    assert DaemonOrchestrator._should_deep_extract("high", "all") is True
    assert DaemonOrchestrator._should_deep_extract("medium", "all") is True
    assert DaemonOrchestrator._should_deep_extract("low", "all") is True


def test_should_deep_extract_none_threshold_always_skips() -> None:
    """auto_extract="none" or falsy -> never extract regardless of priority."""
    assert DaemonOrchestrator._should_deep_extract("high", "none") is False
    assert DaemonOrchestrator._should_deep_extract("high", "") is False
    assert DaemonOrchestrator._should_deep_extract("high", None) is False


def test_should_deep_extract_none_priority_skips() -> None:
    """None priority + auto_extract="all" -> skip (un-prioritized items excluded)."""
    # auto_extract="all" only returns True for ("high", "medium", "low") -- not None
    assert DaemonOrchestrator._should_deep_extract(None, "all") is False


# ---------------------------------------------------------------------------
# INV-001: ContentDeepExtractor calls get_circuit_breaker("prismis-openai-deep")
# ---------------------------------------------------------------------------


def test_deep_extractor_uses_deep_circuit_breaker() -> None:
    """
    INV-001: extract() must call get_circuit_breaker with the deep service name.
    BREAKS: A copy-paste of the service name silently routes circuit state to
    the light summarizer's circuit; deep failures trip the light pipeline.
    """
    extractor = ContentDeepExtractor("prismis-openai-deep")
    captured_service_names = []

    def fake_get_circuit_breaker(name: str):
        captured_service_names.append(name)
        return _FakeCircuitBreaker()

    fake_result = _FakeResult(
        json.dumps({"synthesis": "test synthesis", "quotables": []})
    )

    with (
        patch(_PATCH_CIRCUIT, side_effect=fake_get_circuit_breaker),
        patch(_PATCH_COMPLETE, return_value=fake_result),
        patch(_PATCH_OBS_LOG),
    ):
        extractor.extract(content="Some article content here.", title="Test")

    assert len(captured_service_names) == 1, "get_circuit_breaker should be called once"
    assert captured_service_names[0] == "prismis-openai-deep", (
        f"INV-001: expected 'prismis-openai-deep', got '{captured_service_names[0]}'"
    )


# ---------------------------------------------------------------------------
# Empty content guard
# ---------------------------------------------------------------------------


def test_extract_empty_content_returns_none_without_llm_call() -> None:
    """
    Empty or whitespace-only content returns None immediately without LLM call.
    BREAKS: LLM called with empty prompt; costs money and produces garbage.
    """
    extractor = ContentDeepExtractor("prismis-openai-deep")
    call_count = [0]

    def fake_complete(**kwargs):
        call_count[0] += 1

    with patch(_PATCH_COMPLETE, side_effect=fake_complete):
        result_empty = extractor.extract(content="")
        result_whitespace = extractor.extract(content="   \n  ")

    assert result_empty is None
    assert result_whitespace is None
    assert call_count[0] == 0, "complete() must not be called for empty content"


# ---------------------------------------------------------------------------
# Malformed JSON from LLM -> returns None (error case from Test Considerations)
# ---------------------------------------------------------------------------


def test_extract_malformed_json_returns_none() -> None:
    """
    Malformed JSON from LLM -> extract() returns None; does NOT raise.
    BREAKS: JSON parse error propagates up; orchestrator's INV-002 catch fires
    but item stored without any LLM failure logged properly.
    """
    extractor = ContentDeepExtractor("prismis-openai-deep")
    fake_result = _FakeResult("This is not JSON at all")

    with (
        patch(_PATCH_CIRCUIT, return_value=_FakeCircuitBreaker()),
        patch(_PATCH_COMPLETE, return_value=fake_result),
        patch(_PATCH_OBS_LOG),
    ):
        result = extractor.extract(content="Actual article content here.")

    assert result is None, "Malformed JSON must produce None, not raise"


# ---------------------------------------------------------------------------
# Missing "synthesis" key -> returns None (edge case from Test Considerations)
# ---------------------------------------------------------------------------


def test_extract_missing_synthesis_key_returns_none() -> None:
    """
    LLM returns valid JSON but missing "synthesis" key -> extract() returns None.
    BREAKS: KeyError propagates; item stored as extraction error vs. clean None.
    """
    extractor = ContentDeepExtractor("prismis-openai-deep")
    # Valid JSON but no "synthesis" key
    fake_result = _FakeResult(json.dumps({"quotables": ["a quote"], "model": "gpt-5"}))

    with (
        patch(_PATCH_CIRCUIT, return_value=_FakeCircuitBreaker()),
        patch(_PATCH_COMPLETE, return_value=fake_result),
        patch(_PATCH_OBS_LOG),
    ):
        result = extractor.extract(content="Actual article content here.")

    assert result is None, "Missing 'synthesis' key must produce None, not raise"
