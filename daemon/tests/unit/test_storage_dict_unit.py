"""Unit tests for Storage.add_content dict interface logic."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch
import pytest
from database import init_db
from storage import Storage


def test_dict_to_content_item_conversion() -> None:
    """Test that dict is properly converted to ContentItem with all fields."""
    # Create temporary test database for unit test
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)

    # We'll mock the database operations to test only conversion logic
    storage = Storage(db_path)

    # Mock get_active_sources to return a fake source
    with patch.object(storage, "get_active_sources") as mock_get_sources:
        mock_get_sources.return_value = [{"id": "test-source-id"}]

        # Mock the actual database execution to test conversion logic
        with patch("storage.get_db_connection") as mock_db:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None  # No duplicate
            mock_conn.execute.return_value = mock_cursor
            mock_db.return_value = mock_conn

            # Test dict with all fields
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

            # Call add_content with dict
            storage.add_content(test_dict)

            # Verify the INSERT was called with converted ContentItem fields
            insert_call = mock_conn.execute.call_args_list[-1]
            insert_values = insert_call[0][1]

            # Check that required fields were set
            assert insert_values[2] == "test-123"  # external_id
            assert insert_values[3] == "Test Title"  # title
            assert insert_values[4] == "http://test.com"  # url
            assert insert_values[5] == "Test content"  # content
            assert insert_values[6] == "Test summary"  # summary
            assert insert_values[8] == "high"  # priority

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_dict_without_source_id_uses_first_active_source() -> None:
    """Test that missing source_id is automatically assigned from active sources."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)
    storage = Storage(db_path)

    # Mock get_active_sources to return sources
    with patch.object(storage, "get_active_sources") as mock_get_sources:
        mock_get_sources.return_value = [
            {"id": "source-1", "url": "http://source1.com"},
            {"id": "source-2", "url": "http://source2.com"},
        ]

        with patch("storage.get_db_connection") as mock_db:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.execute.return_value = mock_cursor
            mock_db.return_value = mock_conn

            # Test dict without source_id
            test_dict = {"external_id": "no-source-test", "title": "No Source Test"}

            storage.add_content(test_dict)

            # Verify source_id was set to first active source
            insert_call = mock_conn.execute.call_args_list[-1]
            insert_values = insert_call[0][1]
            assert insert_values[1] == "source-1"  # source_id should be first source

            # Verify get_active_sources was called
            mock_get_sources.assert_called_once()

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_dict_without_source_id_raises_when_no_active_sources() -> None:
    """Test that ValueError is raised when no source_id provided and no active sources."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)
    storage = Storage(db_path)

    # Mock get_active_sources to return empty list
    with patch.object(storage, "get_active_sources") as mock_get_sources:
        mock_get_sources.return_value = []

        # Test dict without source_id
        test_dict = {"external_id": "no-source-test", "title": "No Source Test"}

        # Should raise ValueError with specific message
        with pytest.raises(
            ValueError, match="No source_id provided and no active sources available"
        ):
            storage.add_content(test_dict)

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_dict_with_explicit_source_id_bypasses_lookup() -> None:
    """Test that explicit source_id in dict bypasses active source lookup."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)
    storage = Storage(db_path)

    # Mock get_active_sources - should NOT be called
    with patch.object(storage, "get_active_sources") as mock_get_sources:
        with patch("storage.get_db_connection") as mock_db:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.execute.return_value = mock_cursor
            mock_db.return_value = mock_conn

            # Test dict with explicit source_id
            test_dict = {
                "external_id": "explicit-source-test",
                "title": "Explicit Source Test",
                "source_id": "explicit-source-123",
            }

            storage.add_content(test_dict)

            # Verify the provided source_id was used
            insert_call = mock_conn.execute.call_args_list[-1]
            insert_values = insert_call[0][1]
            assert insert_values[1] == "explicit-source-123"

            # Verify get_active_sources was NOT called
            mock_get_sources.assert_not_called()

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)


def test_dict_optional_fields_handling() -> None:
    """Test that optional fields are properly handled when present or absent."""
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)
    storage = Storage(db_path)

    with patch.object(storage, "get_active_sources") as mock_get_sources:
        mock_get_sources.return_value = [{"id": "test-source"}]

        with patch("storage.get_db_connection") as mock_db:
            mock_conn = Mock()
            mock_cursor = Mock()
            mock_cursor.fetchone.return_value = None
            mock_conn.execute.return_value = mock_cursor
            mock_db.return_value = mock_conn

            # Test dict with minimal fields
            minimal_dict = {"external_id": "minimal-test", "title": "Minimal Test"}

            storage.add_content(minimal_dict)

            # Verify defaults were used for missing fields
            insert_call = mock_conn.execute.call_args_list[-1]
            insert_values = insert_call[0][1]
            assert insert_values[4] == ""  # url defaults to empty string
            assert insert_values[5] == ""  # content defaults to empty string
            assert insert_values[6] is None  # summary is None
            assert insert_values[8] is None  # priority is None

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)
