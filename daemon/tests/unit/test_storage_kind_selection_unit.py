"""Unit tests for Storage.get_content_needing_kind -- kind-backfill work order, job 1
(SC-1, gh #83; review finding F-1-1's follow-up coverage for the Storage method that
selection was moved into).

Real Storage against a real sealed test database throughout -- no patched connection,
no patched row-mapping. `cli/tests/unit/test_analyze_kinds_unit.py` proves the same
selection semantics through `prismis-cli analyze kinds`; this file proves the Storage
method itself, independent of the CLI layer that now merely calls it.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage


def _seed(
    storage: Storage,
    source_id: str,
    *,
    title: str,
    summary: str | None = "A short summary.",
    analysis: dict[str, Any] | None = None,
    fetched_at: datetime | None = None,
) -> str:
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=str(uuid.uuid4()),
            title=title,
            url=f"http://example.com/{uuid.uuid4().hex[:8]}",
            content="Article body.",
            summary=summary,
            analysis=analysis,
            fetched_at=fetched_at,
        )
    )
    assert content_id is not None, "expected a newly inserted content id, got a duplicate"
    return content_id


def test_selects_items_with_summary_and_no_kind_confidence_key(test_db: Path) -> None:
    """
    INVARIANT: only an item with a summary and no kind_confidence key at all in its
    analysis is returned -- an already-classified item, an already-unclassified item
    (kind_confidence stored as null, not absent), and a summary-less item are excluded.
    BREAKS: json_extract() in place of json_type() would treat a stored null the same
    as an absent key, reselecting unclassified items on every run.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    needs_kind_id = _seed(
        storage, source_id, title="Needs Kind", analysis={"reading_summary": "x"}
    )
    _seed(
        storage,
        source_id,
        title="Already Classified",
        analysis={"kind": "release", "kind_confidence": 0.9},
    )
    _seed(
        storage,
        source_id,
        title="Already Unclassified",
        analysis={"kind": None, "kind_confidence": None},
    )
    _seed(storage, source_id, title="No Summary", summary=None, analysis={})

    results = storage.get_content_needing_kind(limit=10)

    ids = [item["id"] for item in results]
    assert ids == [needs_kind_id]


def test_selects_newest_first_and_at_most_limit(test_db: Path) -> None:
    """
    INVARIANT: results are ordered by fetched_at newest first and bounded to `limit`.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    now = datetime.now(UTC)
    oldest_id = _seed(
        storage, source_id, title="Oldest", analysis={}, fetched_at=now - timedelta(days=2)
    )
    middle_id = _seed(
        storage, source_id, title="Middle", analysis={}, fetched_at=now - timedelta(days=1)
    )
    newest_id = _seed(storage, source_id, title="Newest", analysis={}, fetched_at=now)

    results = storage.get_content_needing_kind(limit=2)

    assert [item["id"] for item in results] == [newest_id, middle_id]
    assert oldest_id not in [item["id"] for item in results]


def test_selects_only_items_fetched_within_since_days_when_given(test_db: Path) -> None:
    """
    INVARIANT: since_days narrows the result to items fetched within the last N days.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    now = datetime.now(UTC)
    _seed(storage, source_id, title="Ancient", analysis={}, fetched_at=now - timedelta(days=10))
    recent_id = _seed(storage, source_id, title="Recent", analysis={}, fetched_at=now)

    results = storage.get_content_needing_kind(limit=10, since_days=5)

    assert [item["id"] for item in results] == [recent_id]


def test_returned_analysis_is_parsed_json(test_db: Path) -> None:
    """
    INVARIANT: the returned dict's analysis field is the parsed dict, not the raw
    JSON text, and every existing field survives -- get_content_needing_kind shares
    Storage._parse_analysis_json with every other row-mapping method.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    _seed(
        storage,
        source_id,
        title="Has Analysis",
        analysis={"reading_summary": "abc", "alpha_insights": ["x"]},
    )

    results = storage.get_content_needing_kind(limit=10)

    assert len(results) == 1
    assert results[0]["analysis"] == {
        "reading_summary": "abc",
        "alpha_insights": ["x"],
    }
