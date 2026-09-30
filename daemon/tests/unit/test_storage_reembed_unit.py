"""Re-embedding an item replaces its search vector.

sqlite-vec's vec0 virtual table ignores INSERT OR REPLACE, so a second add_embedding
for the same content_id raised a UNIQUE error and left the item searchable only by its
old vector: 76 backfilled YouTube videos and 20 retried items on cerebro, 2026-09-30.
Real sqlite-vec database; embeddings are seeded directly as unit vectors.
"""

from pathlib import Path

from conftest import add_new_content
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


def test_add_embedding_twice_replaces_the_search_vector(test_db: Path) -> None:
    storage = Storage(test_db)
    source = storage.add_source("https://example.com/feed", "rss", "Feed")
    content_id = add_new_content(
        storage,
        ContentItem(
            source_id=source,
            external_id="item-1",
            title="Item",
            url="https://example.com/1",
            content="body",
            priority="high",
        ),
    )
    storage.add_embedding(content_id, _unit_vector(0))

    storage.add_embedding(content_id, _unit_vector(1))

    near_new = storage.search_content(_unit_vector(1), limit=5, min_score=0.9)
    near_old = storage.search_content(_unit_vector(0), limit=5, min_score=0.9)
    assert [r["id"] for r in near_new] == [content_id]
    assert near_old == []
