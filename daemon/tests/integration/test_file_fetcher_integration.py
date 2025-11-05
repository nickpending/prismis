"""Integration tests for FileFetcher Storage interaction with real SQLite database.

Tests protect critical invariant:
- Storage query returns actual latest version (not oldest/random)

Note: Full HTTP workflow testing would require real HTTP server setup.
Unit tests already cover diff generation logic. This test focuses on the
HIGH RISK invariant: Storage query correctness.
"""

from pathlib import Path
from datetime import datetime, timedelta
import hashlib

from prismis_daemon.storage import Storage


def test_get_latest_content_for_source_returns_actual_latest(test_db: Path) -> None:
    """
    INVARIANT: get_latest_content_for_source returns actual latest entry by fetched_at
    BREAKS: FileFetcher diffs against wrong version if query returns oldest/random

    This is the CRITICAL storage integration that FileFetcher depends on.
    If this query is wrong (bad ORDER BY, wrong filter), change detection breaks completely.
    """
    storage = Storage(test_db)

    # Add file source
    source_id = storage.add_source(
        "https://example.com/CHANGELOG.md", "file", "Test Changelog"
    )

    # Manually insert 3 versions with explicit fetched_at times
    # Simulating 3 fetches over time (v1 oldest, v3 newest)
    base_time = datetime(2024, 1, 1, 12, 0, 0)

    version1_content = "# Changelog\n\nVersion 1.0"
    version1_hash = hashlib.sha256(version1_content.encode()).hexdigest()

    version2_content = "# Changelog\n\nVersion 1.0\nVersion 1.1"
    version2_hash = hashlib.sha256(version2_content.encode()).hexdigest()

    version3_content = "# Changelog\n\nVersion 1.0\nVersion 1.1\nVersion 1.2"
    version3_hash = hashlib.sha256(version3_content.encode()).hexdigest()

    # Insert in order: v1 (oldest) → v2 → v3 (newest)
    for idx, (content, content_hash, time_offset) in enumerate(
        [
            (version1_content, version1_hash, timedelta(hours=0)),  # Oldest
            (version2_content, version2_hash, timedelta(hours=1)),
            (version3_content, version3_hash, timedelta(hours=2)),  # Newest
        ],
        start=1,
    ):
        item_dict = {
            "source_id": source_id,
            "external_id": f"changelog-{content_hash[:8]}",
            "title": f"Changelog v{idx}",
            "url": "https://example.com/CHANGELOG.md",
            "content": f"Diff for v{idx}" if idx > 1 else content,
            "priority": "high",
            "fetched_at": base_time + time_offset,
            "analysis": {
                "content_hash": content_hash,
                "full_text": content,
                "first_fetch": idx == 1,
            },
        }
        content_id, is_new = storage.create_or_update_content(item_dict)
        assert is_new is True, f"Version {idx} should be new"

    # THE CRITICAL TEST: Query must return v3 (newest by fetched_at)
    latest = storage.get_latest_content_for_source(source_id)

    assert latest is not None, "Should return latest entry"

    # MUST be version 3 (most recent fetched_at)
    assert latest["title"] == "Changelog v3", (
        f"Got {latest['title']}, expected Changelog v3"
    )
    assert latest["analysis"]["content_hash"] == version3_hash, "Wrong version hash"
    assert "Version 1.2" in latest["analysis"]["full_text"], "Wrong version content"

    # Verify it's actually the LATEST by time (SQLite returns string)
    expected_time = str(base_time + timedelta(hours=2))
    assert latest["fetched_at"] == expected_time, (
        f"Not the newest entry: got {latest['fetched_at']}, expected {expected_time}"
    )

    # If FileFetcher uses this, it will compare new content against v3
    # If query is broken (returns v1 or v2), diffs will be wrong


def test_get_latest_content_for_source_with_multiple_sources(test_db: Path) -> None:
    """
    INVARIANT: get_latest_content_for_source filters by source_id correctly
    BREAKS: FileFetcher could diff against content from DIFFERENT source

    Ensures query doesn't leak content from other sources.
    """
    storage = Storage(test_db)

    # Add two file sources
    source1_id = storage.add_source("https://example.com/doc1.md", "file", "Doc 1")
    source2_id = storage.add_source("https://example.com/doc2.md", "file", "Doc 2")

    base_time = datetime(2024, 1, 1, 12, 0, 0)

    # Add content to source 1
    item1_dict = {
        "source_id": source1_id,
        "external_id": "doc1-v1",
        "title": "Doc 1 Content",
        "url": "https://example.com/doc1.md",
        "content": "Document 1",
        "priority": "high",
        "fetched_at": base_time,
        "analysis": {"content_hash": "hash1", "full_text": "Document 1"},
    }
    storage.create_or_update_content(item1_dict)

    # Add content to source 2 (newer fetched_at, but different source)
    item2_dict = {
        "source_id": source2_id,
        "external_id": "doc2-v1",
        "title": "Doc 2 Content",
        "url": "https://example.com/doc2.md",
        "content": "Document 2",
        "priority": "high",
        "fetched_at": base_time + timedelta(hours=5),  # Much newer
        "analysis": {"content_hash": "hash2", "full_text": "Document 2"},
    }
    storage.create_or_update_content(item2_dict)

    # Query for source 1 - MUST return source 1 content, not source 2
    latest_source1 = storage.get_latest_content_for_source(source1_id)

    assert latest_source1 is not None
    assert latest_source1["title"] == "Doc 1 Content", "Returned wrong source content"
    assert latest_source1["source_id"] == source1_id, "source_id mismatch"
    assert latest_source1["analysis"]["full_text"] == "Document 1", "Wrong content"

    # Query for source 2
    latest_source2 = storage.get_latest_content_for_source(source2_id)

    assert latest_source2 is not None
    assert latest_source2["title"] == "Doc 2 Content"
    assert latest_source2["source_id"] == source2_id
    assert latest_source2["analysis"]["full_text"] == "Document 2"

    # Sources must not cross-contaminate


def test_get_latest_content_for_source_with_no_previous_entry(test_db: Path) -> None:
    """
    INVARIANT: get_latest_content_for_source returns None when no previous entry exists
    BREAKS: FileFetcher first fetch logic depends on None to detect baseline

    Ensures first fetch (no previous entry) is handled correctly.
    """
    storage = Storage(test_db)

    # Add file source but NO content yet
    source_id = storage.add_source("https://example.com/newfile.md", "file", "New File")

    # Query for latest - should return None (no entries yet)
    latest = storage.get_latest_content_for_source(source_id)

    assert latest is None, "Should return None when no content exists for source"

    # This None triggers FileFetcher's first_fetch logic (stores baseline, no diff)
