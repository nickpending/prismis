"""Unit tests for search_content's kind/source filter constraining the KNN candidate
query itself -- search-kind-filter work order, job 1 (SC-1).

Real sqlite-vec database throughout (Principle I: only the LLM provider may be faked,
and search needs none -- embeddings are seeded directly as unit vectors, the same
pattern test_search_min_score_unit.py already uses).

Reproduces the measured defect (work order's `why`): items of one kind (or one source)
that all rank outside the top-100 nearest neighbours are invisible under a
filter-the-top-100-afterward implementation, but present in full under a
filter-the-candidates-before-the-limit implementation. The KNN query must apply the
filter as a `content_id IN (subquery)` constraint before the `LIMIT 100`, not to the
top 100 afterward.
"""

from pathlib import Path

import pytest

from conftest import add_new_content
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# > the KNN candidate pool size (100 in search_content), so the target items below
# rank outside it and an unfiltered scan never reaches them.
NOISE_COUNT = 105


def _unit_vector(dim: int, size: int = 384) -> list[float]:
    emb = [0.0] * size
    emb[dim] = 1.0
    return emb


def _query_embedding() -> list[float]:
    return _unit_vector(0)


@pytest.fixture
def filtered_knn_storage(test_db: Path) -> tuple[Storage, list[str]]:
    """105 noise items identical to the query embedding (distance 0, filling the
    KNN candidate pool), plus 3 target items far from the query (orthogonal
    dimension 1, so they rank outside the top 100 under an unfiltered KNN scan).

    Returns (storage, target_content_ids).
    """
    storage = Storage(test_db)
    noise_source = storage.add_source("https://example.com/noise", "rss", "Noise Feed")
    target_source = storage.add_source(
        "https://example.com/target", "rss", "Target Feed"
    )

    for i in range(NOISE_COUNT):
        item = ContentItem(
            source_id=noise_source,
            external_id=f"noise-{i}",
            title=f"Noise item {i}",
            url=f"https://example.com/noise/{i}",
            content="Noise content",
            priority="low",
            published_at=None,
            analysis={"kind": "news", "kind_confidence": 0.9},
        )
        content_id = add_new_content(storage, item)
        storage.add_embedding(content_id, _unit_vector(0))

    target_ids: list[str] = []
    for i in range(3):
        item = ContentItem(
            source_id=target_source,
            external_id=f"target-{i}",
            title=f"Target item {i}",
            url=f"https://example.com/target/{i}",
            content="Target content",
            priority="low",
            published_at=None,
            analysis={"kind": "tutorial", "kind_confidence": 0.9},
        )
        content_id = add_new_content(storage, item)
        storage.add_embedding(content_id, _unit_vector(1))
        target_ids.append(content_id)

    return storage, target_ids


def test_unfiltered_top_100_excludes_the_far_items(
    filtered_knn_storage: tuple[Storage, list[str]],
) -> None:
    """
    INVARIANT (baseline, unchanged behaviour): with no filter, the KNN candidate
    pool is still the nearest 100 -- the 3 target items, all farther than the 105
    noise items, rank outside it and are absent from the results.
    BREAKS: if this fails, the fixture's premise is wrong and the tests below prove
    nothing about the fix.
    """
    storage, target_ids = filtered_knn_storage
    results = storage.search_content(_query_embedding(), limit=50, min_score=0.0)
    result_ids = {r["id"] for r in results}
    assert not result_ids & set(target_ids), (
        "target items must rank outside the unfiltered top-100 pool for this test "
        "to prove anything"
    )


def test_kind_filter_constrains_the_knn_query_not_the_top_100(
    filtered_knn_storage: tuple[Storage, list[str]],
) -> None:
    """
    INVARIANT (SC-1): a kind filter on items that all rank outside the top-100
    nearest neighbours still returns them -- the filter is a content_id IN
    (subquery) constraint on the KNN candidate query itself.
    BREAKS: filtering the already-limited top-100 pool afterward (the original
    defect) returns zero results here, since none of the 3 target items are in it.
    """
    storage, target_ids = filtered_knn_storage
    results = storage.search_content(
        _query_embedding(), limit=50, min_score=0.0, kind_filter=["tutorial"]
    )
    result_ids = {r["id"] for r in results}
    assert result_ids == set(target_ids)
    for r in results:
        assert r["analysis"]["kind"] == "tutorial"


def test_source_filter_constrains_the_knn_query_not_the_top_100(
    filtered_knn_storage: tuple[Storage, list[str]],
) -> None:
    """
    INVARIANT (SC-1): a source filter behaves the same as a kind filter -- it also
    constrains the KNN candidate query, not the top-100 pool afterward.
    BREAKS: filtering the top 100 afterward returns zero results, since none of the
    3 target-source items are in the unfiltered top 100.
    """
    storage, target_ids = filtered_knn_storage
    results = storage.search_content(
        _query_embedding(), limit=50, min_score=0.0, source_filter="Target Feed"
    )
    result_ids = {r["id"] for r in results}
    assert result_ids == set(target_ids)
    for r in results:
        assert r["source_name"] == "Target Feed"


def test_combined_kind_and_source_filter_both_apply(
    filtered_knn_storage: tuple[Storage, list[str]],
) -> None:
    """
    INVARIANT (SC-1): kind and source filters combine (AND), both against the KNN
    candidate query.
    BREAKS: only one of the two filters is threaded into the candidate subquery, so
    combining them silently drops one of them.
    """
    storage, target_ids = filtered_knn_storage
    results = storage.search_content(
        _query_embedding(),
        limit=50,
        min_score=0.0,
        kind_filter=["tutorial"],
        source_filter="Target Feed",
    )
    result_ids = {r["id"] for r in results}
    assert result_ids == set(target_ids)

    # A kind that matches nothing in the target source returns nothing.
    empty = storage.search_content(
        _query_embedding(),
        limit=50,
        min_score=0.0,
        kind_filter=["news"],
        source_filter="Target Feed",
    )
    assert empty == []


def test_no_filter_results_unchanged(
    filtered_knn_storage: tuple[Storage, list[str]],
) -> None:
    """
    INVARIANT (SC-1): with no kind/source filter, results are identical to today's
    -- the candidate pool is still capped at the nearest 100 (unchanged; not this
    work order's concern per its outOfScope), so a `limit` above 100 still returns
    at most 100, and no target item leaks in since they rank outside that pool.
    BREAKS: the added content_id IN (subquery) branch changes unfiltered behaviour.
    """
    storage, target_ids = filtered_knn_storage
    results = storage.search_content(_query_embedding(), limit=105, min_score=0.0)
    assert len(results) == 100
    result_ids = {r["id"] for r in results}
    assert result_ids.isdisjoint(set(target_ids))
