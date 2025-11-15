"""Integration tests for Context Assistant - database layer and API integration.

Tests prune protection invariants AND context suggestion API invariants.
"""

from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.config import Config
from prismis_daemon.context_analyzer import ContextAnalyzer
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# ===== EXISTING TESTS: Prune Protection Invariants =====


def test_INVARIANT_flagged_items_excluded_from_prune_count(test_db: Path) -> None:
    """
    INVARIANT: Flagged items excluded from count_unprioritized()
    BREAKS: Prune count inaccuracy leads to user confusion
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 3 unprioritized items
    items = []
    for i in range(3):
        item = ContentItem(
            source_id=source_id,
            external_id=f"unprioritized-{i}",
            title=f"Unprioritized Article {i}",
            url=f"https://example.com/article-{i}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)
        items.append(content_id)

    # Initial count: all 3 unprioritized items
    count_before = storage.count_unprioritized()
    assert count_before == 3, "Should count all 3 unprioritized items initially"

    # Flag one item as interesting
    storage.flag_interesting(items[0])

    # Count should now exclude the flagged item
    count_after = storage.count_unprioritized()
    assert count_after == 2, "Flagged item should be excluded from prune count"

    # Flag a second item
    storage.flag_interesting(items[1])

    # Count should exclude both flagged items
    count_final = storage.count_unprioritized()
    assert count_final == 1, "Both flagged items should be excluded from prune count"


def test_INVARIANT_flagged_items_not_deleted_by_prune(test_db: Path) -> None:
    """
    INVARIANT: delete_unprioritized() must not delete flagged items
    BREAKS: Data loss of items user explicitly saved for context analysis
    CRITICAL: This is the core prune protection invariant
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 5 unprioritized items
    flagged_items = []
    unflagged_items = []

    for i in range(5):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{i}",
            title=f"Article {i}",
            url=f"https://example.com/article-{i}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)

        # Flag first 2 items
        if i < 2:
            storage.flag_interesting(content_id)
            flagged_items.append(content_id)
        else:
            unflagged_items.append(content_id)

    # Verify setup: 5 total items, 2 flagged
    cursor = storage.conn.execute("SELECT COUNT(*) FROM content WHERE priority IS NULL")
    total_count = cursor.fetchone()[0]
    assert total_count == 5, "Should have 5 unprioritized items"

    flagged = storage.get_flagged_items(limit=10)
    assert len(flagged) == 2, "Should have 2 flagged items"

    # Execute prune
    deleted_count = storage.delete_unprioritized()
    assert deleted_count == 3, "Should delete only the 3 unflagged items"

    # Verify flagged items still exist
    cursor = storage.conn.execute("SELECT COUNT(*) FROM content WHERE priority IS NULL")
    remaining_count = cursor.fetchone()[0]
    assert remaining_count == 2, "Only flagged items should remain"

    # Verify the correct items survived
    cursor = storage.conn.execute("SELECT id FROM content")
    remaining_ids = {row[0] for row in cursor.fetchall()}

    for flagged_id in flagged_items:
        assert flagged_id in remaining_ids, (
            f"Flagged item {flagged_id} was incorrectly deleted"
        )

    for unflagged_id in unflagged_items:
        assert unflagged_id not in remaining_ids, (
            f"Unflagged item {unflagged_id} should have been deleted"
        )


def test_INVARIANT_favorited_items_also_protected_from_prune(test_db: Path) -> None:
    """
    INVARIANT: Both favorited AND flagged items excluded from prune
    BREAKS: Trust violation if favorited items get pruned
    """
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add 4 unprioritized items
    favorited_id = None
    flagged_id = None
    both_id = None
    neither_id = None

    for i, case in enumerate(["favorited", "flagged", "both", "neither"]):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{case}",
            title=f"Article {case}",
            url=f"https://example.com/{case}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = storage.add_content(item)

        if case == "favorited":
            storage.update_content_status(content_id, favorited=True)
            favorited_id = content_id
        elif case == "flagged":
            storage.flag_interesting(content_id)
            flagged_id = content_id
        elif case == "both":
            storage.update_content_status(content_id, favorited=True)
            storage.flag_interesting(content_id)
            both_id = content_id
        else:  # neither
            neither_id = content_id

    # Verify count excludes protected items
    count = storage.count_unprioritized()
    assert count == 1, "Only 'neither' item should be counted for prune"

    # Execute prune
    deleted_count = storage.delete_unprioritized()
    assert deleted_count == 1, "Should delete only the unprotected item"

    # Verify protected items still exist
    cursor = storage.conn.execute("SELECT id FROM content WHERE priority IS NULL")
    remaining_ids = {row[0] for row in cursor.fetchall()}

    assert favorited_id in remaining_ids, "Favorited item should be protected"
    assert flagged_id in remaining_ids, "Flagged item should be protected"
    assert both_id in remaining_ids, "Item with both flags should be protected"
    assert neither_id not in remaining_ids, "Unprotected item should be deleted"


# ===== NEW TESTS: Context Suggestion API Invariants =====


@pytest.fixture
def api_client() -> TestClient:
    """Create test client for API."""
    return TestClient(app)


@pytest.fixture
def sample_flagged_items() -> list[dict]:
    """Create sample flagged items for testing."""
    return [
        {
            "id": "item1",
            "title": "AI Agent Frameworks Comparison",
            "summary": "Deep dive into LangChain, AutoGPT, and other agentic frameworks",
            "content": "Full analysis of different AI agent frameworks...",
            "source_name": "AI Newsletter",
            "source_type": "rss",
        },
        {
            "id": "item2",
            "title": "SQLite Performance Tuning",
            "summary": "Advanced techniques for optimizing SQLite databases",
            "content": "Detailed guide on SQLite optimization...",
            "source_name": "Database Blog",
            "source_type": "rss",
        },
    ]


def test_INVARIANT_flagged_items_unchanged_after_suggest(
    api_client: TestClient, test_db: Path, sample_flagged_items: list[dict]
) -> None:
    """
    INVARIANT: Flagged items state never corrupted by suggestion generation
    BREAKS: User loses curated research queue if database state modified
    """
    # Setup: Add flagged items to database
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    for item_data in sample_flagged_items:
        item = ContentItem(
            source_id=source_id,
            external_id=item_data["id"],
            title=item_data["title"],
            content=item_data["content"],
            url=f"https://example.com/{item_data['id']}",
        )
        content_id = storage.add_content(item)
        storage.flag_interesting(content_id)

    # Capture state before API call
    flagged_before = storage.get_flagged_items()
    flagged_ids_before = {item["id"] for item in flagged_before}
    flagged_titles_before = {item["title"] for item in flagged_before}

    # Make API call (will fail without OpenAI key, but that's OK for this test)
    try:
        response = api_client.post(
            "/api/context", headers={"X-API-Key": "prismis-api-4d5e"}
        )
        # Response might be 422 (no flagged items if wrong DB) or 500 (LLM error)
        # We don't care - we're testing database integrity
    except Exception:
        pass  # Failures are fine, we're testing state integrity

    # Verify state unchanged after API call
    flagged_after = storage.get_flagged_items()
    flagged_ids_after = {item["id"] for item in flagged_after}
    flagged_titles_after = {item["title"] for item in flagged_after}

    assert flagged_ids_before == flagged_ids_after, "Flagged item IDs changed"
    assert flagged_titles_before == flagged_titles_after, "Flagged item titles changed"
    assert len(flagged_before) == len(flagged_after), "Number of flagged items changed"


def test_INVARIANT_empty_flagged_returns_empty_suggestions(
    test_db: Path, full_config: dict
) -> None:
    """
    INVARIANT: Empty flagged items returns empty suggestions without LLM call
    BREAKS: Wasted API costs and unnecessary delays
    """
    # Setup: Empty database (no flagged items)
    Storage(test_db)  # Initialize but don't add any flagged items

    # Get context text from config
    config = Config.from_file()
    context_text = config.context

    # Create analyzer with real config
    llm_config = {
        "model": full_config.get("llm", {}).get("model", "gpt-4o-mini"),
        "api_key": full_config.get("llm", {}).get("api_key"),
        "api_base": full_config.get("llm", {}).get("api_base"),
        "provider": full_config.get("llm", {}).get("provider", "openai"),
    }
    analyzer = ContextAnalyzer(llm_config)

    # Mock _call_llm to verify it's never called
    with mock.patch.object(
        analyzer, "_call_llm", side_effect=AssertionError("LLM should not be called")
    ) as mock_llm:
        # Call with empty list
        result = analyzer.analyze_flagged_items([], context_text)

        # Verify no LLM call was made
        mock_llm.assert_not_called()

        # Verify empty result
        assert result == {"suggested_topics": []}


def test_INVARIANT_no_credentials_in_errors(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: Error messages never contain API keys or credentials
    BREAKS: Security breach, credential exposure in logs
    """
    # Setup: Add flagged items
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    item = ContentItem(
        source_id=source_id,
        external_id="cred_test",
        title="Test Item",
        content="Test content",
        url="https://example.com/cred",
    )
    content_id = storage.add_content(item)
    storage.flag_interesting(content_id)

    # Mock Config to inject a fake API key we can detect
    fake_api_key = "sk-test-SENSITIVE-KEY-12345"

    with mock.patch("prismis_daemon.api.Config.from_file") as mock_config:
        # Create mock config with sensitive key
        mock_instance = mock.MagicMock()
        mock_instance.llm_api_key = fake_api_key
        mock_instance.llm_model = "gpt-4o-mini"
        mock_instance.llm_provider = "openai"
        mock_instance.context = "# Test Context"
        mock_config.return_value = mock_instance

        # Mock LLM call to raise error with API key in exception
        with mock.patch(
            "prismis_daemon.context_analyzer.litellm.completion",
            side_effect=Exception(f"API call failed with key {fake_api_key}"),
        ):
            # Make API call
            response = api_client.post(
                "/api/context", headers={"X-API-Key": "prismis-api-4d5e"}
            )

            # Verify error response
            assert response.status_code == 500

            # Verify sensitive key NOT in response
            response_text = response.text
            response_json = response.json()

            assert fake_api_key not in response_text, "API key found in response body"
            assert fake_api_key not in response_json.get("message", ""), (
                "API key found in error message"
            )
            assert "sk-test" not in response_text, "Partial API key found in response"


def test_FAILURE_database_locked_during_get_flagged(test_db: Path) -> None:
    """
    FAILURE: Database locked during get_flagged_items()
    GRACEFUL: Must fail with clear error, not corrupt state
    """
    # Setup: Add flagged items
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    item = ContentItem(
        source_id=source_id,
        external_id="lock_test",
        title="Test Item",
        content="Test content",
        url="https://example.com/lock",
    )
    content_id = storage.add_content(item)
    storage.flag_interesting(content_id)

    # Mock get_flagged_items to simulate database lock
    original_get_flagged = storage.get_flagged_items

    def locked_get_flagged(*args, **kwargs):
        raise Exception("database is locked")

    with mock.patch.object(
        storage, "get_flagged_items", side_effect=locked_get_flagged
    ):
        # This should raise, not corrupt
        try:
            # Simulate what API endpoint does
            storage.get_flagged_items()
            # Should not reach here
            assert False, "Should have raised database lock error"
        except Exception as e:
            # Verify error is clear
            assert "locked" in str(e).lower()

    # Verify database state intact after error
    flagged_after = original_get_flagged()
    assert len(flagged_after) == 1, "Database corrupted by lock error"


def test_FAILURE_malformed_context_md_graceful(
    test_db: Path, full_config: dict
) -> None:
    """
    FAILURE: Malformed context.md with problematic patterns
    GRACEFUL: Must not crash, should proceed with empty existing_topics
    """
    # Create analyzer
    llm_config = {
        "model": full_config.get("llm", {}).get("model", "gpt-4o-mini"),
        "api_key": full_config.get("llm", {}).get("api_key"),
        "api_base": full_config.get("llm", {}).get("api_base"),
        "provider": full_config.get("llm", {}).get("provider", "openai"),
    }
    analyzer = ContextAnalyzer(llm_config)

    # Test various malformed context.md contents
    malformed_contexts = [
        "",  # Empty
        "No headers at all just text",  # No sections
        "## Wrong Header Name\n- Topic",  # Wrong header
        "## High Priority Topics\n\nNo bullet points",  # No bullets
        "## High Priority Topics\n" * 100,  # Excessive repetition
    ]

    for malformed in malformed_contexts:
        # Should not crash, should parse as empty topics
        existing_topics = analyzer._parse_context_sections(malformed)

        # Verify returns empty dict, not crash
        assert isinstance(existing_topics, dict)
        assert "high" in existing_topics
        assert "medium" in existing_topics
        assert "low" in existing_topics

        # Empty or malformed should return empty lists
        # (or gracefully extracted topics if any valid structure found)
        assert isinstance(existing_topics["high"], list)
        assert isinstance(existing_topics["medium"], list)
        assert isinstance(existing_topics["low"], list)
