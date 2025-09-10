"""Integration tests for Analyzer with real LLM API calls."""

import os
import pytest
from analyzer import Analyzer


def test_analyze_with_real_llm_high_priority() -> None:
    """Test complete analysis workflow with real LLM API for high priority content.

    This test:
    - Initializes Analyzer with real configuration
    - Makes actual LLM API call
    - Analyzes AI-related content against relevant context
    - Verifies structured response with high priority
    """
    # Configure with API key (use environment variable or config)
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
    }

    # Skip test if no API key is available
    if not config["api_key"]:
        pytest.skip("OPENAI_API_KEY environment variable not set")

    analyzer = Analyzer(config)

    # Test content that should be high priority
    content = """
    OpenAI announced GPT-5 today with breakthrough reasoning capabilities. 
    The new model shows 10x performance improvements in mathematical reasoning 
    and can solve complex problems that previous models struggled with.
    This represents a major advancement in AI capabilities.
    """

    context = """
    High Priority Topics:
    - AI/LLM breakthroughs, especially reasoning capabilities
    - OpenAI model releases and improvements
    
    Medium Priority Topics:
    - Python tooling updates
    - Database design patterns
    
    Low Priority Topics:
    - General programming tutorials
    
    Not Interested:
    - Crypto, blockchain, web3
    """

    # Make real LLM API call
    result = analyzer.analyze(content, context)

    # Verify result structure
    assert isinstance(result, dict)
    assert "summary" in result
    assert "priority" in result
    assert "topics" in result
    assert "relevance_score" in result

    # Verify high priority assignment for AI content
    assert result["priority"] == "high"
    assert result["relevance_score"] > 0.8

    # Verify summary is meaningful
    assert len(result["summary"]) > 10
    assert "GPT" in result["summary"] or "OpenAI" in result["summary"]

    # Verify topics extraction
    assert isinstance(result["topics"], list)
    assert len(result["topics"]) > 0


def test_analyze_with_real_llm_medium_priority() -> None:
    """Test analysis assigns medium priority correctly."""
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
    }

    # Skip test if no API key is available
    if not config["api_key"]:
        pytest.skip("OPENAI_API_KEY environment variable not set")

    analyzer = Analyzer(config)

    # Content that should be medium priority
    content = """
    Python 3.13 was released with improved performance and new features.
    The release includes better error messages and faster startup times.
    Several standard library modules have been updated.
    """

    context = """
    High Priority Topics:
    - AI/LLM breakthroughs
    
    Medium Priority Topics:
    - Python releases and tooling updates
    - Programming language improvements
    
    Low Priority Topics:
    - General tutorials
    """

    result = analyzer.analyze(content, context)

    # Should be medium priority
    assert result["priority"] == "medium"
    assert 0.5 < result["relevance_score"] <= 0.8
    assert len(result["summary"]) > 10


def test_analyze_with_real_llm_low_priority() -> None:
    """Test analysis assigns low priority correctly."""
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
    }

    # Skip test if no API key is available
    if not config["api_key"]:
        pytest.skip("OPENAI_API_KEY environment variable not set")

    analyzer = Analyzer(config)

    # Content that should be low priority
    content = """
    Here's a basic tutorial on how to center a div in CSS.
    You can use flexbox, grid, or traditional margin techniques.
    This is a common question for beginners.
    """

    context = """
    High Priority Topics:
    - AI/LLM breakthroughs
    
    Medium Priority Topics:
    - Python tooling updates
    
    Low Priority Topics:
    - Basic programming tutorials
    - CSS tips and tricks
    """

    result = analyzer.analyze(content, context)

    # Should be low priority
    assert result["priority"] == "low"
    assert result["relevance_score"] <= 0.5
    assert len(result["summary"]) > 10


def test_analyzer_with_environment_variable_api_key() -> None:
    """Test analyzer works with API key from environment variable."""
    import os

    # Set API key in environment
    original_key = os.environ.get("OPENAI_API_KEY")
    os.environ["OPENAI_API_KEY"] = os.environ.get("OPENAI_API_KEY", "")

    try:
        # Create analyzer with minimal config (API key from env var)
        analyzer = Analyzer({"model": "gpt-4.1-mini"})

        content = "AI breakthrough in reasoning capabilities"
        context = "High Priority: AI breakthroughs"

        result = analyzer.analyze(content, context)

        # Should work with environment variable
        assert result["priority"] in ["high", "medium", "low"]
        assert isinstance(result["summary"], str)

    finally:
        # Restore original API key
        if original_key is not None:
            os.environ["OPENAI_API_KEY"] = original_key
        else:
            os.environ.pop("OPENAI_API_KEY", None)


def test_analyzer_handles_invalid_api_key() -> None:
    """Test analyzer handles invalid API keys gracefully."""
    config = {"model": "gpt-4o-mini", "api_key": "invalid-key-12345"}

    analyzer = Analyzer(config)

    content = "Test content"
    context = "High Priority: Testing"

    # Should raise exception for invalid API key
    with pytest.raises(Exception) as exc_info:
        analyzer.analyze(content, context)

    # Error should be wrapped with context
    assert "Failed to analyze content" in str(exc_info.value)


def test_analyzer_with_different_models() -> None:
    """Test analyzer works with different model configurations."""
    # Test with different temperature
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY", ""),
        "temperature": 0.1,  # Very low temperature for consistent results
    }

    analyzer = Analyzer(config)

    # Verify temperature is set
    assert analyzer.temperature == 0.1

    content = "AI development news"
    context = "High Priority: AI"

    result = analyzer.analyze(content, context)

    # Should work with custom temperature
    assert result["priority"] in ["high", "medium", "low"]
    assert isinstance(result["summary"], str)
