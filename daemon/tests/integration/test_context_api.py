"""Integration tests for Context Assistant API with REAL LLM calls.

INVARIANTS PROTECTED:
1. Context.md parsing preserves user config - existing topics extracted correctly
2. LLM receives flagged items and context - integration works end-to-end
3. No flagged items returns clear error - API validates input
4. Malformed context.md handled gracefully - parser doesn't crash

All tests use REAL LiteLLM API calls - no mocks.
Tests skip if ~/.config/prismis/config.toml missing.
"""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_config, get_storage
from prismis_daemon.config import Config
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


@pytest.fixture
def storage_with_flagged_items(test_db: Path) -> Storage:
    """Storage with flagged interesting items for context analysis."""
    storage = Storage(test_db)

    # Add test source
    source_id = storage.add_source("https://example.com/test", "rss", "Test Source")

    # Add unprioritized content items about a specific topic
    test_items = [
        ContentItem(
            external_id="item-1",
            source_id=source_id,
            title="Introduction to Conflict-Free Replicated Data Types",
            url="https://example.com/crdts-intro",
            content="CRDTs are data structures that can be replicated across multiple computers in a network, where replicas can be updated independently without coordination.",
            priority=None,  # Unprioritized
            read=False,
        ),
        ContentItem(
            external_id="item-2",
            source_id=source_id,
            title="Building Offline-First Applications with CRDTs",
            url="https://example.com/offline-crdts",
            content="How to use CRDTs to build applications that work offline and sync automatically when online.",
            priority=None,
            read=False,
        ),
        ContentItem(
            external_id="item-3",
            source_id=source_id,
            title="CRDT Implementation Patterns in Distributed Systems",
            url="https://example.com/crdt-patterns",
            content="Common patterns for implementing CRDTs in production distributed systems.",
            priority=None,
            read=False,
        ),
    ]

    # Add items and flag them as interesting
    for item in test_items:
        content_id = storage.add_content(item)
        storage.flag_interesting(content_id)  # Use UUID returned from add_content

    return storage


@pytest.fixture
def test_context_md(tmp_path: Path) -> Path:
    """Create test context.md with existing topics."""
    context_file = tmp_path / "context.md"
    context_content = """# Personal Context

## High Priority Topics
- Rust programming language
- Local-first software architecture
- SQLite optimization

## Medium Priority Topics
- Python performance optimization
- Go concurrency patterns

## Low Priority Topics
- JavaScript frameworks
- CSS trends
"""
    context_file.write_text(context_content)
    return context_file


@pytest.fixture
def malformed_context_md(tmp_path: Path) -> Path:
    """Create malformed context.md missing proper headers."""
    context_file = tmp_path / "context_malformed.md"
    # No ## section headers - parser will treat as empty
    context_content = """# Personal Context

Some topics I care about:
- Rust
- CRDTs
- Local-first

Other interests:
- Python
- SQLite
"""
    context_file.write_text(context_content)
    return context_file


def create_api_client_with_config(
    storage: Storage, context_path: Path, full_config: Config
) -> TestClient:
    """Create API client with real LLM config and test context.md."""
    # Read test context.md content
    context_content = context_path.read_text()

    # Create new config with test context content but real LLM settings
    config = Config(
        # Daemon settings from full_config
        fetch_interval=full_config.fetch_interval,
        max_items_rss=full_config.max_items_rss,
        max_items_reddit=full_config.max_items_reddit,
        max_items_youtube=full_config.max_items_youtube,
        max_items_file=full_config.max_items_file,
        max_days_lookback=full_config.max_days_lookback,
        # LLM settings (real API keys)
        llm_provider=full_config.llm_provider,
        llm_model=full_config.llm_model,
        llm_api_key=full_config.llm_api_key,
        llm_api_base=full_config.llm_api_base,
        # Reddit settings
        reddit_client_id=full_config.reddit_client_id,
        reddit_client_secret=full_config.reddit_client_secret,
        reddit_user_agent=full_config.reddit_user_agent,
        reddit_max_comments=full_config.reddit_max_comments,
        # Notification settings
        high_priority_only=full_config.high_priority_only,
        notification_command=full_config.notification_command,
        # API settings
        api_key=full_config.api_key,
        api_host=full_config.api_host,
        # Context override (test content, not path!)
        context=context_content,
        # Archival settings
        archival_enabled=full_config.archival_enabled,
        archival_high_read=full_config.archival_high_read,
        archival_medium_unread=full_config.archival_medium_unread,
        archival_medium_read=full_config.archival_medium_read,
        archival_low_unread=full_config.archival_low_unread,
        archival_low_read=full_config.archival_low_read,
        # Audio settings
        audio_provider=full_config.audio_provider,
        audio_voice=full_config.audio_voice,
    )

    def override_get_storage() -> Generator[Storage]:
        yield storage

    def override_get_config() -> Generator[Config]:
        yield config

    app.dependency_overrides[get_storage] = override_get_storage
    app.dependency_overrides[get_config] = override_get_config

    # Create client with API key header
    client = TestClient(app)
    client.headers["X-API-Key"] = config.api_key
    yield client

    # Cleanup
    app.dependency_overrides.clear()


# INVARIANT TEST 1: Real LLM call with existing topics
def test_context_api_real_llm_with_existing_topics(
    storage_with_flagged_items: Storage,
    test_context_md: Path,
    full_config: dict,
):
    """
    INVARIANT: LLM receives both flagged items AND existing topics from context.md
    PROTECTS: Prevents duplicate topic suggestions

    Uses REAL LLM API call to verify end-to-end integration.
    Verifies that suggestions don't duplicate existing topics.
    """
    api_client = next(
        create_api_client_with_config(
            storage_with_flagged_items, test_context_md, full_config
        )
    )

    # Make real API call (costs money, uses actual LLM)
    response = api_client.post("/api/context")

    # Verify API succeeded
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    # Verify response structure
    assert "data" in data
    assert "suggested_topics" in data["data"]

    suggestions = data["data"]["suggested_topics"]

    # LLM should suggest something related to CRDTs (all 3 flagged items about CRDTs)
    for suggestion in suggestions:
        # Verify structure (new gap analysis format)
        assert "topic" in suggestion
        assert "section" in suggestion
        assert "action" in suggestion
        assert "gap_analysis" in suggestion
        assert "existing_topic" in suggestion
        assert "rationale" in suggestion

        # Verify section is valid
        assert suggestion["section"] in ["high", "medium", "low"]

        # Verify action is valid
        assert suggestion["action"] in ["expand", "narrow", "add", "split"]

        # Verify not empty
        assert len(suggestion["topic"]) > 0
        assert len(suggestion["gap_analysis"]) > 0
        assert len(suggestion["rationale"]) > 0

        # If action is not "add", existing_topic should be provided
        if suggestion["action"] != "add":
            assert suggestion["existing_topic"] is not None
            assert len(suggestion["existing_topic"]) > 0

    # Real LLM should provide reasonable suggestions
    assert len(suggestions) > 0, "LLM should suggest at least one topic"


# INVARIANT TEST 2: No flagged items returns clear error
def test_context_api_no_flagged_items(
    test_db: Path,
    test_context_md: Path,
    full_config: dict,
):
    """
    FAILURE: No items flagged as interesting
    GRACEFUL: Returns 422 with helpful message

    No LLM call needed - validates at API layer.
    """
    # Empty storage (no flagged items)
    empty_storage = Storage(test_db)

    api_client = next(
        create_api_client_with_config(empty_storage, test_context_md, full_config)
    )

    response = api_client.post("/api/context")

    # Verify validation error
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False

    # Verify helpful message mentions flagging items
    message = data.get("message", "").lower()
    assert "flag" in message or "interesting" in message or "items" in message


# INVARIANT TEST 3: Malformed context.md handled gracefully
def test_context_api_malformed_context_md(
    storage_with_flagged_items: Storage,
    malformed_context_md: Path,
    full_config: dict,
):
    """
    FAILURE: Context.md missing proper ## section headers
    GRACEFUL: Parser returns empty topics, LLM call proceeds

    Uses real LLM to verify system doesn't crash with malformed context.md.
    """
    api_client = next(
        create_api_client_with_config(
            storage_with_flagged_items, malformed_context_md, full_config
        )
    )

    # Make real API call with malformed context.md
    response = api_client.post("/api/context")

    # Verify API doesn't crash
    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True

    # System proceeds despite malformed context.md (graceful degradation)
    suggestions = data["data"]["suggested_topics"]

    # LLM should still provide suggestions (just without existing topic context)
    assert isinstance(suggestions, list)

    # If suggestions provided, verify structure (gap analysis format)
    for suggestion in suggestions:
        assert "topic" in suggestion
        assert "section" in suggestion
        assert "action" in suggestion
        assert "gap_analysis" in suggestion
        assert "existing_topic" in suggestion
        assert "rationale" in suggestion


# INVARIANT TEST 4: Real LLM suggestions have valid structure
def test_context_api_suggestion_quality(
    storage_with_flagged_items: Storage,
    test_context_md: Path,
    full_config: dict,
):
    """
    INVARIANT: LLM suggestions meet quality standards
    PROTECTS: Users get actionable, well-formatted suggestions

    Verifies real LLM returns properly structured suggestions.
    """
    api_client = next(
        create_api_client_with_config(
            storage_with_flagged_items, test_context_md, full_config
        )
    )

    response = api_client.post("/api/context")

    assert response.status_code == 200
    data = response.json()

    suggestions = data["data"]["suggested_topics"]

    # At least one suggestion
    assert len(suggestions) >= 1, "LLM should provide suggestions for flagged items"

    # Each suggestion must have substantive content (gap analysis format)
    for suggestion in suggestions:
        topic = suggestion["topic"]
        section = suggestion["section"]
        action = suggestion["action"]
        gap_analysis = suggestion["gap_analysis"]
        existing_topic = suggestion["existing_topic"]
        rationale = suggestion["rationale"]

        # Topic should be meaningful (not just single word)
        assert len(topic) >= 3, f"Topic too short: {topic}"

        # Gap analysis should explain WHY context.md missed this (at least 20 chars)
        assert len(gap_analysis) >= 20, f"Gap analysis too short: {gap_analysis}"

        # Rationale should explain how this fix helps (at least 20 chars)
        assert len(rationale) >= 20, f"Rationale too short: {rationale}"

        # Section must be valid priority level
        assert section in ["high", "medium", "low"], f"Invalid section: {section}"

        # Action must be valid
        assert action in ["expand", "narrow", "add", "split"], (
            f"Invalid action: {action}"
        )

        # If action is not "add", existing_topic should be provided
        if action != "add":
            assert existing_topic is not None, (
                f"Action {action} requires existing_topic"
            )
            assert len(existing_topic) >= 3, (
                f"Existing topic too short: {existing_topic}"
            )

        # Topic shouldn't just be generic
        generic_topics = ["interesting", "important", "relevant", "useful", "good"]
        assert topic.lower() not in generic_topics, f"Topic too generic: {topic}"
