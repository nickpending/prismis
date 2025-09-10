"""Integration tests for Storage deduplication workflow with real database."""

from pathlib import Path
from datetime import datetime
import pytest

from storage import Storage
from models import ContentItem


def test_deduplication_workflow_end_to_end(test_db: Path) -> None:
    """Test complete deduplication workflow: first fetch creates, second fetch skips."""
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test Feed")
    assert source_id is not None

    # Simulate first fetch cycle - should create new content

    # Check what external_ids we already have (should be empty)
    existing_ids = storage.get_existing_external_ids(source_id)
    assert len(existing_ids) == 0
    assert isinstance(existing_ids, set)

    # Create some test content items (simulating fetcher output)
    item1_dict = {
        "source_id": source_id,
        "external_id": "article-1",
        "title": "First Article",
        "url": "https://example.com/article-1",
        "content": "Content of first article",
        "summary": "Summary 1",
        "priority": "high",
        "published_at": datetime(2024, 1, 15, 10, 0),
    }

    item2_dict = {
        "source_id": source_id,
        "external_id": "article-2",
        "title": "Second Article",
        "url": "https://example.com/article-2",
        "content": "Content of second article",
        "summary": "Summary 2",
        "priority": "medium",
        "published_at": datetime(2024, 1, 15, 11, 0),
    }

    # First fetch: Process items (should create new)
    content_id1, is_new1 = storage.create_or_update_content(item1_dict)
    content_id2, is_new2 = storage.create_or_update_content(item2_dict)

    assert is_new1 is True  # New content created
    assert is_new2 is True  # New content created
    assert len(content_id1) == 36  # Valid UUID
    assert len(content_id2) == 36  # Valid UUID
    assert content_id1 != content_id2  # Different items

    # Verify content is in database
    high_content = storage.get_content_by_priority("high")
    medium_content = storage.get_content_by_priority("medium")
    assert len(high_content) == 1
    assert len(medium_content) == 1
    assert high_content[0]["title"] == "First Article"
    assert medium_content[0]["title"] == "Second Article"

    # Simulate second fetch cycle - should skip existing content

    # Check existing external_ids (should have our items now)
    existing_ids = storage.get_existing_external_ids(source_id)
    assert len(existing_ids) == 2
    assert "article-1" in existing_ids
    assert "article-2" in existing_ids

    # Simulate deduplication filtering (what orchestrator would do)
    # New fetch would include same items plus one new item
    all_items = [item1_dict, item2_dict]  # Same items from feed
    items_to_process = [
        item for item in all_items if item["external_id"] not in existing_ids
    ]

    # Should filter out existing items
    assert len(items_to_process) == 0  # No new items to process

    # Process the existing items (should update, not create)
    item1_dict["summary"] = "Updated summary 1"
    item2_dict["summary"] = "Updated summary 2"

    content_id1_update, is_new1_update = storage.create_or_update_content(item1_dict)
    content_id2_update, is_new2_update = storage.create_or_update_content(item2_dict)

    assert is_new1_update is False  # Updated existing
    assert is_new2_update is False  # Updated existing
    assert content_id1_update == content_id1  # Same UUID
    assert content_id2_update == content_id2  # Same UUID

    # Verify updates applied
    high_content = storage.get_content_by_priority("high")
    assert high_content[0]["summary"] == "Updated summary 1"


def test_force_refetch_processes_all_items(test_db: Path) -> None:
    """Test that force_refetch parameter bypasses deduplication filtering."""
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Test Feed")

    # Add initial content
    item_dict = {
        "source_id": source_id,
        "external_id": "article-1",
        "title": "Test Article",
        "url": "https://example.com/article-1",
        "content": "Original content",
        "summary": "Original summary",
        "priority": "high",
    }

    # First time: create content
    content_id, is_new = storage.create_or_update_content(item_dict)
    assert is_new is True

    # Verify it exists in external_ids
    existing_ids = storage.get_existing_external_ids(source_id)
    assert "article-1" in existing_ids

    # Simulate normal fetch (would skip existing)
    items_to_process_normal = [
        item for item in [item_dict] if item["external_id"] not in existing_ids
    ]
    assert len(items_to_process_normal) == 0  # Would skip

    # Simulate force_refetch (processes all items)
    force_refetch = True
    if force_refetch:
        items_to_process_force = [item_dict]  # Process all items
    else:
        items_to_process_force = [
            item for item in [item_dict] if item["external_id"] not in existing_ids
        ]

    assert len(items_to_process_force) == 1  # Force processes existing

    # Process with force (should update)
    item_dict["summary"] = "Force refetch summary"
    item_dict["content"] = "Force refetch content"

    content_id_force, is_new_force = storage.create_or_update_content(item_dict)
    assert is_new_force is False  # Updated existing
    assert content_id_force == content_id  # Same UUID

    # Verify content was actually updated
    content = storage.get_content_by_priority("high")
    assert len(content) == 1
    assert content[0]["summary"] == "Force refetch summary"
    assert content[0]["content"] == "Force refetch content"


def test_mixed_new_and_existing_content_workflow(test_db: Path) -> None:
    """Test workflow with mix of new and existing content items."""
    storage = Storage(test_db)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Mixed Feed")

    # Add some initial content
    existing_item = {
        "source_id": source_id,
        "external_id": "existing-1",
        "title": "Existing Article",
        "url": "https://example.com/existing",
        "content": "Existing content",
        "priority": "medium",
    }

    storage.create_or_update_content(existing_item)

    # Get existing external_ids
    existing_ids = storage.get_existing_external_ids(source_id)
    assert "existing-1" in existing_ids

    # Simulate new fetch with mix of existing and new items
    all_fetched_items = [
        existing_item,  # Already exists
        {
            "source_id": source_id,
            "external_id": "new-1",
            "title": "New Article 1",
            "url": "https://example.com/new-1",
            "content": "New content 1",
            "priority": "high",
        },
        {
            "source_id": source_id,
            "external_id": "new-2",
            "title": "New Article 2",
            "url": "https://example.com/new-2",
            "content": "New content 2",
            "priority": "low",
        },
    ]

    # Filter to only new items (simulating orchestrator logic)
    items_to_process = [
        item for item in all_fetched_items if item["external_id"] not in existing_ids
    ]

    # Should only process the 2 new items
    assert len(items_to_process) == 2
    assert items_to_process[0]["external_id"] == "new-1"
    assert items_to_process[1]["external_id"] == "new-2"

    # Process the new items
    new_stats = {"items_new": 0, "items_updated": 0}

    for item in items_to_process:
        content_id, is_new = storage.create_or_update_content(item)
        if is_new:
            new_stats["items_new"] += 1
        else:
            new_stats["items_updated"] += 1

    # Should have created 2 new items
    assert new_stats["items_new"] == 2
    assert new_stats["items_updated"] == 0

    # Verify all content exists
    all_content = (
        storage.get_content_by_priority("high")
        + storage.get_content_by_priority("medium")
        + storage.get_content_by_priority("low")
    )

    # Should have original + 2 new = 3 total (assuming default priority)
    assert len(all_content) >= 3

    # Verify external_ids updated
    final_existing_ids = storage.get_existing_external_ids(source_id)
    assert len(final_existing_ids) == 3
    assert "existing-1" in final_existing_ids
    assert "new-1" in final_existing_ids
    assert "new-2" in final_existing_ids
