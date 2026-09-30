"""Unit tests for RSSFetcher logic functions."""

import http.server
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from prismis_daemon.fetchers.rss import RSSFetcher


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
    entry: dict[str, str] = {}

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

    assert parsed_date == datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)


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

    assert parsed_date == datetime(2024, 1, 16, 14, 45, 0, tzinfo=UTC)


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


# ---------------------------------------------------------------------------
# known_readable_ids (SC-4): the fetcher skips trafilatura entirely for an
# entry the orchestrator already has stored readably.
# ---------------------------------------------------------------------------

_FEED_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Skip Known Test Feed</title><link>{base}/</link><description>local</description>
  <item><title>Known Readable Item</title><link>{base}/article</link>
    <description>fallback description text</description>
    <guid isPermaLink="false">known-readable-id</guid></item>
</channel></rss>
"""

_ARTICLE_HTML = (
    b"<!doctype html><html><body><article><h1>Real Article</h1>"
    b"<p>A genuine article body, long enough for trafilatura to extract as the "
    b"main content rather than boilerplate chrome around it.</p></article>"
    b"</body></html>"
)


@pytest.fixture
def rss_skip_known_server() -> Iterator[tuple[str, dict[str, int]]]:
    """A local feed + article server that counts hits per path -- proof that
    `known_readable_ids` skips the article fetch entirely, not merely that the
    result gets discarded downstream."""
    hits: dict[str, int] = {}

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            hits[self.path] = hits.get(self.path, 0) + 1
            if self.path == "/feed.xml":
                base = f"http://{self.headers.get('Host', '127.0.0.1')}"
                body = _FEED_XML.format(base=base).encode()
                self._send(body, "application/rss+xml")
            elif self.path == "/article":
                self._send(_ARTICLE_HTML, "text/html")
            else:
                self.send_error(404)

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_fetch_content_skip_known_readable_ids_skips_extraction(
    rss_skip_known_server: tuple[str, dict[str, int]],
) -> None:
    """
    SC-4: an entry whose external_id is already known-readable never has its
    article fetched -- the orchestrator's dedup filter discards the result
    either way, so extracting it would only be wasted work.
    BREAKS: Threading known_readable_ids through fetch_content's dedup filter
    downstream but not the extraction step above it still fetches every
    already-readable article on every single cycle.
    """
    base_url, hits = rss_skip_known_server
    fetcher = RSSFetcher(max_items=5)
    source = {"url": f"{base_url}/feed.xml", "id": "src-1"}

    items = fetcher.fetch_content(source, known_readable_ids={"known-readable-id"})

    assert len(items) == 1
    assert items[0].content == "fallback description text"
    assert hits.get("/article") is None, (
        "SC-4: a known-readable entry's article must not be fetched at all"
    )


def test_fetch_content_without_skip_still_fetches_the_article(
    rss_skip_known_server: tuple[str, dict[str, int]],
) -> None:
    """Companion to the skip test: without known_readable_ids, the same entry's
    article IS fetched, proving the skip above is meaningful rather than the
    article simply being unreachable in this fixture."""
    base_url, hits = rss_skip_known_server
    fetcher = RSSFetcher(max_items=5)
    source = {"url": f"{base_url}/feed.xml", "id": "src-1"}

    items = fetcher.fetch_content(source)

    assert len(items) == 1
    assert items[0].content is not None
    assert "genuine article body" in items[0].content
    assert hits.get("/article") == 1
