"""Unit tests for Storage.add_content dict interface logic.

Real collaborators throughout: a real Storage against a real sealed test database.
add_content's conversion and source-assignment logic is proven by what actually lands
in the content table, read back through Storage's own public methods -- no patched
connection, no patched get_active_sources.
"""

from pathlib import Path

import pytest

from prismis_daemon.storage import Storage


def test_dict_to_content_item_conversion(test_db: Path) -> None:
    """Test that dict is properly converted to ContentItem with all fields."""
    storage = Storage(test_db)
    storage.add_source("https://example.com/feed", "rss", "Test Source")

    test_dict = {
        "external_id": "test-123",
        "title": "Test Title",
        "url": "http://test.com",
        "content": "Test content",
        "summary": "Test summary",
        "priority": "high",
        "analysis": {"topics": ["test"]},
        "notes": "Test notes",
    }

    content_id = storage.add_content(test_dict)
    assert content_id is not None

    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["external_id"] == "test-123"
    assert stored["title"] == "Test Title"
    assert stored["url"] == "http://test.com"
    assert stored["content"] == "Test content"
    assert stored["summary"] == "Test summary"
    assert stored["priority"] == "high"
    assert stored["analysis"] == {"topics": ["test"]}
    assert stored["notes"] == "Test notes"


def test_dict_without_source_id_uses_first_active_source(test_db: Path) -> None:
    """Test that missing source_id is automatically assigned from active sources."""
    storage = Storage(test_db)
    storage.add_source("http://source1.com", "rss", "Source One")
    storage.add_source("http://source2.com", "rss", "Source Two")

    # add_content's fallback calls the real get_active_sources() and takes its first
    # result -- computed here through the same public method so the assertion tracks
    # the real ordering (get_active_sources sorts by id) instead of insertion order.
    expected_source_id = storage.get_active_sources()[0]["id"]

    test_dict = {"external_id": "no-source-test", "title": "No Source Test"}
    content_id = storage.add_content(test_dict)
    assert content_id is not None

    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["source_id"] == expected_source_id


def test_dict_without_source_id_raises_when_no_active_sources(test_db: Path) -> None:
    """Test that ValueError is raised when no source_id provided and no active sources."""
    storage = Storage(test_db)
    # No sources added at all -- get_active_sources() returns [].

    test_dict = {"external_id": "no-source-test", "title": "No Source Test"}

    with pytest.raises(
        ValueError, match="No source_id provided and no active sources available"
    ):
        storage.add_content(test_dict)


def test_dict_with_explicit_source_id_bypasses_lookup(test_db: Path) -> None:
    """Test that explicit source_id in dict bypasses active source lookup.

    The source is paused (inactive) after creation, so get_active_sources() returns
    []. If add_content's explicit-source_id branch fell through to the active-source
    fallback anyway, this would raise ValueError instead of succeeding -- the same
    failure test_dict_without_source_id_raises_when_no_active_sources proves above.
    """
    storage = Storage(test_db)
    source_id = storage.add_source(
        "https://example.com/paused-feed", "rss", "Paused Source"
    )
    storage.pause_source(source_id)
    assert storage.get_active_sources() == []

    test_dict = {
        "external_id": "explicit-source-test",
        "title": "Explicit Source Test",
        "source_id": source_id,
    }

    content_id = storage.add_content(test_dict)
    assert content_id is not None

    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["source_id"] == source_id


def test_dict_optional_fields_handling(test_db: Path) -> None:
    """Test that optional fields are properly handled when present or absent."""
    storage = Storage(test_db)
    storage.add_source("https://example.com/feed", "rss", "Test Source")

    minimal_dict = {"external_id": "minimal-test", "title": "Minimal Test"}
    content_id = storage.add_content(minimal_dict)
    assert content_id is not None

    stored = storage.get_content_by_id(content_id)
    assert stored is not None
    assert stored["url"] == ""
    assert stored["content"] == ""
    assert stored["summary"] is None
    assert stored["priority"] is None
