"""Integration tests for ContentSummarizer and ContentEvaluator with real LLM API calls."""

import os
import pytest
from summarizer import ContentSummarizer
from evaluator import ContentEvaluator, PriorityLevel


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_summarizer_with_real_llm_extracts_all_fields() -> None:
    """Test ContentSummarizer with real LLM API extracts all analysis fields.

    This test:
    - Initializes ContentSummarizer with real API key
    - Makes actual LLM API call to gpt-4o-mini
    - Verifies all fields extracted (summary, reading_summary, alpha_insights, patterns, entities)
    """
    # Use real API key from environment
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    summarizer = ContentSummarizer(config)

    # Test content about AI that should generate rich analysis
    content = """
    OpenAI has announced GPT-5, their most advanced language model yet. 
    The new model demonstrates remarkable improvements in reasoning, particularly 
    in mathematical problem-solving and logical deduction. Early benchmarks show 
    a 10x improvement in complex reasoning tasks compared to GPT-4.
    
    The model uses a new architecture called "Hierarchical Reasoning Networks" 
    that allows it to break down complex problems into smaller sub-problems, 
    solve them independently, and then synthesize the results. This approach 
    mirrors how human experts tackle difficult challenges.
    
    Additionally, GPT-5 features significantly reduced hallucination rates, 
    achieved through a novel training technique called "Verified Chain-of-Thought" 
    where the model learns to validate its own reasoning steps against a knowledge base.
    
    The implications for scientific research, software development, and education 
    are profound. Researchers are already using early access versions to accelerate 
    drug discovery and climate modeling efforts.
    """

    # Make real LLM API call
    result = summarizer.summarize_with_analysis(
        content=content,
        title="OpenAI Announces GPT-5 with Breakthrough Reasoning",
        url="https://example.com/gpt5-announcement",
        source_type="rss",
    )

    # Verify all fields are present and populated
    assert result is not None

    # Check summary is within length limit
    assert len(result.summary) <= 400
    assert len(result.summary) > 50  # Should have meaningful content

    # Check reading summary is comprehensive
    assert len(result.reading_summary) >= 2000
    assert "##" in result.reading_summary  # Should have markdown headers
    assert "Key Points" in result.reading_summary or "Summary" in result.reading_summary

    # Check alpha insights extracted
    assert len(result.alpha_insights) >= 5  # Should have multiple insights
    assert all(isinstance(insight, str) for insight in result.alpha_insights)

    # Check patterns identified
    assert len(result.patterns) >= 2  # Should identify some patterns
    assert all(isinstance(pattern, str) for pattern in result.patterns)

    # Check entities extracted (exactly 5 most significant)
    assert len(result.entities) == 5
    assert all(isinstance(entity, str) for entity in result.entities)
    # Should include major entities from the content
    assert any(
        "gpt" in entity.lower() or "openai" in entity.lower()
        for entity in result.entities
    )

    # Check metadata
    assert result.metadata["model"] == "gpt-4o-mini"
    assert result.metadata["content_length"] == len(content)


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_evaluator_with_real_llm_high_priority() -> None:
    """Test ContentEvaluator correctly identifies high priority content.

    This test:
    - Initializes ContentEvaluator with real API key
    - Makes actual LLM API call
    - Evaluates AI content against context with AI as high priority
    - Verifies HIGH priority assigned with matched interests
    """
    # Use real API key from environment
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    evaluator = ContentEvaluator(config)

    # Content that should be high priority
    content = """
    Major breakthrough in local LLM technology: Researchers have developed 
    a new quantization technique that allows GPT-4 level models to run on 
    consumer hardware with just 8GB of RAM, while maintaining 95% of the 
    original model's performance.
    """

    # Context with AI/LLM as high priority
    context = """
    ## High Priority Topics
    - AI/LLM breakthroughs, especially local models
    - Quantization and model optimization
    - Making AI accessible on consumer hardware
    
    ## Medium Priority Topics
    - Web development frameworks
    - Database optimization
    
    ## Low Priority Topics
    - Gaming news
    - Social media updates
    
    ## Not Interested
    - Cryptocurrency
    - Celebrity news
    """

    # Make real LLM API call
    result = evaluator.evaluate_content(
        content=content,
        title="Breakthrough in Local LLM Technology",
        url="https://example.com/local-llm",
        context=context,
    )

    # Should identify as high priority
    assert result.priority == PriorityLevel.HIGH

    # Should match relevant interests
    assert len(result.matched_interests) > 0
    assert any(
        "AI" in interest or "LLM" in interest or "local" in interest
        for interest in result.matched_interests
    )

    # Should have reasoning
    assert result.reasoning is not None
    assert len(result.reasoning) > 10


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_evaluator_with_real_llm_low_priority() -> None:
    """Test ContentEvaluator correctly identifies low priority content.

    This test:
    - Uses content about topics in "Not Interested" section
    - Verifies LOW priority assigned
    """
    # Use real API key from environment
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    evaluator = ContentEvaluator(config)

    # Content that should be low priority (crypto - in Not Interested)
    content = """
    Bitcoin reaches new all-time high as institutional investors continue 
    to pour money into cryptocurrency markets. The latest DeFi protocol 
    promises 1000% APY returns through yield farming strategies.
    """

    # Same context as above
    context = """
    ## High Priority Topics
    - AI/LLM breakthroughs
    - Systems programming
    
    ## Medium Priority Topics
    - Web development
    
    ## Low Priority Topics
    - Gaming news
    
    ## Not Interested
    - Cryptocurrency, blockchain, DeFi
    - Celebrity news
    """

    # Make real LLM API call
    result = evaluator.evaluate_content(
        content=content,
        title="Bitcoin Reaches New High",
        url="https://example.com/bitcoin",
        context=context,
    )

    # Should identify as low priority (matches Not Interested)
    assert result.priority == PriorityLevel.LOW

    # Should still have reasoning explaining why
    assert result.reasoning is not None


def test_complete_analysis_pipeline(llm_config, full_config) -> None:
    """Test complete pipeline: summarization followed by evaluation.

    This test:
    - Runs content through summarizer for rich analysis
    - Then evaluates for priority
    - Simulates the actual daemon workflow
    """
    # Use config from fixture (loaded from actual config file)
    summarizer = ContentSummarizer(llm_config)
    evaluator = ContentEvaluator(llm_config)

    # Rust content (should be high priority based on typical context)
    content = """
    Rust 2.0 has been released with groundbreaking memory management improvements.
    The new version introduces "Phantom Ownership", a compile-time mechanism that
    eliminates even more classes of memory bugs while improving performance by 40%.
    
    The borrow checker has been completely rewritten using a new algorithm based on
    linear types, making it both more permissive for valid code and better at
    catching subtle bugs. Error messages now provide automatic fix suggestions
    that work 90% of the time.
    
    WebAssembly compilation is now 3x faster, and the resulting WASM modules are
    50% smaller. This makes Rust even more attractive for browser-based applications
    and edge computing scenarios.
    """

    # Use actual context from config
    context = full_config["context"]

    # Step 1: Summarize and extract insights
    summary_result = summarizer.summarize_with_analysis(
        content=content,
        title="Rust 2.0 Released with Memory Management Breakthrough",
        url="https://example.com/rust-2",
        source_type="rss",
    )

    assert summary_result is not None
    assert len(summary_result.alpha_insights) > 0
    assert any("rust" in entity.lower() for entity in summary_result.entities)

    # Step 2: Evaluate priority
    evaluation = evaluator.evaluate_content(
        content=content,
        title="Rust 2.0 Released with Memory Management Breakthrough",
        url="https://example.com/rust-2",
        context=context,
    )

    # Verify priority was assigned (don't assume HIGH since it depends on actual context)
    assert evaluation.priority in [
        PriorityLevel.HIGH,
        PriorityLevel.MEDIUM,
        PriorityLevel.LOW,
    ]
    # May or may not have matched interests depending on context
    assert isinstance(evaluation.matched_interests, list)

    # Simulate what orchestrator would store
    analysis_json = {
        "reading_summary": summary_result.reading_summary,
        "alpha_insights": summary_result.alpha_insights,
        "patterns": summary_result.patterns,
        "entities": summary_result.entities,
        "matched_interests": evaluation.matched_interests,
        "metadata": summary_result.metadata,
    }

    # Verify we have all the data needed for storage
    assert "reading_summary" in analysis_json
    assert "alpha_insights" in analysis_json
    assert "patterns" in analysis_json
    assert "entities" in analysis_json
    assert "matched_interests" in analysis_json

    # This would go to database with:
    # - summary: summary_result.summary (400 char brief)
    # - analysis: analysis_json (full structured data)
    # - priority: evaluation.priority.value ("high")
