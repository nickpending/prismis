"""Unit tests for FileFetcher pure functions."""

from prismis_daemon.fetchers.file import FileFetcher


def test_generate_external_id_consistent() -> None:
    """Test external ID generation is consistent for same inputs."""
    fetcher = FileFetcher()

    url = "https://example.com/CHANGELOG.md"
    content_hash = "abc123def456"

    # Same inputs should produce same ID
    id1 = fetcher._generate_external_id(url, content_hash)
    id2 = fetcher._generate_external_id(url, content_hash)

    assert id1 == id2
    assert len(id1) == 16  # Truncated SHA256


def test_generate_external_id_unique_for_different_content() -> None:
    """Test external ID changes when content hash changes."""
    fetcher = FileFetcher()

    url = "https://example.com/CHANGELOG.md"

    # Different content hashes should produce different IDs
    id1 = fetcher._generate_external_id(url, "hash1")
    id2 = fetcher._generate_external_id(url, "hash2")

    assert id1 != id2


def test_generate_external_id_unique_for_different_urls() -> None:
    """Test external ID changes when URL changes."""
    fetcher = FileFetcher()

    content_hash = "same_content_hash"

    # Different URLs should produce different IDs
    id1 = fetcher._generate_external_id("https://example.com/file1.md", content_hash)
    id2 = fetcher._generate_external_id("https://example.com/file2.md", content_hash)

    assert id1 != id2


def test_generate_diff_basic() -> None:
    """Test unified diff generation for simple content changes."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2\nLine 3"
    current = "Line 1\nLine 2 modified\nLine 3"
    url = "https://example.com/test.md"

    diff = fetcher._generate_diff(previous, current, url)

    # Should be unified diff format
    assert diff.startswith("---")
    assert "+++" in diff
    assert "-Line 2" in diff
    assert "+Line 2 modified" in diff


def test_generate_diff_addition() -> None:
    """Test diff generation when lines are added."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2"
    current = "Line 1\nLine 2\nLine 3 is new"
    url = "https://example.com/test.md"

    diff = fetcher._generate_diff(previous, current, url)

    assert "+Line 3 is new" in diff


def test_generate_diff_deletion() -> None:
    """Test diff generation when lines are removed."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2\nLine 3"
    current = "Line 1\nLine 3"
    url = "https://example.com/test.md"

    diff = fetcher._generate_diff(previous, current, url)

    assert "-Line 2" in diff


def test_calculate_diff_stats_additions() -> None:
    """Test diff stats calculation for added lines."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2"
    current = "Line 1\nLine 2\nLine 3\nLine 4"

    stats = fetcher._calculate_diff_stats(previous, current)

    assert stats["added_lines"] == 2
    assert stats["removed_lines"] == 0
    assert stats["changed_lines"] == 2


def test_calculate_diff_stats_deletions() -> None:
    """Test diff stats calculation for removed lines."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2\nLine 3\nLine 4"
    current = "Line 1\nLine 2"

    stats = fetcher._calculate_diff_stats(previous, current)

    assert stats["added_lines"] == 0
    assert stats["removed_lines"] == 2
    assert stats["changed_lines"] == 2


def test_calculate_diff_stats_mixed_changes() -> None:
    """Test diff stats calculation for mixed additions and removals."""
    fetcher = FileFetcher()

    previous = "Line 1\nLine 2\nLine 3"
    current = "Line 1\nLine 2 modified\nLine 4"

    stats = fetcher._calculate_diff_stats(previous, current)

    # Should detect changes (removals and additions)
    assert stats["added_lines"] >= 1
    assert stats["removed_lines"] >= 1
    assert stats["changed_lines"] >= 2
