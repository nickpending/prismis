"""Unit tests for the decidable logic in `source list` and `source remove`.

Both commands were left with zero coverage when the SQLite-era integration tests were
deleted. The parts that hold a real decision are called directly here — no mocks, per
constitution.md:42-43 ("Internal code is never mocked") and :130-132 (LLM providers are
the only permitted mock). The `APIClient` calls that remain in those commands are
one-line pass-throughs and are recorded as glue in the plan's Constitution Check table.
"""

import pytest

from cli.source import find_source_by_id, format_source_row


# ---------------------------------------------------------------------------
# remove: find-by-id (source.py:221-228)
#
# INVARIANT: removal only ever targets a source that exists.
# BREAKS: `remove` is destructive and cascades to all content from the source. A lookup
# that returned the wrong row, or a falsy row treated as "not found", deletes the wrong
# thing or refuses to delete the right one.
# ---------------------------------------------------------------------------


def test_find_source_by_id_returns_the_matching_source() -> None:
    sources = [
        {"id": "a", "name": "First"},
        {"id": "b", "name": "Second"},
        {"id": "c", "name": "Third"},
    ]
    assert find_source_by_id(sources, "b") == {"id": "b", "name": "Second"}


def test_find_source_by_id_returns_none_when_absent() -> None:
    """The not-found path: `remove` turns this into exit 1 without deleting anything."""
    sources = [{"id": "a", "name": "First"}]
    assert find_source_by_id(sources, "definitely-not-there") is None


def test_find_source_by_id_on_empty_list() -> None:
    assert find_source_by_id([], "a") is None


def test_find_source_by_id_returns_the_first_match_only() -> None:
    """Ids are UUIDs and unique; if the API ever returns duplicates, take the first."""
    sources = [{"id": "a", "name": "First"}, {"id": "a", "name": "Shadow"}]
    assert find_source_by_id(sources, "a")["name"] == "First"


def test_find_source_by_id_does_not_match_on_substring() -> None:
    """A prefix of a real id must not resolve — that would delete the wrong source."""
    sources = [{"id": "abc123", "name": "Real"}]
    assert find_source_by_id(sources, "abc") is None


# ---------------------------------------------------------------------------
# list: row formatting (source.py:192-194 truncation, and the column derivations)
# ---------------------------------------------------------------------------


def test_format_source_row_truncates_a_long_name() -> None:
    """Names over 25 chars are cut to 22 plus an ellipsis, so the column stays aligned."""
    source = {"id": "1", "type": "rss", "name": "x" * 40, "active": True}
    _, _, name, _, _, _ = format_source_row(source)
    assert name == "x" * 22 + "..."
    assert len(name) == 25


def test_format_source_row_leaves_a_boundary_name_intact() -> None:
    """Exactly 25 is not 'too long' — the boundary must not truncate."""
    source = {"id": "1", "type": "rss", "name": "y" * 25, "active": True}
    assert format_source_row(source)[2] == "y" * 25


def test_format_source_row_renders_an_unnamed_source() -> None:
    source = {"id": "1", "type": "rss", "name": None, "active": True}
    assert format_source_row(source)[2] == "Unnamed"


@pytest.mark.parametrize(
    ("active", "expected"), [(True, "✅ Yes"), (False, "❌ No"), (None, "❌ No")]
)
def test_format_source_row_renders_active_state(active, expected: str) -> None:
    source = {"id": "1", "type": "rss", "name": "n", "active": active}
    assert format_source_row(source)[3] == expected


def test_format_source_row_shows_a_dash_for_zero_errors() -> None:
    """Zero errors reads as '—', not '0' — an em dash is the empty-success rendering."""
    source = {"id": "1", "type": "rss", "name": "n", "active": True, "error_count": 0}
    assert format_source_row(source)[4] == "—"


def test_format_source_row_shows_the_error_count_when_nonzero() -> None:
    source = {"id": "1", "type": "rss", "name": "n", "active": True, "error_count": 3}
    assert format_source_row(source)[4] == "3"


def test_format_source_row_truncates_the_timestamp_to_seconds() -> None:
    source = {
        "id": "1",
        "type": "rss",
        "name": "n",
        "active": True,
        "last_fetched": "2026-09-04T09:30:01.123456+00:00",
    }
    assert format_source_row(source)[5] == "2026-09-04T09:30:01"


def test_format_source_row_renders_a_never_fetched_source() -> None:
    source = {"id": "1", "type": "rss", "name": "n", "active": True, "last_fetched": None}
    assert format_source_row(source)[5] == "Never"
