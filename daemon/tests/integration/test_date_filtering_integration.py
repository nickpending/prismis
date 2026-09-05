"""Integration tests for date filtering with real feeds."""

import http.server
import threading
import time
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from prismis_daemon.fetchers.rss import RSSFetcher
from conftest import make_config


# A syntactically valid feed, so a fetch that is NOT cut short by a deadline succeeds.
# An empty or malformed response would make the fetch fail for its own reasons and the
# timeout assertion would hold with the timeout removed — which is what made the first
# version of this fixture mutation-insensitive.
_VALID_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Slow Feed</title><link>http://127.0.0.1/</link><description>d</description>
  <item><title>An item</title><link>http://127.0.0.1/item</link>
        <description>body</description></item>
</channel></rss>"""

SLOW_FEED_DELAY_SECONDS = 3


def _dated_feed(recent_days: list[int], old_days: list[int]) -> str:
    """Build a feed whose items sit a known number of days in the past."""
    from email.utils import format_datetime

    now = datetime.now(timezone.utc)
    items = []
    for tag, offsets in (("recent", recent_days), ("old", old_days)):
        for d in offsets:
            when = format_datetime(now - timedelta(days=d))
            items.append(
                f"<item><title>{tag} item {d}d</title>"
                f"<link>http://127.0.0.1/{tag}-{d}</link>"
                f"<description>body</description>"
                f"<pubDate>{when}</pubDate></item>"
            )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0"><channel>'
        "<title>Dated Feed</title><link>http://127.0.0.1/</link>"
        "<description>d</description>" + "".join(items) + "</channel></rss>"
    )


@pytest.fixture
def dated_feed_url() -> Iterator[str]:
    """A local feed carrying items both inside and well outside a 7-day window.

    Previously this test fetched https://simonwillison.net/atom/everything/ on every gate
    run, so the result depended on a third party's publishing cadence — and its `except`
    only skipped on network-shaped errors, so an empty or stale feed surfaced as a hard
    failure. Serving known dates locally makes the date filter the only variable.
    """
    body = _dated_feed(recent_days=[1, 3], old_days=[30, 120]).encode()

    class FeedHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # BaseHTTPRequestHandler dispatches on this name
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FeedHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/feed.xml"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def slow_feed_url() -> Iterator[str]:
    """A local feed URL that answers correctly, but only after a delay.

    Timeout behaviour must not depend on a third party staying slow, so the delay is
    produced here rather than observed from the network. The response is a well-formed
    feed: with the fetcher's deadline removed the fetch SUCCEEDS, which is what makes the
    timeout assertion mutation-sensitive instead of passing for any failure at all.
    """

    class SlowHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # BaseHTTPRequestHandler dispatches on this name
            time.sleep(SLOW_FEED_DELAY_SECONDS)
            body = _VALID_FEED.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass  # keep pytest output clean

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SlowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/feed.xml"
    finally:
        server.shutdown()
        server.server_close()


def test_date_filtering_prevents_old_content(dated_feed_url: str) -> None:
    """
    INVARIANT: NO content older than max_days_lookback ever fetched
    BREAKS: Could cost hundreds in API charges if violated

    The feed is served locally and carries items at 1, 3, 30 and 120 days old, so the
    7-day window must admit exactly the first two. Removing the cutoff comparison in
    RSSFetcher lets the 30- and 120-day items through and turns this red.
    """
    config = make_config(max_days_lookback=7, max_items_rss=10)
    rss_fetcher = RSSFetcher(config=config)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)

    items = rss_fetcher.fetch_content({"url": dated_feed_url, "id": "test-source-id"})

    # CRITICAL INVARIANT: NO items older than cutoff
    old_items = [
        i for i in items if i.published_at and i.published_at < cutoff_date
    ]
    assert not old_items, (
        f"Found {len(old_items)} items older than {config.max_days_lookback} days "
        "- API cost protection FAILED"
    )

    # The feed carries two items inside the window; both must survive. Asserting the
    # exact count is what makes the filter's absence visible — a bare `len(items) > 0`
    # would still hold with every old item let through.
    assert len(items) == 2, f"expected the 2 in-window items, got {len(items)}"
    assert all(i.published_at is not None for i in items), (
        "every item in this feed carries a pubDate"
    )
    assert all(i.published_at.tzinfo is not None for i in items), (
        "dates must be timezone-aware"
    )


def test_network_timeout_graceful(slow_feed_url: str) -> None:
    """
    FAILURE MODE: Network timeout during fetch
    GRACEFUL: System continues, logs error, doesn't crash

    Served by a local socket that holds the request open past the fetcher's deadline.
    This previously pointed at https://httpstat.us/200?sleep=5000 and asserted that the
    request would time out — which made the outcome a property of a third party's current
    behaviour rather than of this code. httpstat.us now answers immediately with an HTML
    page, so no timeout occurred and `pytest.raises` failed with DID NOT RAISE.
    """
    # Use config with very short timeout to force failure
    config = make_config(max_days_lookback=7)
    rss_fetcher = RSSFetcher(config=config, timeout=1)  # 1 second timeout

    test_source = {
        "url": slow_feed_url,
        "id": "test-timeout-source",
    }

    # Should raise exception but not crash the process
    with pytest.raises(Exception) as exc_info:
        rss_fetcher.fetch_content(test_source)

    # The wrapper prefix alone proves nothing: rss.py wraps EVERY exception in
    # "Failed to fetch RSS feed", so asserting on it passes for a DNS error, a refused
    # connection or a malformed body just as readily as for the timeout under test.
    # Assert on the chained cause's type, which only the deadline produces.
    cause = exc_info.value.__cause__
    assert isinstance(cause, httpx.TimeoutException), (
        f"expected the failure to be a timeout, got {type(cause).__name__}: {cause}"
    )

    # And that the wrapper still carries the context a caller needs.
    error_msg = str(exc_info.value)
    assert "Failed to fetch RSS feed" in error_msg, (
        "Error should be wrapped with context"
    )
    assert test_source["url"] in error_msg, "Error should include source URL"
