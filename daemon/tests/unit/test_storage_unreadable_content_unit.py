"""Unit tests for Storage.get_unreadable_content -- refetch-unreadable work order,
job 2 (SC-2; review finding F-2-3's follow-up coverage for the Storage method the
selection was built in).

Every refetch test in the diff seeds only already-unreadable rows, so none of them
can tell "the selection query enforces readable-items-untouched, the --limit cap, and
oldest-first ordering" apart from "every seeded row happens to already satisfy them".
This file proves the Storage method itself, directly, against a real sealed test
database -- no patched connection, no patched row-mapping.
"""

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from prismis_daemon.models import ContentItem
from prismis_daemon.readability import RSS_NO_CONTENT_FALLBACK
from prismis_daemon.storage import Storage

_READABLE_CONTENT = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)


def _seed(
    storage: Storage,
    source_id: str,
    *,
    title: str,
    content: str,
    fetched_at: datetime | None = None,
) -> str:
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=str(uuid.uuid4()),
            title=title,
            url=f"http://example.com/{uuid.uuid4().hex[:8]}",
            content=content,
            fetched_at=fetched_at,
        )
    )
    assert content_id is not None, "expected a newly inserted content id, got a duplicate"
    return content_id


def test_readable_items_are_never_selected(test_db: Path) -> None:
    """
    SC-2: "Readable items are never touched" is enforced by the selection
    query itself -- proved with a mix of readable and not-readable rows of
    the same source type, not a seed set where every row already qualifies.
    BREAKS: a WHERE clause that drops (or inverts) the is_readable filter
    returns the readable row too, or returns only it.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    unreadable_id = _seed(
        storage, source_id, title="Unreadable", content=RSS_NO_CONTENT_FALLBACK
    )
    _seed(storage, source_id, title="Readable", content=_READABLE_CONTENT)

    results = storage.get_unreadable_content("rss", limit=10)

    assert [row["id"] for row in results] == [unreadable_id]


def test_selects_at_most_limit_when_more_unreadable_rows_exist(test_db: Path) -> None:
    """
    SC-2: "bounded by --limit" -- three not-readable rows exist, `limit=2`
    returns exactly two.
    BREAKS: a cap applied to the underlying page size rather than the final
    selection (or no cap at all) returns every not-readable row regardless
    of `limit`.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    for i in range(3):
        _seed(
            storage,
            source_id,
            title=f"Unreadable {i}",
            content=RSS_NO_CONTENT_FALLBACK,
        )

    results = storage.get_unreadable_content("rss", limit=2)

    assert len(results) == 2


def test_selects_oldest_fetched_first(test_db: Path) -> None:
    """
    SC-2: "oldest first" -- rows seeded with distinct fetched_at values, out
    of insertion order, are returned oldest fetched_at first.
    BREAKS: an ORDER BY that sorts DESC, or omits ORDER BY and relies on
    insertion order, returns them in a different order than this test seeded.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    now = datetime.now(UTC)
    # Seeded newest-first, the opposite of insertion order the query must
    # return -- so a query that (wrongly) relies on insertion/rowid order
    # would fail this too.
    newest_id = _seed(
        storage, source_id, title="Newest", content=RSS_NO_CONTENT_FALLBACK, fetched_at=now
    )
    middle_id = _seed(
        storage,
        source_id,
        title="Middle",
        content=RSS_NO_CONTENT_FALLBACK,
        fetched_at=now - timedelta(days=1),
    )
    oldest_id = _seed(
        storage,
        source_id,
        title="Oldest",
        content=RSS_NO_CONTENT_FALLBACK,
        fetched_at=now - timedelta(days=2),
    )

    results = storage.get_unreadable_content("rss", limit=10)

    assert [row["id"] for row in results] == [oldest_id, middle_id, newest_id]


def test_excludes_archived_rows(test_db: Path) -> None:
    """
    SC-2: "non-archived" -- an archived not-readable row is excluded even
    though its content would otherwise qualify.
    BREAKS: a query missing (or misapplying) the `archived_at IS NULL` filter
    resurfaces an archived row for refetch.
    """
    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")

    archived_id = _seed(
        storage, source_id, title="Archived", content=RSS_NO_CONTENT_FALLBACK
    )
    active_id = _seed(
        storage, source_id, title="Active", content=RSS_NO_CONTENT_FALLBACK
    )
    storage.conn.execute(
        "UPDATE content SET archived_at = CURRENT_TIMESTAMP WHERE id = ?",
        (archived_id,),
    )
    storage.conn.commit()

    results = storage.get_unreadable_content("rss", limit=10)

    assert [row["id"] for row in results] == [active_id]
