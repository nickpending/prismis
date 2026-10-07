"""Integration tests for content-aware summarization against real LLM replies (Tasks 1.1-1.3).

Each test's LLM calls are the production model's real replies, recorded once and
replayed by `local_pipeline_stub` under the `no_network` fixture (`recorded_llm`
marker). Re-record with PRISMIS_RECORD_LLM (see docs/architecture/boundaries.md).

These tests verify:
1. Empty content handling in full pipeline
2. All modes (brief/standard/detailed) return same JSON structure from LLM
"""

from pathlib import Path

import pytest
from conftest import LOCAL_LIGHT_SERVICE, configure_local_services
from prismis_daemon.summarizer import ContentSummarizer

pytestmark = pytest.mark.usefixtures("no_network")


@pytest.fixture
def stub_service(local_pipeline_stub: str, isolated_xdg_env: Path) -> str:
    """The light-service name, pointed at `local_pipeline_stub`."""
    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    return LOCAL_LIGHT_SERVICE


def test_empty_content_does_not_crash() -> None:
    """
    FAILURE: Empty content from failed fetch must not crash system.
    GRACEFUL: Returns None gracefully without API call.

    Needs no recording: the service is never configured, so a call would fail.
    """
    summarizer = ContentSummarizer(LOCAL_LIGHT_SERVICE)

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


@pytest.mark.recorded_llm
def test_all_modes_return_same_json_structure(stub_service: str) -> None:
    """
    INVARIANT: Brief/standard/detailed modes all return same JSON structure.
    BREAKS: Parsing fails if LLM returns different fields for different modes.
    """
    summarizer = ContentSummarizer(stub_service)

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
        assert isinstance(result.quotes, list)
        assert isinstance(result.tools, list)
        assert isinstance(result.urls, list)
        assert isinstance(result.metadata, dict)

    # The mode the code selected is deterministic; the lengths the recorded model
    # wrote for each prompt are not exactly what the prompts ask for (the brief prompt
    # asks 500-800 chars and the production model writes about 1300), so the length
    # bounds are the ones its real replies keep.
    assert brief_result.metadata["summarization_mode"] == "brief"
    assert detailed_result.metadata["summarization_mode"] == "detailed"
    assert standard_result.metadata["summarization_mode"] == "standard"

    assert len(brief_result.reading_summary) < 1500, (
        "Brief mode should produce short reading summary"
    )
    assert len(detailed_result.reading_summary) > 1000, (
        "Detailed mode should produce longer reading summary"
    )
    assert len(standard_result.reading_summary) >= 1000, (
        "Standard mode should produce a comprehensive reading summary"
    )


@pytest.mark.recorded_llm
def test_content_aware_mode_selection_with_real_api(stub_service: str) -> None:
    """
    CONFIDENCE: Verify mode selection works correctly in real pipeline.

    This test demonstrates that:
    1. Reddit <300 words uses brief mode (shorter reading summary)
    2. YouTube >5000 words uses detailed mode (longer reading summary)
    3. Everything else uses standard mode (comprehensive reading summary)
    """
    summarizer = ContentSummarizer(stub_service)

    # Test 1: Brief mode for short Reddit post (299 words in total, the 4-word
    # lead-in included: brief mode is under 300 words)
    words_295 = " ".join(["word"] * 295)
    brief_content = f"TIL an interesting fact. {words_295}"

    brief_result = summarizer.summarize_with_analysis(
        content=brief_content,
        title="Short Reddit TIL",
        url="https://reddit.com/r/todayilearned/1",
        source_type="reddit",
    )

    assert brief_result is not None
    assert brief_result.metadata["summarization_mode"] == "brief"
    # Brief mode produces minimal reading summary
    assert len(brief_result.reading_summary) < 1500, (
        f"Brief mode should produce short summary, got {len(brief_result.reading_summary)} chars"
    )

    # Test 2: Standard mode for 300-word Reddit post (boundary: 3-word lead-in + 297)
    words_297 = " ".join(["word"] * 297)
    standard_reddit_content = f"TIL another fact. {words_297}"

    standard_reddit_result = summarizer.summarize_with_analysis(
        content=standard_reddit_content,
        title="300 Word Reddit Post",
        url="https://reddit.com/r/todayilearned/2",
        source_type="reddit",
    )

    assert standard_reddit_result is not None
    assert standard_reddit_result.metadata["summarization_mode"] == "standard"
    # Standard mode produces comprehensive reading summary (the standard prompt asks
    # for 2000+ chars; the recorded production model writes about 1400)
    assert len(standard_reddit_result.reading_summary) >= 1000, (
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
    assert detailed_result.metadata["summarization_mode"] == "detailed"
    # Detailed mode produces an extensive reading summary (the detailed prompt asks for
    # 20-25% of the original; the recorded production model, given repetitive filler,
    # writes about 1800 chars)
    assert len(detailed_result.reading_summary) > 1000, (
        f"Detailed mode should produce extensive summary, got {len(detailed_result.reading_summary)} chars"
    )
