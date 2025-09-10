"""Unit tests for Storage class validation logic."""

import tempfile
from pathlib import Path
import pytest
from database import init_db
from storage import Storage


def test_add_source_validates_source_type() -> None:
    """Test that add_source validates source_type parameter."""
    # Create temporary test database for validation test
    temp_dir = tempfile.mkdtemp()
    db_path = Path(temp_dir) / "test.db"
    init_db(db_path)

    storage = Storage(db_path)

    # Valid source types should not raise ValueError
    # (We won't actually test these as they require database operations)

    # Invalid source type should raise ValueError
    with pytest.raises(ValueError, match="Invalid source type: invalid"):
        storage.add_source("https://example.com", "invalid", "Test")

    with pytest.raises(ValueError, match="Invalid source type: blog"):
        storage.add_source("https://example.com", "blog", "Test")

    with pytest.raises(ValueError, match="Invalid source type: news"):
        storage.add_source("https://example.com", "news", "Test")

    # Cleanup
    import shutil

    shutil.rmtree(temp_dir, ignore_errors=True)
