"""Unit tests for Storage class deduplication methods."""

import tempfile
from pathlib import Path
import pytest
from database import init_db
from storage import Storage
from models import ContentItem


def test_create_or_update_content_returns_correct_tuple_types() -> None:
    """Test that create_or_update_content returns (str, bool) tuple."""
    # Create temporary test database
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)

    storage = Storage(db_path)

    # Add a source first
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    # Create test content
    content_dict = {
        "source_id": source_id,
        "external_id": "test-123",
        "title": "Test Article",
        "url": "https://example.com/article",
        "content": "Test content",
    }

    # Test return types for new content
    result = storage.create_or_update_content(content_dict)

    # Should return tuple
    assert isinstance(result, tuple)
    assert len(result) == 2

    # Check types
    content_id, is_new = result
    assert isinstance(content_id, str)
    assert isinstance(is_new, bool)
    assert len(content_id) == 36  # UUID string length
    assert is_new is True  # New content

    # Test return types for existing content (update)
    content_dict["summary"] = "Updated summary"
    result2 = storage.create_or_update_content(content_dict)

    content_id2, is_new2 = result2
    assert isinstance(content_id2, str)
    assert isinstance(is_new2, bool)
    assert content_id2 == content_id  # Same UUID
    assert is_new2 is False  # Existing content

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_get_existing_external_ids_returns_set() -> None:
    """Test that get_existing_external_ids returns a set for O(1) lookup."""
    # Create temporary test database
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)

    storage = Storage(db_path)

    # Add a source
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    # Initially should return empty set
    result = storage.get_existing_external_ids(source_id)
    assert isinstance(result, set)
    assert len(result) == 0

    # Add some content
    content1 = {
        "source_id": source_id,
        "external_id": "item-1",
        "title": "Article 1",
        "url": "https://example.com/1",
        "content": "Content 1",
    }
    content2 = {
        "source_id": source_id,
        "external_id": "item-2",
        "title": "Article 2",
        "url": "https://example.com/2",
        "content": "Content 2",
    }

    storage.create_or_update_content(content1)
    storage.create_or_update_content(content2)

    # Should return set with external_ids
    result = storage.get_existing_external_ids(source_id)
    assert isinstance(result, set)
    assert len(result) == 2
    assert "item-1" in result
    assert "item-2" in result

    # Test O(1) lookup performance characteristic
    assert "item-1" in result  # Should be fast set lookup
    assert "nonexistent" not in result

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_get_by_external_id_returns_dict_or_none() -> None:
    """Test that _get_by_external_id returns dict for existing, None for missing."""
    # Create temporary test database
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)

    storage = Storage(db_path)

    # Test with non-existent external_id
    result = storage._get_by_external_id("nonexistent")
    assert result is None

    # Add a source and content
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")

    content_dict = {
        "source_id": source_id,
        "external_id": "test-item",
        "title": "Test Article",
        "url": "https://example.com/article",
        "content": "Test content",
        "summary": "Test summary",
        "priority": "high",
    }

    storage.create_or_update_content(content_dict)

    # Test with existing external_id
    result = storage._get_by_external_id("test-item")
    assert isinstance(result, dict)

    # Check required fields in returned dict
    assert "id" in result
    assert "source_id" in result
    assert "external_id" in result
    assert "title" in result
    assert "url" in result
    assert "content" in result

    # Check values
    assert result["external_id"] == "test-item"
    assert result["title"] == "Test Article"
    assert result["url"] == "https://example.com/article"
    assert result["content"] == "Test content"
    assert result["summary"] == "Test summary"
    assert result["priority"] == "high"

    # Test with different non-existent external_id
    result2 = storage._get_by_external_id("different-nonexistent")
    assert result2 is None

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)
