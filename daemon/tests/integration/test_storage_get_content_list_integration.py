"""Integration tests for Storage.get_content_list: one bounded SQL list query
(memory-footprint work order, SC-2).

Real Storage against a real sealed test database. Rows are planted with raw SQL so
priority, read state, user_feedback, kind, source, fetched_at and archived_at are
controlled exactly, and each carries a 50 KB content and a 50 KB analysis.full_text
-- the shape of a file-source item the list view must never read.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from prismis_daemon.storage import Storage

BIG = "x" * 50_000
NOW = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
LIST_KEYS = {
    "kind",
    "kind_confidence",
    "title_only",
    "metrics",
    "metadata",
    "matched_interests",
    "preference_influenced",
}


def _plant(
    conn: sqlite3.Connection,
    source_id: str,
    name: str,
    *,
    priority: str | None,
    published_hours_ago: int,
    read: int = 0,
    feedback: str | None = None,
    fetched_days_ago: int = 0,
    archived: bool = False,
    analysis: dict[str, object] | None = None,
    raw_analysis: str | None = None,
) -> None:
    if raw_analysis is None:
        raw_analysis = json.dumps(analysis) if analysis is not None else None
    conn.execute(
        """
        INSERT INTO content (id, source_id, external_id, title, url, content, summary,
            analysis, priority, published_at, fetched_at, read, user_feedback,
            archived_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name,
            source_id,
            name,
            f"Title {name}",
            f"https://example.com/{name}",
            BIG,
            f"summary {name}",
            raw_analysis,
            priority,
            (NOW - timedelta(hours=published_hours_ago)).isoformat(),
            (NOW - timedelta(days=fetched_days_ago)).isoformat(),
            read,
            feedback,
            NOW.isoformat() if archived else None,
        ),
    )


def _analysis(kind: str, **extra: object) -> dict[str, object]:
    return {"kind": kind, "full_text": BIG, "extra_noise": BIG[:100], **extra}


@pytest.fixture
def storage(test_db: Path) -> Storage:
    s = Storage(test_db)
    alpha = s.add_source("https://alpha.example/rss", "rss", "Alpha Feed")
    beta = s.add_source("https://beta.example/rss", "rss", "Beta Blog")
    c = s.conn
    # published newest-first: lower hours_ago = newer
    _plant(c, alpha, "h-new", priority="high", published_hours_ago=1, analysis=_analysis("news"))
    _plant(c, alpha, "h-old", priority="high", published_hours_ago=50, read=1,
           feedback="up", analysis=_analysis("release"))
    _plant(c, beta, "m-new", priority="medium", published_hours_ago=2, feedback="up",
           analysis=_analysis("news"))
    _plant(c, beta, "m-old", priority="medium", published_hours_ago=60, read=1,
           analysis=_analysis("tutorial"))
    _plant(c, alpha, "l-new", priority="low", published_hours_ago=3, fetched_days_ago=10,
           analysis=_analysis("news"))
    _plant(c, beta, "n-newest", priority=None, published_hours_ago=0, feedback="down",
           analysis=_analysis("news"))
    _plant(c, beta, "n-old", priority=None, published_hours_ago=70, read=1,
           analysis=_analysis("news"))
    _plant(c, alpha, "arch", priority="high", published_hours_ago=4, archived=True,
           analysis=_analysis("news"))
    c.commit()
    return s


def _ids(rows: list[dict[str, Any]]) -> list[str]:
    return [r["id"] for r in rows]


def test_limit_bounds_rows_and_priority_sort_is_high_medium_low_none(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, sort_by="priority")
    assert _ids(rows) == [
        "h-new", "h-old", "m-new", "m-old", "l-new", "n-newest", "n-old",
    ]
    assert _ids(storage.get_content_list(limit=3)) == ["h-new", "h-old", "m-new"]


def test_date_sort_is_published_newest_first_across_priorities(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, sort_by="date")
    assert _ids(rows) == [
        "n-newest", "h-new", "m-new", "l-new", "h-old", "m-old", "n-old",
    ]


def test_unread_sort_puts_unread_first_then_newest(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, sort_by="unread")
    assert _ids(rows) == [
        "n-newest", "h-new", "m-new", "l-new", "h-old", "m-old", "n-old",
    ]
    # tell it apart from the date sort: make an old row unread
    storage.conn.execute("UPDATE content SET read = 0 WHERE id = 'n-old'")
    storage.conn.commit()
    rows = storage.get_content_list(limit=100, sort_by="unread")
    assert _ids(rows) == [
        "n-newest", "h-new", "m-new", "l-new", "n-old", "h-old", "m-old",
    ]


def test_unread_only_excludes_read_rows(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, unread_only=True)
    assert set(_ids(rows)) == {"h-new", "m-new", "l-new", "n-newest"}


def test_interesting_selects_user_feedback_up(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, interesting=True)
    assert set(_ids(rows)) == {"h-old", "m-new"}


def test_priority_filter(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, priorities=["medium", "low"])
    assert _ids(rows) == ["m-new", "m-old", "l-new"]


def test_kind_filter_applies_before_limit(storage: Storage) -> None:
    rows = storage.get_content_list(limit=1, kind_filter=["tutorial"])
    assert _ids(rows) == ["m-old"]


def test_source_substring_filter_is_case_insensitive(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, source_filter="ALPHA")
    assert set(_ids(rows)) == {"h-new", "h-old", "l-new"}


def test_since_filters_on_fetched_at(storage: Storage) -> None:
    rows = storage.get_content_list(limit=100, since=NOW - timedelta(days=5))
    assert "l-new" not in _ids(rows)
    assert len(rows) == 6


def test_archived_rows_excluded_unless_included(storage: Storage) -> None:
    assert "arch" not in _ids(storage.get_content_list(limit=100))
    assert "arch" in _ids(storage.get_content_list(limit=100, include_archived=True))


def test_list_view_has_no_content_and_cuts_analysis_to_list_keys(
    storage: Storage,
) -> None:
    storage.conn.execute(
        "UPDATE content SET analysis = ? WHERE id = 'h-new'",
        (
            json.dumps(
                _analysis(
                    "news",
                    kind_confidence=0.9,
                    title_only=True,
                    metrics={"a": 1},
                    metadata={"k": "v"},
                    matched_interests=["ai"],
                    preference_influenced=False,
                    deep_extraction={"full": BIG},
                )
            ),
        ),
    )
    storage.conn.commit()
    rows = {r["id"]: r for r in storage.get_content_list(limit=100, view="list")}
    row = rows["h-new"]
    assert "content" not in row
    assert row["analysis"] == {
        "kind": "news",
        "kind_confidence": 0.9,
        "title_only": True,
        "metrics": {"a": 1},
        "metadata": {"k": "v"},
        "matched_interests": ["ai"],
        "preference_influenced": False,
    }
    assert row["has_deep_extraction"] is True
    # absent keys are omitted, not null-filled
    assert rows["h-old"]["analysis"] == {"kind": "release"}
    assert rows["h-old"]["has_deep_extraction"] is False
    for r in rows.values():
        assert set(r["analysis"]) <= LIST_KEYS
        assert "full_text" not in json.dumps(r)
    assert row["summary"] == "summary h-new"
    assert row["source_name"] == "Alpha Feed"
    assert row["read"] is False


def test_list_view_invalid_or_missing_analysis_gives_empty_analysis(
    storage: Storage,
) -> None:
    c = storage.conn
    src = c.execute("SELECT id FROM sources LIMIT 1").fetchone()[0]
    _plant(c, src, "bad-json", priority="low", published_hours_ago=5, raw_analysis="{not json")
    _plant(c, src, "no-analysis", priority="low", published_hours_ago=6)
    _plant(c, src, "array-analysis", priority="low", published_hours_ago=7, raw_analysis="[1, 2]")
    c.commit()
    rows = {r["id"]: r for r in storage.get_content_list(limit=100, view="list")}
    for rid in ("bad-json", "no-analysis", "array-analysis"):
        assert rows[rid]["analysis"] == {}, rid
        assert rows[rid]["has_deep_extraction"] is False, rid


def test_full_view_carries_content_and_whole_analysis(storage: Storage) -> None:
    storage.conn.execute(
        "UPDATE content SET analysis = ? WHERE id = 'h-new'",
        (json.dumps(_analysis("news", deep_extraction={"x": 1})),),
    )
    storage.conn.commit()
    rows = {r["id"]: r for r in storage.get_content_list(limit=100, view="full")}
    assert rows["h-new"]["content"] == BIG
    assert rows["h-new"]["analysis"]["full_text"] == BIG
    assert rows["h-new"]["has_deep_extraction"] is True
    assert rows["h-old"]["has_deep_extraction"] is False
    assert _ids(storage.get_content_list(limit=2, view="full")) == ["h-new", "h-old"]


def test_one_sql_statement_per_call(storage: Storage) -> None:
    statements: list[str] = []
    storage.conn.set_trace_callback(statements.append)
    storage.get_content_list(limit=5, view="list", priorities=["high"], unread_only=True)
    storage.conn.set_trace_callback(None)
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1


# --- iter_content_list / count_content_list (bounded-list-memory, SC-1) ---------------

FILTERS: list[dict[str, Any]] = [
    {},
    {"priorities": ["high", "low"]},
    {"unread_only": True},
    {"interesting": True},
    {"kind_filter": ["news"]},
    {"source_filter": "beta"},
    {"include_archived": True},
    {"since": NOW - timedelta(days=5)},
]


@pytest.mark.parametrize("view", ["full", "list"])
@pytest.mark.parametrize("filters", FILTERS, ids=[str(f) for f in FILTERS])
def test_iterating_across_fetch_batches_yields_what_the_list_read_returns(
    storage: Storage,
    monkeypatch: pytest.MonkeyPatch,
    view: str,
    filters: dict[str, Any],
) -> None:
    """
    INVARIANT: the iterator yields exactly get_content_list's rows, in order
    BREAKS: a row is dropped or repeated where one fetch batch ends and the next begins
    """
    monkeypatch.setattr(Storage, "_LIST_FETCH_BATCH", 2)
    streamed = list(storage.iter_content_list(100, view=view, **filters))
    assert streamed == storage.get_content_list(limit=100, view=view, **filters)
    assert len(streamed) >= 1


@pytest.mark.parametrize("filters", FILTERS, ids=[str(f) for f in FILTERS])
@pytest.mark.parametrize("limit", [1, 3, 100])
def test_count_is_the_number_of_rows_the_iterator_yields(
    storage: Storage, filters: dict[str, Any], limit: int
) -> None:
    """
    INVARIANT: count_content_list announces exactly the rows iter_content_list sends
    BREAKS: the message and total of a streamed response disagree with its items
    """
    expected = len(list(storage.iter_content_list(limit, **filters)))
    assert storage.count_content_list(limit, **filters) == expected


def test_an_unknown_view_raises_when_called_not_on_the_first_row(
    storage: Storage,
) -> None:
    with pytest.raises(ValueError, match="Unknown content view"):
        storage.iter_content_list(5, view="bogus")


def test_the_connection_is_usable_after_the_iterator_is_abandoned(
    storage: Storage,
) -> None:
    rows = storage.iter_content_list(100)
    next(rows)
    rows.close()  # type: ignore[attr-defined]
    assert len(storage.get_content_list(limit=100)) == 7
