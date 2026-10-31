"""An RSS or Atom entry's author is stored and found by /api/search's author filter.

Invariant protected:
  - the RSS fetcher stores each entry's own authors in analysis.metrics.author, every
    one of several, as display names; an Atom entry with none of its own inherits the
    feed's; an RSS entry never inherits the channel's managingEditor
  - GET /api/search's `author` is a case-insensitive substring over that key and
    returns only the items it matches

Success criteria covered: C1.

The feeds are served by a real local HTTP server, fetched by the real RSSFetcher and
stored through the real orchestrator's analyze_and_store_item with the real Embedder;
the LLM is `local_pipeline_stub`, the one collaborator the constitution permits
standing in for. Nothing here writes metrics.author itself: every stored author came
through the fetcher and the orchestrator's merge.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.config import Config
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import TEST_API_KEY, LocalHttpServer, configure_local_services

_RSS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
  <title>Authored RSS</title><link>{base}/</link><description>local</description>
  <managingEditor>ed@example.com (Ed Itor)</managingEditor>
  <item><title>Datasette plugins for local-first tools</title>
    <link>{base}/simon</link><guid>rss-simon</guid>
    <dc:creator>Simon Willison</dc:creator>
    <description>Notes on building plugins for a local-first database explorer.</description></item>
  <item><title>Writing a resolver cache</title>
    <link>{base}/jane</link><guid>rss-jane</guid>
    <author>jane@example.com (Jane Doe)</author>
    <description>How a package manager reuses dependency resolution results.</description></item>
  <item><title>Co-written notes on linters</title>
    <link>{base}/two</link><guid>rss-two</guid>
    <dc:creator>A One</dc:creator><dc:creator>B Two</dc:creator>
    <description>Two people compare lint rule configurations across repos.</description></item>
  <item><title>Editorial roundup</title>
    <link>{base}/editor</link><guid>rss-editor</guid>
    <description>A weekly roundup of links with no named writer.</description></item>
</channel></rss>
"""

_ATOM_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Authored Atom</title><id>urn:atom-feed</id><updated>{updated}</updated>
  <author><name>Feed Writer</name></author>
  <link href="{base}/"/>
  <entry><title>A feed-level author's essay</title><id>atom-feedwriter</id>
    <link href="{base}/atom-entry"/><updated>{updated}</updated>
    <summary>An essay whose writer is named only at the feed level.</summary></entry>
</feed>
"""

QUERY = "notes and essays"


def _source(storage: Storage, url: str, name: str) -> dict[str, Any]:
    source_id = storage.add_source(url, "rss", name)
    return {"id": source_id, "url": url, "type": "rss", "name": name}


@pytest.fixture
def authored_storage(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    local_http_server: LocalHttpServer,
) -> Storage:
    """Both feeds fetched and every item stored through the real pipeline."""
    base = local_http_server.base_url
    updated = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    local_http_server.routes["/rss.xml"] = (
        200,
        "application/rss+xml",
        _RSS_FEED.format(base=base).encode(),
    )
    local_http_server.routes["/atom.xml"] = (
        200,
        "application/atom+xml",
        _ATOM_FEED.format(base=base, updated=updated).encode(),
    )

    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    config = Config.from_file()
    storage = Storage(test_db)
    fetcher = RSSFetcher(config=config)
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=fetcher,
        reddit_fetcher=fetcher,
        youtube_fetcher=fetcher,
        file_fetcher=fetcher,
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        config=config,
    )

    for path, name in (("/rss.xml", "Authored RSS"), ("/atom.xml", "Authored Atom")):
        source = _source(storage, f"{base}{path}", name)
        items = fetcher.fetch_content(source)
        assert items, f"{name} yielded no items"
        for item in items:
            assert orchestrator.analyze_and_store_item(item, source) is not None
    return storage


@pytest.fixture
def search_client(authored_storage: Storage) -> Iterator[TestClient]:
    def override_get_storage() -> Iterator[Storage]:
        yield authored_storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _stored_authors(storage: Storage) -> dict[str, Any]:
    rows = storage.conn.execute(
        "SELECT external_id, json_extract(analysis, '$.metrics.author') AS author "
        "FROM content"
    ).fetchall()
    return {row["external_id"]: row["author"] for row in rows}


def _search(client: TestClient, author: str) -> set[str]:
    response = client.get(
        "/api/search",
        params={"q": QUERY, "min_score": 0.0, "limit": 50, "author": author},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    return {item["external_id"] for item in response.json()["data"]["items"]}


def test_each_entry_stores_its_own_authors_or_the_atom_feeds(
    authored_storage: Storage,
) -> None:
    """
    INVARIANT: metrics.author holds every entry author's display name, an Atom
               entry inherits its feed's author, and an RSS entry under a
               managingEditor stores none
    BREAKS: reading feedparser's entry.author keeps only the last of several authors
            and the raw `email (Name)` form; skipping the Atom fallback leaves
            simonwillison.net-style feeds authorless; inheriting from any feed
            credits an RSS channel's editor with every item
    """
    assert _stored_authors(authored_storage) == {
        "rss-simon": "Simon Willison",
        "rss-jane": "Jane Doe",
        "rss-two": "A One, B Two",
        "rss-editor": None,
        "atom-feedwriter": "Feed Writer",
    }


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ("jane doe", {"rss-jane"}),
        ("b two", {"rss-two"}),
        ("SIMON", {"rss-simon"}),
        ("feed writer", {"atom-feedwriter"}),
        ("ed itor", set()),
    ],
)
def test_author_search_returns_only_the_matching_item(
    search_client: TestClient, author: str, expected: set[str]
) -> None:
    """
    INVARIANT: author=<substring> returns exactly the items whose stored author
               contains it, case-insensitively
    BREAKS: an ignored parameter returns all five items; a missing Atom fallback
            finds nothing for the feed writer; an inherited managingEditor finds the
            editor's roundup
    """
    assert _search(search_client, author) == expected
