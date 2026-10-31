"""GET /api/search's `author` filter narrows the KNN candidates and joins the others.

Invariant protected:
  - author constrains the KNN candidate query itself, so a matching item outside the
    unfiltered top-100 nearest neighbours is still found
  - author with source, or with kind, returns only items satisfying every filter
  - without author, search returns what it returned before, authorless items
    included, and filters_applied reports the author or null

Success criteria covered: C2, C3, C4.

Real app, real sqlite-vec database, real Embedder for the query text. Rows are seeded
with an analysis.metrics.author directly: these tests are about the filter, and the
fetcher's capture is proven through the real pipeline in
test_author_filter_rss_integration.py.
"""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.embeddings import Embedder
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage
from conftest import TEST_API_KEY, add_new_content

QUERY = "local-first databases"

# More than search_content's KNN candidate pool of 100, so every noise item outranks
# the far item and an unfiltered scan never reaches it.
NOISE_COUNT = 105


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


def _orthogonal_to(vector: list[float]) -> list[float]:
    """A unit vector at cosine similarity 0 to `vector`: farther from the query than
    any noise item, yet still at relevance >= 0 so min_score=0.0 keeps it."""
    basis = _unit_vector(1, len(vector))
    projection = sum(a * b for a, b in zip(basis, vector, strict=True))
    rest = [a - projection * b for a, b in zip(basis, vector, strict=True)]
    norm = sum(x * x for x in rest) ** 0.5
    return [x / norm for x in rest]


def _client_over(storage: Storage) -> Iterator[TestClient]:
    def override_get_storage() -> Iterator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    client = TestClient(app)
    yield client
    app.dependency_overrides.clear()


def _search(client: TestClient, **params: str) -> dict[str, Any]:
    response = client.get(
        "/api/search",
        params={"q": QUERY, "min_score": 0.0, "limit": 50, **params},
        headers={"X-API-Key": TEST_API_KEY},
    )
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _external_ids(data: dict[str, Any]) -> set[str]:
    return {item["external_id"] for item in data["items"]}


@pytest.fixture
def far_author_client(test_db: Path) -> Iterator[TestClient]:
    """105 items by someone else at the query's own embedding, and Simon's item
    orthogonal to it, so all 105 lie nearer the query than Simon's."""
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/rss", "rss", "Mixed Feed")
    query_embedding = Embedder().generate_embedding(text=QUERY)

    for i in range(NOISE_COUNT):
        content_id = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=f"noise-{i}",
                title=f"Noise item {i}",
                url=f"https://example.com/noise/{i}",
                content="Noise content",
                priority="low",
                analysis={"metrics": {"author": "Someone Else"}},
            ),
        )
        storage.add_embedding(content_id, query_embedding)

    content_id = add_new_content(
        storage,
        ContentItem(
            source_id=source_id,
            external_id="simon",
            title="Simon's far item",
            url="https://example.com/simon",
            content="Far content",
            priority="low",
            analysis={"metrics": {"author": "Simon Willison"}},
        ),
    )
    storage.add_embedding(content_id, _orthogonal_to(query_embedding))

    yield from _client_over(storage)


def test_author_finds_an_item_outside_the_unfiltered_top_100(
    far_author_client: TestClient,
) -> None:
    """
    INVARIANT: author=simon returns Simon's item though 105 others lie nearer the
               query; without author it is absent, proving it lies outside the
               unfiltered top-100 candidates
    BREAKS: matching the author over the top 100 after the KNN's LIMIT returns
            nothing for author=simon
    """
    assert _external_ids(_search(far_author_client, author="simon")) == {"simon"}
    assert "simon" not in _external_ids(_search(far_author_client))


@pytest.fixture
def composed_client(test_db: Path) -> Iterator[TestClient]:
    """Four items sharing one embedding: Jane Doe under Blog A (tutorial) and under
    Blog B (release), Other Person under Blog A, and an authorless Blog A item."""
    storage = Storage(test_db)
    blog_a = storage.add_source("https://a.example.com/rss", "rss", "Blog A")
    blog_b = storage.add_source("https://b.example.com/rss", "rss", "Blog B")
    seeds = [
        ("jane-a", blog_a, "Jane Doe", "tutorial"),
        ("jane-b", blog_b, "Jane Doe", "release"),
        ("other-a", blog_a, "Other Person", "tutorial"),
        ("nobody-a", blog_a, None, "tutorial"),
    ]
    for external_id, source_id, author, kind in seeds:
        analysis: dict[str, Any] = {"kind": kind, "kind_confidence": 0.9}
        if author:
            analysis["metrics"] = {"author": author}
        content_id = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=external_id,
                title=f"Item {external_id}",
                url=f"https://example.com/{external_id}",
                content="Body text",
                priority="high",
                analysis=analysis,
            ),
        )
        storage.add_embedding(content_id, _unit_vector(0))

    yield from _client_over(storage)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"author": "jane", "source": "blog a"}, {"jane-a"}),
        ({"author": "jane", "kind": "release"}, {"jane-b"}),
        ({"author": "jane"}, {"jane-a", "jane-b"}),
    ],
)
def test_author_joins_source_and_kind(
    composed_client: TestClient, params: dict[str, str], expected: set[str]
) -> None:
    """
    INVARIANT: author with source or kind returns only items satisfying every
               given filter; author alone returns every item of that author
    BREAKS: an author condition that replaces the source or kind condition returns
            Jane Doe's Blog B item for author=jane&source=blog a
    """
    assert _external_ids(_search(composed_client, **params)) == expected


def test_no_author_keeps_authorless_items_and_reports_null(
    composed_client: TestClient,
) -> None:
    """
    INVARIANT: without author every item returns, the authorless one included, and
               filters_applied.author is null; with author it echoes the value
    BREAKS: an author clause added unconditionally drops the authorless item from
            every plain search; a filters_applied without author hides the filter
    """
    unfiltered = _search(composed_client)
    assert _external_ids(unfiltered) == {"jane-a", "jane-b", "other-a", "nobody-a"}
    assert "author" in unfiltered["filters_applied"]
    assert unfiltered["filters_applied"]["author"] is None

    filtered = _search(composed_client, author="jane")
    assert filtered["filters_applied"]["author"] == "jane"
