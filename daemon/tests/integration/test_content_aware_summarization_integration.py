"""Integration tests for content-aware summarization with real LLM calls (Tasks 1.1-1.3).

These tests verify:
1. Empty content handling in full pipeline
2. All modes (brief/standard/detailed) return same JSON structure from LLM
"""

import os
import pytest
from prismis_daemon.summarizer import ContentSummarizer


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_empty_content_does_not_crash() -> None:
    """
    FAILURE: Empty content from failed fetch must not crash system.
    GRACEFUL: Returns None gracefully without API call.
    """
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    summarizer = ContentSummarizer(config)

    # Empty content should return None without crashing
    result = summarizer.summarize_with_analysis(
        content="",
        title="Empty Article",
        url="https://example.com/empty",
        source_type="rss",
    )

    assert result is None

    # Whitespace-only content should also return None
    result = summarizer.summarize_with_analysis(
        content="   \n\n\t   ",
        title="Whitespace Article",
        url="https://example.com/whitespace",
        source_type="rss",
    )

    assert result is None


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_all_modes_return_same_json_structure() -> None:
    """
    INVARIANT: Brief/standard/detailed modes all return same JSON structure.
    BREAKS: Parsing fails if LLM returns different fields for different modes.
    """
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    summarizer = ContentSummarizer(config)

    # Short Reddit content for brief mode
    short_content = """
    TIL that SQLite is the most deployed database engine in the world.
    It's embedded in billions of devices including phones, browsers, and OS kernels.
    """

    brief_result = summarizer.summarize_with_analysis(
        content=short_content,
        title="TIL about SQLite deployment",
        url="https://reddit.com/r/todayilearned/123",
        source_type="reddit",  # <300 words triggers brief
    )

    # Long YouTube content for detailed mode
    long_content = " ".join(
        [
            "The history of database systems is fascinating.",
            "Early database systems in the 1960s were hierarchical and network-based.",
            "Then Edgar Codd introduced the relational model in 1970.",
            "SQL became the standard query language in the 1980s.",
            "NoSQL databases emerged in the 2000s for web-scale applications.",
            "Modern databases like PostgreSQL and SQLite power most applications today.",
        ]
        * 500  # Repeat to get >5000 words
    )

    detailed_result = summarizer.summarize_with_analysis(
        content=long_content,
        title="Complete History of Database Systems",
        url="https://youtube.com/@tech-history",
        source_type="youtube",  # >5000 words triggers detailed
    )

    # Medium RSS content for standard mode
    medium_content = (
        """
    PostgreSQL 17 has been released with significant performance improvements.

    The new version includes better query optimization, improved vacuum performance,
    and enhanced JSON support. Parallel query execution is now faster for large datasets.

    New features include incremental backups, better index management, and improved
    replication. The JSON operators have been expanded with better path expressions.

    This release represents months of work from the PostgreSQL community.
    """
        * 20
    )  # Repeat to get ~500 words (standard mode)

    standard_result = summarizer.summarize_with_analysis(
        content=medium_content,
        title="PostgreSQL 17 Released",
        url="https://postgresql.org/blog/release-17",
        source_type="rss",  # Not reddit/youtube, triggers standard
    )

    # All three results should be non-None
    assert brief_result is not None
    assert detailed_result is not None
    assert standard_result is not None

    # All three should have the same fields
    for result in [brief_result, detailed_result, standard_result]:
        assert hasattr(result, "summary")
        assert hasattr(result, "reading_summary")
        assert hasattr(result, "alpha_insights")
        assert hasattr(result, "patterns")
        assert hasattr(result, "entities")
        assert hasattr(result, "quotes")
        assert hasattr(result, "tools")
        assert hasattr(result, "urls")
        assert hasattr(result, "metadata")

    # Verify types are consistent
    for result in [brief_result, detailed_result, standard_result]:
        assert isinstance(result.summary, str)
        assert isinstance(result.reading_summary, str)
        assert isinstance(result.alpha_insights, list)
        assert isinstance(result.patterns, list)
        assert isinstance(result.entities, list)
        assert isinstance(result.quotes, list)
        assert isinstance(result.tools, list)
        assert isinstance(result.urls, list)
        assert isinstance(result.metadata, dict)

    # Verify reading_summary lengths match mode expectations
    # Brief should be shorter
    assert len(brief_result.reading_summary) < 1000, (
        "Brief mode should produce short reading summary"
    )

    # Detailed should be longer (though we can't guarantee exact length)
    # Just verify it has substantial content
    assert len(detailed_result.reading_summary) > 1000, (
        "Detailed mode should produce longer reading summary"
    )

    # Standard should be in the middle range
    assert len(standard_result.reading_summary) >= 2000, (
        "Standard mode should produce comprehensive reading summary"
    )


@pytest.mark.skipif(
    not os.environ.get("OPENAI_API_KEY"),
    reason="Requires OPENAI_API_KEY environment variable",
)
def test_content_aware_mode_selection_with_real_api() -> None:
    """
    CONFIDENCE: Verify mode selection works correctly in real pipeline.

    This test demonstrates that:
    1. Reddit <300 words uses brief mode (shorter reading summary)
    2. YouTube >5000 words uses detailed mode (longer reading summary)
    3. Everything else uses standard mode (comprehensive reading summary)
    """
    config = {
        "model": "gpt-4o-mini",
        "api_key": os.environ.get("OPENAI_API_KEY"),
    }

    summarizer = ContentSummarizer(config)

    # Test 1: Brief mode for short Reddit post (299 words)
    words_299 = " ".join(["word"] * 299)
    brief_content = f"TIL an interesting fact. {words_299}"

    brief_result = summarizer.summarize_with_analysis(
        content=brief_content,
        title="Short Reddit TIL",
        url="https://reddit.com/r/todayilearned/1",
        source_type="reddit",
    )

    assert brief_result is not None
    # Brief mode produces minimal reading summary
    assert len(brief_result.reading_summary) < 1500, (
        f"Brief mode should produce short summary, got {len(brief_result.reading_summary)} chars"
    )

    # Test 2: Standard mode for 300-word Reddit post (boundary)
    words_300 = " ".join(["word"] * 300)
    standard_reddit_content = f"TIL another fact. {words_300}"

    standard_reddit_result = summarizer.summarize_with_analysis(
        content=standard_reddit_content,
        title="300 Word Reddit Post",
        url="https://reddit.com/r/todayilearned/2",
        source_type="reddit",
    )

    assert standard_reddit_result is not None
    # Standard mode produces comprehensive reading summary
    assert len(standard_reddit_result.reading_summary) >= 2000, (
        f"Standard mode should produce comprehensive summary, got {len(standard_reddit_result.reading_summary)} chars"
    )

    # Test 3: Detailed mode for long YouTube transcript (5001 words)
    # Create realistic transcript-style content
    transcript_segment = """
    So today we're going to talk about an interesting topic.
    As you can see here, the research shows significant improvements.
    Let me explain how this works. First, we need to understand the background.
    The history of this technology goes back several decades.
    """
    long_youtube_content = " ".join([transcript_segment] * 500)  # >5000 words

    detailed_result = summarizer.summarize_with_analysis(
        content=long_youtube_content,
        title="Long Tech Explanation Video",
        url="https://youtube.com/@tech/video",
        source_type="youtube",
    )

    assert detailed_result is not None
    # Detailed mode produces extensive reading summary (20-25% of original)
    # With 5000+ words, that's 1000-1250 words = 5000-6250 chars minimum
    assert len(detailed_result.reading_summary) > 3000, (
        f"Detailed mode should produce extensive summary, got {len(detailed_result.reading_summary)} chars"
    )
