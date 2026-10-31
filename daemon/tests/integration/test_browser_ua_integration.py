"""A source marked `naughty` is requested with the browser user-agent on every path.

Invariant protected:
  - every request prismis makes for a marked source -- add-time validation, the RSS
    feed GET, each article extraction, a file source's GET, a Reddit post's linked
    article, and a refetched RSS row -- carries `_BROWSER_USER_AGENT`; the same
    requests for an unmarked source carry the identifiers they carried before the
    flag existed

Success criteria covered: C1, C2.

The site is a real local HTTP server that answers 403 to any User-Agent containing
'prismis' or 'httpx' and logs the User-Agent of every request. Real API, real
validator, real Storage, real orchestrator and fetchers; the LLM is `local_pipeline_stub`
and Reddit's API is a hand-written client handing over one stubbed submission, the
two collaborators the constitution permits standing in for.
"""

from __future__ import annotations

import http.server
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import TEST_API_KEY, configure_local_services, make_config
from fixtures.reddit_mocks import create_link_post_mock
from prismis_daemon.api import app
from prismis_daemon.article_extractor import _BROWSER_USER_AGENT, _USER_AGENT
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.fetchers.file import FileFetcher
from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.models import ContentItem
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.readability import RSS_NO_CONTENT_FALLBACK
from prismis_daemon.refetch import run_refetch
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

_HEADERS = {"X-API-Key": TEST_API_KEY}

# What the feed GET and the validator sent before the flag existed: httpx's own default
# and the validator's own identifier. The article fetch's is `_USER_AGENT`.
_HTTPX_DEFAULT_PREFIX = "python-httpx/"
_VALIDATOR_UA = "Prismis/1.0 (Content Aggregator)"

_ARTICLE_PHRASE = "The gated article body only a browser ever receives"
_ARTICLE_HTML = (
    "<!doctype html><html><body><article><h1>Gated</h1><p>"
    + _ARTICLE_PHRASE
    + ", long enough for trafilatura's extraction heuristics to take it as the main"
    " content of the page rather than boilerplate around it. A second sentence keeps"
    " the paragraph well past any minimum length the extractor or the readability"
    " check applies to an article.</p></article></body></html>"
).encode()
_TEASER = "Short teaser."
_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Gated Feed</title><link>{base}/</link><description>gated</description>
  <item><title>First</title><link>{base}/article/1</link>
    <description>{teaser}</description><guid>gated-1</guid></item>
  <item><title>Second</title><link>{base}/article/2</link>
    <description>{teaser}</description><guid>gated-2</guid></item>
</channel></rss>
"""
_NOTES = b"# Changelog\n\n- 1.0: first release\n"


class _GatedSite:
    """403 to a non-browser User-Agent, 200 otherwise; logs (path, User-Agent).

    `/open-feed.xml` is the one ungated path: it serves the same feed to anyone, so an
    unmarked source's items exist and their article requests can be observed.
    """

    def __init__(self) -> None:
        self.log: list[tuple[str, str]] = []
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:
                return

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:
                ua = self.headers.get("User-Agent", "")
                outer.log.append((self.path, ua))
                path = self.path.split("?", 1)[0]
                base = f"http://{self.headers.get('Host')}"
                feed = _FEED.format(base=base, teaser=_TEASER).encode()
                if path == "/open-feed.xml":
                    self._send(200, feed, "application/rss+xml")
                elif "prismis" in ua.lower() or "httpx" in ua.lower():
                    self._send(403, b"Forbidden", "text/plain")
                elif path == "/feed.xml":
                    self._send(200, feed, "application/rss+xml")
                elif path.startswith("/article/"):
                    self._send(200, _ARTICLE_HTML, "text/html")
                elif path == "/notes.md":
                    self._send(200, _NOTES, "text/markdown")
                else:
                    self._send(404, b"Not Found", "text/plain")

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address[0], self._server.server_address[1]
        assert isinstance(host, str)
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def agents(self, prefix: str) -> list[str]:
        """The User-Agent of every logged request whose path starts with `prefix`."""
        return [ua for path, ua in self.log if path.startswith(prefix)]

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def site() -> Iterator[_GatedSite]:
    server = _GatedSite()
    try:
        yield server
    finally:
        server.shutdown()


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


class _NullFetcher:
    def fetch_content(
        self, source: dict[str, Any], known_readable_ids: set[str] | None = None
    ) -> list[ContentItem]:
        raise AssertionError(f"no {source['type']} source is configured here")


def _config(stub_url: str) -> Config:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), stub_url)
    return Config.from_file()


def _orchestrator(
    config: Config, storage: Storage, file_fetcher: FileFetcher | None = None
) -> DaemonOrchestrator:
    return DaemonOrchestrator(
        storage=storage,
        rss_fetcher=RSSFetcher(config=config),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=file_fetcher or _NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        config=config,
    )


def _stored_contents(storage: Storage, source_id: str) -> list[str]:
    rows = storage.conn.execute(
        "SELECT content FROM content WHERE source_id = ? ORDER BY external_id",
        (source_id,),
    ).fetchall()
    return [row["content"] for row in rows]


def _source(storage: Storage, source_id: str) -> dict[str, Any]:
    (source,) = [s for s in storage.get_active_sources() if s["id"] == source_id]
    return source


# --- C1: add-time validation, the feed GET and every article GET ----------------------


def test_a_marked_rss_source_is_added_and_fetched_with_the_browser_user_agent(
    test_db: Path, local_pipeline_stub: str, site: _GatedSite
) -> None:
    """
    INVARIANT: the same feed, behind a site that refuses prismis, is refused when added
               unmarked and accepted when added with naughty=true; one cycle then stores
               its items with the extracted article text, every request carrying the
               browser string
    BREAKS: a source behind a UA filter cannot be added, or its items arrive with only
            the feed's teaser because one of the three requests still says prismis
    """
    config = _config(local_pipeline_stub)
    client = TestClient(app)
    feed_url = f"{site.base_url}/feed.xml"

    refused = client.post(
        "/api/sources", json={"url": feed_url, "type": "rss"}, headers=_HEADERS
    )
    assert refused.status_code == 422, refused.text
    assert "HTTP 403" in refused.json()["message"], refused.text
    assert site.agents("/feed.xml") == [_VALIDATOR_UA]

    site.log.clear()
    added = client.post(
        "/api/sources",
        json={"url": feed_url, "type": "rss", "naughty": True},
        headers=_HEADERS,
    )
    assert added.status_code == 200, added.text
    assert site.agents("/feed.xml") == [_BROWSER_USER_AGENT]

    site.log.clear()
    storage = Storage(test_db)
    source_id = added.json()["data"]["id"]
    _orchestrator(config, storage).run_once()

    contents = _stored_contents(storage, source_id)
    assert len(contents) == 2, contents
    assert all(_ARTICLE_PHRASE in c for c in contents), contents
    assert site.agents("/feed.xml") == [_BROWSER_USER_AGENT]
    assert site.agents("/article/") == [_BROWSER_USER_AGENT] * 2


def test_an_unmarked_rss_source_is_fetched_with_todays_identifiers(
    test_db: Path, local_pipeline_stub: str, site: _GatedSite
) -> None:
    """
    INVARIANT: an unmarked source's feed GET carries httpx's default and each article
               GET carries prismis's own identifier, exactly as before the flag existed;
               the refused articles leave the feed's own text stored
    BREAKS: the flag leaks onto every source, so the daemon impersonates a browser for
            sites that never asked for it
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source(f"{site.base_url}/open-feed.xml", "rss", "Open")

    _orchestrator(config, storage).run_once()

    (feed_ua,) = site.agents("/open-feed.xml")
    assert feed_ua.startswith(_HTTPX_DEFAULT_PREFIX), feed_ua
    assert site.agents("/article/") == [_USER_AGENT] * 2
    contents = _stored_contents(storage, source_id)
    assert len(contents) == 2, contents
    assert not any(_ARTICLE_PHRASE in c for c in contents), contents


def test_a_refused_feed_get_is_recorded_as_a_fetch_failure(
    test_db: Path, local_pipeline_stub: str, site: _GatedSite
) -> None:
    """
    INVARIANT: a feed GET the site answers with 403 fails the source's cycle, counted in
               error_count with the status in last_error, and stores nothing
    BREAKS: the 403 body parses as an empty feed, so a source the site refuses reads as
            a healthy feed with nothing new, every cycle, and is never deactivated
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source(f"{site.base_url}/feed.xml", "rss", "Gated")

    _orchestrator(config, storage).run_once()

    (row,) = [s for s in storage.get_all_sources() if s["id"] == source_id]
    assert row["error_count"] == 1, row
    assert "403" in (row["last_error"] or ""), row
    assert row["last_fetched_at"] is None, row
    assert _stored_contents(storage, source_id) == []
    assert site.agents("/article/") == []


# --- C2: a file source, a Reddit link post, a refetched RSS row -----------------------


def test_a_marked_file_source_get_carries_the_browser_user_agent(
    test_db: Path, site: _GatedSite
) -> None:
    """
    INVARIANT: a marked file source's GET carries the browser string and its file is
               read
    BREAKS: a marked CHANGELOG behind a UA filter never produces an entry
    """
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{site.base_url}/notes.md", "file", "Notes", naughty=True
    )
    fetcher = FileFetcher(config=make_config(), storage=storage)

    fetched = fetcher.fetch_content(_source(storage, source_id))

    assert len(fetched) == 1
    assert site.agents("/notes.md") == [_BROWSER_USER_AGENT]


def test_an_unmarked_file_source_refused_by_the_site_is_a_fetch_failure(
    test_db: Path, local_pipeline_stub: str, site: _GatedSite
) -> None:
    """
    INVARIANT: an unmarked file source's GET carries httpx's default; the site's 403
               fails the source's cycle, counted in error_count with the status in
               last_error
    BREAKS: the refused GET reads as an unchanged file, so the source looks healthy
            every cycle and is never deactivated
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source(f"{site.base_url}/notes.md", "file", "Notes")

    _orchestrator(
        config, storage, file_fetcher=FileFetcher(config=config, storage=storage)
    ).run_once()

    (ua,) = site.agents("/notes.md")
    assert ua.startswith(_HTTPX_DEFAULT_PREFIX), ua
    (row,) = [s for s in storage.get_all_sources() if s["id"] == source_id]
    assert row["error_count"] == 1, row
    assert "403" in (row["last_error"] or ""), row
    assert _stored_contents(storage, source_id) == []


class _Subreddit:
    def __init__(self, posts: list) -> None:
        self.posts = posts

    def hot(self, limit: int) -> list:
        return self.posts


class _RedditClient:
    def __init__(self, posts: list) -> None:
        self.posts = posts

    def subreddit(self, name: str) -> _Subreddit:
        return _Subreddit(self.posts)


@pytest.mark.usefixtures("reddit_credentials")
@pytest.mark.parametrize(
    ("naughty", "expected_ua"),
    [(True, _BROWSER_USER_AGENT), (False, _USER_AGENT)],
    ids=["marked", "unmarked"],
)
def test_a_reddit_link_posts_article_follows_its_sources_flag(
    test_db: Path, site: _GatedSite, naughty: bool, expected_ua: str
) -> None:
    """
    INVARIANT: the linked article of a post from a marked subreddit is fetched with the
               browser string, and appended; from an unmarked one, with prismis's own
    BREAKS: a marked subreddit's link posts stay link-only placeholders
    """
    storage = Storage(test_db)
    source_id = storage.add_source(
        "https://www.reddit.com/r/python", "reddit", "r/python", naughty=naughty
    )
    post = create_link_post_mock(
        url=f"{site.base_url}/article/1", created_utc=time.time()
    )
    post.stickied = False
    fetcher = RedditFetcher(config=make_config())
    fetcher.reddit = _RedditClient([post])  # type: ignore[assignment]  # ty: ignore[invalid-assignment]

    (item,) = fetcher.fetch_content(_source(storage, source_id))

    assert site.agents("/article/") == [expected_ua]
    assert (_ARTICLE_PHRASE in (item.content or "")) is naughty, item.content


@pytest.mark.parametrize(
    ("naughty", "expected_ua", "status"),
    [
        (True, _BROWSER_USER_AGENT, "recovered"),
        (False, _USER_AGENT, "still_title_only"),
    ],
    ids=["marked", "unmarked"],
)
def test_a_refetched_rss_row_follows_its_sources_flag(
    test_db: Path,
    local_pipeline_stub: str,
    site: _GatedSite,
    naughty: bool,
    expected_ua: str,
    status: str,
) -> None:
    """
    INVARIANT: `refetch --type rss` re-extracts a stored row of a marked source with the
               browser string and recovers it; a row of an unmarked source is requested
               with prismis's own identifier
    BREAKS: refetch keeps re-requesting a marked source's 403'd items with the
            identifier that was refused, and they stay title-only forever
    """
    config = _config(local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source(
        f"{site.base_url}/feed.xml", "rss", "Gated", naughty=naughty
    )
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="gated-row",
            title="Gated row",
            url=f"{site.base_url}/article/1",
            content=RSS_NO_CONTENT_FALLBACK,
        )
    )

    report = run_refetch(_orchestrator(config, storage), "rss", limit=10)

    assert site.agents("/article/") == [expected_ua]
    assert [o.status for o in report.outcomes] == [status]
