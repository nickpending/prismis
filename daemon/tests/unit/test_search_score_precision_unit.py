"""search_content orders by the raw relevance score, not the 3-decimal rounding it reports (gh #14).

Two items whose raw scores differ by less than 0.0005 round to the same reported
score. When the nearer item (first in the KNN candidate order) has the lower raw score,
sorting on the rounded value keeps candidate order and ranks it first; sorting on the
raw value ranks the truly higher item first.

Geometry (score = sim*0.80 + priority_weight*0.10 + authority*0.10, rss authority 0.6):
  nearer  : cos 0.9624, medium (0.5) -> 0.76992 + 0.05 + 0.06 = 0.87992 -> 0.880
  farther : cos 0.9000, high   (1.0) -> 0.72000 + 0.10 + 0.06 = 0.88000 -> 0.880
"""

import math
from pathlib import Path

from conftest import add_new_content
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


def _unit_vector_at(cos_theta: float) -> list[float]:
    emb = [0.0] * 384
    emb[0] = cos_theta
    emb[1] = math.sqrt(1 - cos_theta**2)
    return emb


def test_search_ranks_by_raw_score_when_rounded_scores_tie(test_db: Path) -> None:
    """BREAKS: sorting on the rounded score returns the nearer, lower-scoring item
    first, because both round to 0.880 and the sort keeps KNN candidate order."""
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "RSS Feed")
    nearer = add_new_content(
        storage,
        ContentItem(
            source_id=src_id,
            external_id="nearer-lower-score",
            title="Nearer, lower raw score",
            url="https://example.com/nearer",
            content="content",
            priority="medium",
        ),
    )
    farther = add_new_content(
        storage,
        ContentItem(
            source_id=src_id,
            external_id="farther-higher-score",
            title="Farther, higher raw score",
            url="https://example.com/farther",
            content="content",
            priority="high",
        ),
    )
    storage.add_embedding(nearer, _unit_vector_at(0.9624))
    storage.add_embedding(farther, _unit_vector_at(0.9000))

    results = storage.search_content(_unit_vector_at(1.0), limit=10, min_score=0.0)

    assert [r["title"] for r in results] == [
        "Farther, higher raw score",
        "Nearer, lower raw score",
    ]
    assert [r["relevance_score"] for r in results] == [0.88, 0.88]
