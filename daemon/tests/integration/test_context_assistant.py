"""Integration tests for Context Assistant - database layer and API integration.

Tests prune protection invariants AND context suggestion API invariants.
"""

import gc
import logging
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app
from prismis_daemon.config import Config
from prismis_daemon.context_analyzer import ContextAnalyzer
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

logger = logging.getLogger(__name__)

# llm_core.complete as imported into a prismis_daemon module -- the one collaborator
# the constitution permits standing in for.
_LLM_COMPLETE_MOCK = (
    "prismis_daemon.context_analyzer.complete"  # claudex-guard: allow-mock
)

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
        content_id = add_new_content(storage, item)
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
        content_id = add_new_content(storage, item)

        # Flag first 2 items via user_feedback='up' (the current flagging mechanism)
        if i < 2:
            storage.update_content_status(content_id, user_feedback="up")
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

    for _i, case in enumerate(["favorited", "flagged", "both", "neither"]):
        item = ContentItem(
            source_id=source_id,
            external_id=f"test-{case}",
            title=f"Article {case}",
            url=f"https://example.com/{case}",
            content="Content",
            priority=None,  # Unprioritized
        )
        content_id = add_new_content(storage, item)

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
        content_id = add_new_content(storage, item)
        storage.flag_interesting(content_id)

    # Capture state before API call
    flagged_before = storage.get_flagged_items()
    flagged_ids_before = {item["id"] for item in flagged_before}
    flagged_titles_before = {item["title"] for item in flagged_before}

    # Make API call (will fail without OpenAI key, but that's OK for this test)
    try:
        api_client.post("/api/context", headers={"X-API-Key": TEST_API_KEY})
        # Response might be 422 (no flagged items if wrong DB) or 500 (LLM error)
        # We don't care - we're testing database integrity
    except Exception as exc:
        logger.debug("Expected API failure during state integrity test: %s", exc)

    # Verify state unchanged after API call
    flagged_after = storage.get_flagged_items()
    flagged_ids_after = {item["id"] for item in flagged_after}
    flagged_titles_after = {item["title"] for item in flagged_after}

    assert flagged_ids_before == flagged_ids_after, "Flagged item IDs changed"
    assert flagged_titles_before == flagged_titles_after, "Flagged item titles changed"
    assert len(flagged_before) == len(flagged_after), "Number of flagged items changed"


def test_INVARIANT_empty_flagged_returns_empty_suggestions(
    test_db: Path, full_config: "Config"
) -> None:
    """
    INVARIANT: Empty flagged items returns empty suggestions without LLM call
    BREAKS: Wasted API costs and unnecessary delays
    """
    # Setup: Empty database (no flagged items)
    Storage(test_db)  # Initialize but don't add any flagged items

    # Get context text from config
    context_text = full_config.context

    # A service name that resolves to no configured service. If
    # analyze_flagged_items reached _call_llm despite the empty list, llm_core.complete
    # would raise "Unknown service" for it and analyze_flagged_items re-raises (it
    # catches and re-raises everything, per its own docstring) -- so this call proves
    # the empty-list short-circuit for real, rather than by patching _call_llm out.
    analyzer = ContextAnalyzer("prismis-no-such-service")

    result = analyzer.analyze_flagged_items([], context_text)

    assert result == {"suggested_topics": []}


def test_INVARIANT_no_credentials_in_errors(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: Error messages never contain API keys or credentials
    BREAKS: Security breach, credential exposure in logs

    Auth and config go through the real Config.from_file(), reading the sealed
    config.toml the isolated_xdg_env fixture writes -- conftest.TEST_API_KEY is the
    real key it authenticates with. Only the LLM call itself is stood in for, since
    that is the one boundary the constitution permits faking.
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
    content_id = add_new_content(storage, item)
    storage.update_content_status(content_id, user_feedback="up")

    # Fake API key that should never appear in error responses
    fake_llm_service_key = "sk-test-SENSITIVE-KEY-12345"

    # Mock LLM call to raise error mentioning the sensitive key
    with patch(
        _LLM_COMPLETE_MOCK,
        side_effect=Exception(f"API call failed with key {fake_llm_service_key}"),
    ):
        # Make API call with the real sealed API key
        response = api_client.post(
            "/api/context", headers={"X-API-Key": TEST_API_KEY}
        )

        # Verify error response
        assert response.status_code == 500

        # Verify sensitive key NOT in response
        response_text = response.text
        response_json = response.json()

        assert fake_llm_service_key not in response_text, (
            "LLM service key found in response body"
        )
        assert fake_llm_service_key not in response_json.get("message", ""), (
            "LLM service key found in error message"
        )
        assert "sk-test" not in response_text, "Partial key found in response"


def test_FAILURE_database_locked_during_get_flagged(test_db: Path) -> None:
    """
    FAILURE: Database locked during get_flagged_items()
    GRACEFUL: Must fail with clear error, not corrupt state

    Drives a real SQLite lock. WAL readers do not block on an ordinary writer (that is
    WAL's whole point) -- proven manually against this schema before writing this test:
    a plain `BEGIN EXCLUSIVE` write left a concurrent get_flagged_items() call
    completely unaffected. `locking_mode=EXCLUSIVE` is what actually forces the
    OS-level exclusive lock a locked-database error needs, and only once every other
    connection to the file (including this test's own setup connection) has actually
    released it -- gc.collect() closes the window between Storage.close() and the
    connection object's real teardown.

    The reader is constructed fresh, after the lock is already held, so it reaches the
    lock through Storage's own default 5000ms busy_timeout (database.py:139) the same
    way a real concurrent caller would -- inescapably real time, since that connection
    does not exist yet for a shorter timeout to be set on.
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
    content_id = add_new_content(storage, item)
    storage.update_content_status(content_id, user_feedback="up")
    storage.close()
    gc.collect()

    # Hold a real, OS-level exclusive lock on the database from a second connection.
    locker = Storage(test_db)
    locker.conn.execute("PRAGMA locking_mode=EXCLUSIVE")
    locker.conn.execute("BEGIN EXCLUSIVE")
    locker.conn.execute(
        "INSERT INTO sources (id, url, type, name) VALUES (?, ?, ?, ?)",
        ("lock-holder", "https://lock-holder.example.com", "rss", "Lock Holder"),
    )

    try:
        with pytest.raises(sqlite3.Error, match="locked"):
            reader = Storage(test_db)
            reader.get_flagged_items()
    finally:
        locker.conn.rollback()
        locker.conn.execute("PRAGMA locking_mode=NORMAL")
        locker.close()

    # Verify database state intact after error
    flagged_after = storage.get_flagged_items()
    assert len(flagged_after) == 1, "Database corrupted by lock error"


def test_FAILURE_malformed_context_md_graceful(
    test_db: Path, full_config: "Config"
) -> None:
    """
    FAILURE: Malformed context.md with problematic patterns
    GRACEFUL: Must not crash, should proceed with empty existing_topics
    """
    # Create analyzer with llm-core service name (new API)
    analyzer = ContextAnalyzer(full_config.llm_light_service)

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
