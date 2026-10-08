"""Tests for task 3.2: litellm consumer migration invariants.

The invariants this
file guards -- no litellm, and complete() reached through a service_name constructor
-- outlived the library that originally motivated them, so the file stays under its
original name with its original litellm-focused scope.

Covers:
- INV-001: Zero litellm imports in daemon/src/prismis_daemon/
- SC-11: Summarizer uses llm_client.complete() via service_name constructor
- SC-12: Evaluator uses llm_client.complete() via service_name constructor
- SC-13: Zero litellm references in daemon/src/ and pyproject.toml
- SC-16: __main__.py passes config.llm_service to consumer constructors
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest

# Add src to path for absolute imports (mirrors conftest.py pattern)
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.summarizer import ContentSummarizer


@pytest.fixture(autouse=True)
def _clean_circuit_registry():
    """Reset the real circuit breaker registry before and after each test.

    llm_client.complete is the one collaborator the constitution permits standing in
    for; the circuit breaker in front of it is prismis's own and stays real -- a
    fresh, closed breaker lets check_can_proceed() return True without patching
    get_circuit_breaker.
    """
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()

# --- INV-001 / SC-13 ---


def test_INVARIANT_zero_litellm_imports_in_source() -> None:
    """
    INV-001: Zero litellm imports must exist in daemon/src/prismis_daemon/
    BREAKS: Supply chain compromise - litellm v1.82.7/1.82.8 contained a credential stealer
    """
    src_dir = Path(__file__).parent.parent.parent / "src" / "prismis_daemon"
    assert src_dir.exists(), f"Source directory not found: {src_dir}"

    violations = []
    for py_file in src_dir.rglob("*.py"):
        content = py_file.read_text()
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if "import litellm" in line or "from litellm" in line:
                violations.append(f"{py_file.name}:{i}: {line.strip()}")

    assert violations == [], (
        "INV-001 FAILED - litellm imports found in source:\n" + "\n".join(violations)
    )


def test_INVARIANT_zero_litellm_in_pyproject() -> None:
    """
    SC-13: litellm must not appear in pyproject.toml dependencies
    BREAKS: Compromised package re-added as dependency silently
    """
    pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
    assert pyproject_path.exists(), f"pyproject.toml not found: {pyproject_path}"

    content = pyproject_path.read_text()
    assert "litellm" not in content, "SC-13 FAILED - litellm found in pyproject.toml"


# --- SC-11: Summarizer uses llm_client ---


def test_SC11_summarizer_constructor_takes_service_name() -> None:
    """
    SC-11: ContentSummarizer constructor must accept service_name: str, not config dict
    BREAKS: Daemon fails to initialize summarizer; no content is summarized
    """
    summarizer = ContentSummarizer("prismis-openai")
    assert summarizer.service_name == "prismis-openai"
    # Old API had .model and .config attributes - must be gone
    assert not hasattr(summarizer, "model"), (
        "Summarizer still has old .model attribute (config dict API not removed)"
    )
    assert not hasattr(summarizer, "config"), (
        "Summarizer still has old .config attribute (config dict API not removed)"
    )


def test_SC11_summarizer_calls_llm_core_complete() -> None:
    """
    SC-11: summarize_with_analysis() must call complete() with service param
    BREAKS: Summarizer bypasses llm_client, uses wrong provider or no auth
    """
    summarizer = ContentSummarizer("prismis-openai")

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = (
        '{"summary": "Test summary", "reading_summary": "# Test\\n\\nContent",'
        ' "alpha_insights": ["insight"], "patterns": ["pattern"],'
        ' "entities": ["ai"], "quotes": [], "tools": [], "urls": []}'
    )
    fake_result.tokens.input = 100
    fake_result.tokens.output = 50
    fake_result.cost = 0.001
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 500

    with patch(
        # dedup (cluster 11): summarizer.py no longer imports complete directly --
        # the call moved into the shared call_llm_with_circuit_breaker helper.
        "prismis_daemon.llm_call.complete"
    ) as mock_complete:  # claudex-guard: allow-mock
        mock_complete.return_value = fake_result

        result = summarizer.summarize_with_analysis(
            content="Test article content about AI",
            title="AI Test",
            url="https://example.com",
            source_type="rss",
        )

        # Verify complete() was called with service= kwarg
        assert mock_complete.called, "complete() was not called"
        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs.get("service") == "prismis-openai", (
            f"complete() called with wrong service: {call_kwargs.get('service')}"
        )

        assert result is not None
        assert result.summary == "Test summary"


# --- SC-12: Evaluator uses llm_client ---


def test_SC12_evaluator_constructor_takes_service_name() -> None:
    """
    SC-12: ContentEvaluator constructor must accept service_name: str, not config dict
    BREAKS: Daemon fails to initialize evaluator; no content is prioritized
    """
    evaluator = ContentEvaluator("prismis-openai")
    assert evaluator.service_name == "prismis-openai"
    assert not hasattr(evaluator, "model"), (
        "Evaluator still has old .model attribute (config dict API not removed)"
    )
    assert not hasattr(evaluator, "config"), (
        "Evaluator still has old .config attribute (config dict API not removed)"
    )


def test_SC12_evaluator_calls_llm_core_complete() -> None:
    """
    SC-12: evaluate_content() must call complete() with service param
    BREAKS: Evaluator bypasses llm_client, uses wrong provider, content never prioritized
    """
    evaluator = ContentEvaluator("prismis-openai")

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = (
        '{"priority": "high", "matched_interests": ["AI"],'
        ' "reasoning": "Matches AI interest"}'
    )
    fake_result.tokens.input = 80
    fake_result.tokens.output = 30
    fake_result.cost = 0.0005
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 300

    with patch(
        # dedup (cluster 11): evaluator.py no longer imports complete directly --
        # the call moved into the shared call_llm_with_circuit_breaker helper.
        "prismis_daemon.llm_call.complete"
    ) as mock_complete:  # claudex-guard: allow-mock
        mock_complete.return_value = fake_result

        result = evaluator.evaluate_content(
            content="AI article content",
            title="AI Test",
            url="https://example.com",
            context="High Priority: AI, machine learning",
        )

        assert mock_complete.called, "complete() was not called"
        call_kwargs = mock_complete.call_args.kwargs
        assert call_kwargs.get("service") == "prismis-openai", (
            f"complete() called with wrong service: {call_kwargs.get('service')}"
        )

        assert result is not None
        assert result.priority is not None


# migrate-config is covered by test_migrate_config_unit.py.

# --- SC-16: __main__.py passes config.llm_light_service to consumers ---


def test_SC16_main_passes_llm_light_service_to_constructors() -> None:
    """
    SC-16: __main__.py must pass config.llm_light_service to ContentSummarizer and
    ContentEvaluator (renamed from llm_service in task 1.1).
    BREAKS: Consumers initialized with wrong service name, silently use wrong LLM.
    """
    main_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "__main__.py"
    )
    assert main_path.exists(), f"__main__.py not found: {main_path}"

    source = main_path.read_text()

    assert "ContentSummarizer(config.llm_light_service)" in source, (
        "ContentSummarizer not constructed with config.llm_light_service"
    )

    assert "ContentEvaluator(config.llm_light_service)" in source, (
        "ContentEvaluator not constructed with config.llm_light_service"
    )
