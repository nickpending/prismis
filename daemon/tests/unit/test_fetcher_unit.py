"""Unit tests for RSSFetcher logic functions."""

from datetime import datetime
import time

from fetchers.rss import RSSFetcher


def test_get_external_id_with_entry_id() -> None:
    """Test external ID uses entry.id when available."""
    fetcher = RSSFetcher()

    # Create entry dict with id field
    entry = {"id": "https://example.com/entry/123"}

    external_id = fetcher._get_external_id(entry)

    assert external_id == "https://example.com/entry/123"


def test_get_external_id_fallback_to_link_hash() -> None:
    """Test external ID falls back to link hash when no id."""
    fetcher = RSSFetcher()

    # Create entry dict with link but no id
    entry = {"link": "https://example.com/article"}

    external_id = fetcher._get_external_id(entry)

    # Should be first 16 chars of SHA256 hash
    assert len(external_id) == 16
    # Should be consistent for same URL
    assert external_id == fetcher._get_external_id(entry)


def test_get_external_id_fallback_to_title_hash() -> None:
    """Test external ID falls back to title hash as last resort."""
    fetcher = RSSFetcher()

    # Create entry dict with only title
    entry = {"title": "Test Article Title"}

    external_id = fetcher._get_external_id(entry)

    # Should be first 16 chars of SHA256 hash
    assert len(external_id) == 16
    # Should be consistent for same title
    assert external_id == fetcher._get_external_id(entry)


def test_get_external_id_no_data_uses_timestamp() -> None:
    """Test external ID uses timestamp when no data available."""
    fetcher = RSSFetcher()

    # Create empty entry
    entry = {}

    external_id = fetcher._get_external_id(entry)

    # Should generate hash from timestamp
    assert len(external_id) == 16
    # Different calls should produce different IDs (due to time)
    # Note: This could be flaky if executed too fast
    import time

    time.sleep(0.001)
    external_id2 = fetcher._get_external_id({})
    assert external_id != external_id2


def test_parse_published_date_from_published_parsed() -> None:
    """Test date parsing from published_parsed field."""
    fetcher = RSSFetcher()

    # Create simple object with published_parsed attribute
    class Entry:
        def __init__(self):
            # Create time struct for Jan 15, 2024, 10:30:00
            self.published_parsed = time.struct_time((2024, 1, 15, 10, 30, 0, 0, 0, 0))

    entry = Entry()
    parsed_date = fetcher._parse_published_date(entry)

    assert parsed_date == datetime(2024, 1, 15, 10, 30, 0)


def test_parse_published_date_fallback_to_updated() -> None:
    """Test date parsing falls back to updated_parsed."""
    fetcher = RSSFetcher()

    # Create simple object with only updated_parsed
    class Entry:
        def __init__(self):
            self.published_parsed = None
            # Create time struct for Jan 16, 2024, 14:45:00
            self.updated_parsed = time.struct_time((2024, 1, 16, 14, 45, 0, 0, 0, 0))

    entry = Entry()
    parsed_date = fetcher._parse_published_date(entry)

    assert parsed_date == datetime(2024, 1, 16, 14, 45, 0)


def test_parse_published_date_returns_none_when_no_dates() -> None:
    """Test date parsing returns None when no date fields."""
    fetcher = RSSFetcher()

    # Create simple object with no date fields
    class Entry:
        def __init__(self):
            self.published_parsed = None
            self.updated_parsed = None

    entry = Entry()
    parsed_date = fetcher._parse_published_date(entry)

    assert parsed_date is None


def test_parse_published_date_handles_invalid_dates() -> None:
    """Test date parsing handles invalid date structures gracefully."""
    fetcher = RSSFetcher()

    # Create simple object with invalid date structure
    class Entry:
        def __init__(self):
            # Invalid time struct (will cause mktime to fail)
            self.published_parsed = time.struct_time((0, 0, 0, 0, 0, 0, 0, 0, 0))
            self.updated_parsed = None

    entry = Entry()
    parsed_date = fetcher._parse_published_date(entry)

    # Should return None on parse failure
    assert parsed_date is None
