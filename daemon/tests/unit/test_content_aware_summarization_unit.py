"""Unit tests for content-aware summarization logic (Tasks 1.1-1.3).

These tests protect the invariants identified during implementation:
1. Standard mode is default fallback
2. Boundaries route correctly (299/300, 5000/5001)
3. Empty content handled gracefully
4. Invalid source_type defaults to standard
5. Word count never negative
"""

from prismis_daemon.summarizer import ContentSummarizer


def test_word_count_empty_content() -> None:
    """
    INVARIANT: Empty content returns 0 words, not crash.
    BREAKS: System crashes on failed content fetches if not handled.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Empty string
    assert summarizer._calculate_word_count("") == 0

    # Whitespace only
    assert summarizer._calculate_word_count("   ") == 0
    assert summarizer._calculate_word_count("\n\n\t") == 0

    # None should be handled by caller, but verify we don't crash
    # (the actual summarize_with_analysis checks this before calling)


def test_word_count_normal_content() -> None:
    """Verify word count calculation for normal content."""
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Simple cases
    assert summarizer._calculate_word_count("hello") == 1
    assert summarizer._calculate_word_count("hello world") == 2
    assert summarizer._calculate_word_count("one two three") == 3

    # With extra whitespace
    assert summarizer._calculate_word_count("  hello   world  ") == 2


def test_routing_boundary_values_reddit() -> None:
    """
    INVARIANT: Boundaries route correctly (299 brief, 300 standard for reddit).
    BREAKS: Wrong summary depth - wastes money or provides poor UX.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Reddit < 300 words → brief
    assert summarizer._get_mode_name(299, "reddit") == "brief"

    # Reddit >= 300 words → standard
    assert summarizer._get_mode_name(300, "reddit") == "standard"
    assert summarizer._get_mode_name(301, "reddit") == "standard"


def test_routing_boundary_values_youtube() -> None:
    """
    INVARIANT: Boundaries route correctly (5000 standard, 5001 detailed for youtube).
    BREAKS: Wrong summary depth - wastes money or provides poor UX.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # YouTube <= 5000 words → standard
    assert summarizer._get_mode_name(5000, "youtube") == "standard"
    assert summarizer._get_mode_name(4999, "youtube") == "standard"

    # YouTube > 5000 words → detailed
    assert summarizer._get_mode_name(5001, "youtube") == "detailed"
    assert summarizer._get_mode_name(10000, "youtube") == "detailed"


def test_default_to_standard_for_invalid_source() -> None:
    """
    INVARIANT: Invalid/missing source_type defaults to standard mode.
    BREAKS: Routing failures with unknown source types.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Invalid source types → standard
    assert summarizer._get_mode_name(100, "rss") == "standard"
    assert summarizer._get_mode_name(100, "twitter") == "standard"
    assert summarizer._get_mode_name(100, "unknown") == "standard"
    assert summarizer._get_mode_name(100, "") == "standard"

    # Even with extreme word counts, non-reddit/youtube → standard
    assert summarizer._get_mode_name(10, "rss") == "standard"
    assert summarizer._get_mode_name(10000, "rss") == "standard"


def test_extreme_word_counts_handled() -> None:
    """
    FAILURE: Extreme word counts must not break routing.
    GRACEFUL: System handles 0 to very large word counts.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Zero words
    assert summarizer._get_mode_name(0, "reddit") == "brief"
    assert summarizer._get_mode_name(0, "youtube") == "standard"

    # Very large word counts
    assert summarizer._get_mode_name(100000, "youtube") == "detailed"
    assert summarizer._get_mode_name(100000, "reddit") == "standard"


def test_standard_mode_preserved_from_baseline() -> None:
    """
    INVARIANT: Standard mode is default and unchanged from baseline.
    BREAKS: Existing summarization behavior changes unexpectedly.
    """
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Get all three prompts
    standard_prompt = summarizer._get_system_prompt()
    brief_prompt = summarizer._get_brief_system_prompt()
    detailed_prompt = summarizer._get_detailed_system_prompt()

    # Standard prompt should contain the baseline instruction
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        in standard_prompt
    )

    # Brief prompt should have modified instruction
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        not in brief_prompt
    )
    assert "500-800 chars" in brief_prompt

    # Detailed prompt should have modified instruction
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        not in detailed_prompt
    )
    assert "20-25%" in detailed_prompt

    # All prompts should have the same core structure (JSON, steps, etc.)
    for prompt in [standard_prompt, brief_prompt, detailed_prompt]:
        assert "STEP 1: CREATE SUMMARIES" in prompt
        assert "STEP 2: EXTRACT INSIGHTS" in prompt
        assert "JSON" in prompt
        assert "summary" in prompt
        assert "reading_summary" in prompt


def test_select_system_prompt_routing() -> None:
    """Verify _select_system_prompt routes to correct prompt variant."""
    summarizer = ContentSummarizer({"model": "gpt-4o-mini"})

    # Reddit < 300 → brief
    brief = summarizer._select_system_prompt(299, "reddit")
    assert "500-800 chars" in brief

    # YouTube > 5000 → detailed
    detailed = summarizer._select_system_prompt(5001, "youtube")
    assert "20-25%" in detailed

    # Everything else → standard
    standard = summarizer._select_system_prompt(500, "reddit")
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        in standard
    )

    standard2 = summarizer._select_system_prompt(3000, "youtube")
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        in standard2
    )

    standard3 = summarizer._select_system_prompt(1000, "rss")
    assert (
        "approximately 10-15% of original content length (minimum 2000 chars)"
        in standard3
    )
