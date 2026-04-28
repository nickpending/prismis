"""Unit tests for search min_score filter — storage-level invariants.

Protects:
- Filter invariant: search_content(min_score=X) excludes items with score < X
- Boundary invariant: score == min_score is INCLUDED (>= predicate)
- No-filter invariant: min_score=0.0 returns all embedable items
- Max-value invariant: min_score=1.0 is inclusive — score=1.0 item IS returned
- Cosine formula: orthogonal unit vectors yield similarity ≈ 0.0 (corrected formula)

Challenge finding: the builder labeled this change LOW risk, but score=1.0 is
reachable (identical embedding + high priority + Anthropic authority = 1.0*0.8 +
1.0*0.1 + 1.0*0.1 = 1.0). The >= predicate must include it; a > predicate would
silently drop it. Tested explicitly below.

These tests seed embeddings directly to produce known relevance scores without
loading the sentence-transformers model.

Embedding geometry (corrected formula: sim = 1 - d²/2):
  query      = [1, 0, 0, ...]       (unit vector in dimension 0)
  emb_high   = [1, 0, 0, ...]       (identical → distance=0, sim=1.0, score=0.96)
  emb_low    = [cos(t), sin(t), ...]  (cos_theta=0.20 → distance≈1.265, sim≈0.200, score≈0.32)

  score = sim*0.80 + priority_weight*0.10 + authority*0.10
  high priority (1.0) + rss authority (0.6):  0.80 + 0.10 + 0.06 = 0.96  (emb_high)
  high priority (1.0) + rss authority (0.6):  0.16 + 0.10 + 0.06 = 0.32  (emb_low)
"""

import math
from pathlib import Path

import pytest

from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


def _make_query() -> list[float]:
    """Unit vector in dimension 0 — the search query embedding."""
    emb = [0.0] * 384
    emb[0] = 1.0
    return emb


def _make_high_score_embedding() -> list[float]:
    """Identical to query → distance≈0, sim≈1.0, score≈0.96 (high priority, rss)."""
    emb = [0.0] * 384
    emb[0] = 1.0
    return emb


def _make_low_score_embedding() -> list[float]:
    """78° off query → distance≈1.265, cosine_sim≈0.200, score≈0.32 (high priority, rss).

    Under corrected formula: sim = 1 - d²/2 = 1 - 1.6/2 = 0.200
    Old formula gave: sim = 1 - 1.265 = -0.265 (negative — the defect)
    """
    cos_theta = 0.20
    sin_theta = math.sqrt(1 - cos_theta**2)
    emb = [0.0] * 384
    emb[0] = cos_theta
    emb[1] = sin_theta
    return emb


@pytest.fixture
def seeded_storage(test_db: Path) -> Storage:
    """Storage with two items at known relevance scores (0.96 and 0.30)."""
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "RSS Feed")

    item_high = ContentItem(
        source_id=src_id,
        external_id="high-relevance",
        title="High Relevance Article",
        url="https://example.com/high",
        content="Highly relevant content",
        priority="high",
        published_at=None,
    )
    item_low = ContentItem(
        source_id=src_id,
        external_id="low-relevance",
        title="Low Relevance Article",
        url="https://example.com/low",
        content="Barely relevant content",
        priority="high",
        published_at=None,
    )

    id_high = storage.add_content(item_high)
    id_low = storage.add_content(item_low)

    storage.add_embedding(id_high, _make_high_score_embedding())
    storage.add_embedding(id_low, _make_low_score_embedding())

    return storage


def test_filter_excludes_below_threshold(seeded_storage: Storage) -> None:
    """
    INVARIANT: search_content(min_score=X) returns ONLY items with score >= X.
    BREAKS: Noise results appear in every consumer's search output.

    High-score item (0.96) passes min_score=0.5.
    Low-score item (0.30) is excluded at min_score=0.5.
    """
    results = seeded_storage.search_content(_make_query(), limit=10, min_score=0.5)

    assert len(results) == 1, (
        f"Expected 1 result above 0.5, got {len(results)}: "
        f"{[r['relevance_score'] for r in results]}"
    )
    assert results[0]["title"] == "High Relevance Article"
    assert results[0]["relevance_score"] >= 0.5


def test_filter_off_returns_both_items(seeded_storage: Storage) -> None:
    """
    INVARIANT: min_score=0.0 returns all items with relevance_score >= 0.0.
    Items with extreme anti-correlation (cosine < -0.20) score below 0.0 and are
    still excluded — min_score=0.0 removes the user-configurable threshold, not the
    non-negativity floor implicit in the relevance formula.
    BREAKS: Override path broken; users cannot disable the default 0.1 threshold to
    retrieve low-but-positive-scoring results.
    """
    results = seeded_storage.search_content(_make_query(), limit=10, min_score=0.0)

    assert len(results) == 2, (
        f"Expected 2 results with min_score=0.0, got {len(results)}: "
        f"{[r['relevance_score'] for r in results]}"
    )
    scores = [r["relevance_score"] for r in results]
    assert max(scores) >= 0.9, "High-score item should have score >= 0.9"
    assert min(scores) >= 0.0, "All returned scores must be non-negative"


def test_boundary_at_exact_threshold_included(seeded_storage: Storage) -> None:
    """
    INVARIANT: score == min_score is INCLUDED (>= predicate, not >).
    BREAKS: Items at exactly the threshold are silently dropped.
    """
    # Get the actual low-score item's score
    all_results = seeded_storage.search_content(_make_query(), limit=10, min_score=0.0)
    low_score = min(r["relevance_score"] for r in all_results)

    # At exactly the threshold, item must be included
    results_at = seeded_storage.search_content(
        _make_query(), limit=10, min_score=low_score
    )
    low_ids = [r["external_id"] for r in results_at]
    assert "low-relevance" in low_ids, (
        f"Item with score={low_score} should be included when min_score={low_score}"
    )

    # One step above threshold, item must be excluded
    above = round(low_score + 0.001, 3)
    results_above = seeded_storage.search_content(
        _make_query(), limit=10, min_score=above
    )
    above_ids = [r["external_id"] for r in results_above]
    assert "low-relevance" not in above_ids, (
        f"Item with score={low_score} should be excluded when min_score={above}"
    )


def test_max_value_min_score_includes_perfect_match(test_db: Path) -> None:
    """
    INVARIANT: min_score=1.0 is inclusive — an item scoring exactly 1.0 IS returned.
    BREAKS: The maximum boundary behaves as > instead of >=, dropping perfect matches.

    CHALLENGE FINDING: builder labeled this LOW risk. Score=1.0 is reachable when
    sim=1.0 (identical embedding) + high priority (1.0) + Anthropic authority (1.0):
      score = 1.0*0.80 + 1.0*0.10 + 1.0*0.10 = 1.0
    The >= predicate must include it. This tests that the upper boundary is correct.
    """
    storage = Storage(test_db)
    # Anthropic source gets authority=1.0 (see _calculate_source_authority)
    src_id = storage.add_source(
        "https://anthropic.com/research", "rss", "Anthropic Research"
    )
    item = ContentItem(
        source_id=src_id,
        external_id="perfect-match",
        title="Perfect Match",
        url="https://anthropic.com/perfect",
        content="Perfect content",
        priority="high",
        published_at=None,
    )
    content_id = storage.add_content(item)

    # Identical embedding as query → sim=1.0
    perfect_emb = [0.0] * 384
    perfect_emb[0] = 1.0
    storage.add_embedding(content_id, perfect_emb)

    results = storage.search_content(_make_query(), limit=10, min_score=1.0)

    assert len(results) == 1, (
        f"Item scoring 1.0 must be included at min_score=1.0 (>= is inclusive). "
        f"Got {len(results)} results. Scores: {[r['relevance_score'] for r in results]}"
    )
    assert results[0]["relevance_score"] == 1.0
    assert results[0]["external_id"] == "perfect-match"


def test_orthogonal_unit_vectors_yield_zero_similarity(test_db: Path) -> None:
    """
    INVARIANT: Orthogonal unit vectors must yield similarity ≈ 0.0 under the corrected formula.
    BREAKS: Regression to `1 - d` formula yields sim = -0.414 for orthogonal pairs.

    Geometry:
      query = [1, 0, 0, ...]   (unit vector in dim 0)
      ortho = [0, 1, 0, ...]   (unit vector in dim 1; 90° off query)
      L2 dist = sqrt(2) ≈ 1.4142
      Corrected formula: sim = 1 - d²/2 = 1 - 2/2 = 0.0  ✓
      Old formula:       sim = 1 - d   = 1 - 1.4142 = -0.414  (regression)

    Uses test_db directly (NOT seeded_storage) to keep exactly one item in the
    database so the len == 1 assertion is unambiguous.
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "RSS Feed")

    item = ContentItem(
        source_id=src_id,
        external_id="ortho-item",
        title="Orthogonal Item",
        url="https://example.com/ortho",
        content="Orthogonal content",
        priority="high",
        published_at=None,
    )
    content_id = storage.add_content(item)

    # Unit vector in dimension 1 — orthogonal to query [1, 0, ...]
    ortho_emb = [0.0] * 384
    ortho_emb[1] = 1.0
    storage.add_embedding(content_id, ortho_emb)

    # Query with unit vector in dimension 0
    query = [0.0] * 384
    query[0] = 1.0

    # min_score=0.0 to ensure the item is not filtered out before we can inspect it
    results = storage.search_content(query, limit=10, min_score=0.0)

    assert len(results) == 1, f"Expected exactly 1 result, got {len(results)}"

    relevance_score = results[0]["relevance_score"]

    # Recover actual_sim from relevance_score:
    # relevance_score = sim*0.8 + priority_weight*0.1 + authority*0.1
    # For high priority (1.0) + rss authority (0.6): non-sim contribution = 0.1 + 0.06 = 0.16
    actual_sim = (relevance_score - 0.16) / 0.8

    assert abs(actual_sim) < 1e-5, (
        f"Orthogonal unit vectors must yield similarity ≈ 0.0 (corrected formula: 1 - d²/2). "
        f"Got actual_sim={actual_sim:.6f} (relevance_score={relevance_score}). "
        f"If actual_sim ≈ -0.414, the old `1 - d` formula has been re-introduced."
    )
