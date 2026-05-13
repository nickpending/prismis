"""Unit tests for Storage.get_feedback_statistics() — SQL parameterization invariants.

Protects:
- INV-SC17: No f-string SQL interpolation of since_days in get_feedback_statistics
- INV-SC18: Behavior unchanged — returned dict has correct shape and values
- INV-TIME-FILTER: Time filter correctly narrows results (since_days=1 vs since_days=365)
- INV-FALSY: since_days=0 is falsy — treated as "all time" (no time filter)
- INV-NONE: since_days=None returns all rows regardless of age

These tests use the test_db fixture (isolated temp SQLite database) and
directly seed user_feedback='up'/'down' rows via update_content_status().
To test time-window filtering, updated_at is backdated via direct SQL on
storage.conn (the live connection) — no production code is modified.
"""

import re
from pathlib import Path

from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXPECTED_KEYS = {
    "totals",
    "by_source",
    "topics_upvoted",
    "topics_downvoted",
    "for_llm_context",
    "time_period",
}

_EXPECTED_TOTALS_KEYS = {"upvotes", "downvotes", "total_votes"}


def _seed_upvote(storage: Storage, src_id: str, external_id: str) -> str:
    """Insert a content item and set user_feedback='up'. Returns content_id."""
    item = ContentItem(
        source_id=src_id,
        external_id=external_id,
        title=f"Article {external_id}",
        url=f"https://example.com/{external_id}",
        content="Test content",
        priority="normal",
        published_at=None,
    )
    content_id = storage.add_content(item)
    assert content_id is not None
    storage.update_content_status(content_id, user_feedback="up")
    return content_id


def _backdate(storage: Storage, content_id: str, days_ago: int) -> None:
    """Move updated_at backward by days_ago days.

    The update_content_timestamp trigger resets updated_at = CURRENT_TIMESTAMP
    on every UPDATE, so we must drop it, apply the backdate, then restore it.
    This is a test-side concern — no production code is modified.

    Reads the live trigger DDL from sqlite_master before dropping, so the
    recreated trigger always matches whatever schema.sql installed — no
    hardcoded copy that can drift.
    """
    conn = storage.conn
    cursor = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='trigger' AND name='update_content_timestamp'"
    )
    row = cursor.fetchone()
    assert row is not None, (
        "update_content_timestamp trigger not found in sqlite_master — "
        "was init_db() called before this helper?"
    )
    trigger_ddl = row[0]
    conn.execute("DROP TRIGGER IF EXISTS update_content_timestamp")
    conn.execute(
        "UPDATE content SET updated_at = datetime('now', ?) WHERE id = ?",
        (f"-{days_ago} days", content_id),
    )
    conn.execute(trigger_ddl)
    conn.commit()


# ---------------------------------------------------------------------------
# SC-17: Structural — no f-string SQL interpolation of since_days
# ---------------------------------------------------------------------------


def test_sc17_no_fstring_sql_interpolation_of_since_days() -> None:
    """
    INVARIANT: since_days must never be interpolated into an f-string SQL string.
    BREAKS: SQL injection vector reopened on any future caller that passes a string.

    Static analysis of storage.py — reads the file and searches for
    f-strings inside execute() that mention since_days.
    """
    storage_path = (
        Path(__file__).parent.parent.parent / "src" / "prismis_daemon" / "storage.py"
    )
    source = storage_path.read_text()

    # Pattern: execute(f"...{since_days}...") or f"""...{since_days}...""" in execute
    fstring_with_since_days = re.compile(
        r'execute\s*\(\s*f["\'].*?\{since_days\}.*?["\']', re.DOTALL
    )
    matches = fstring_with_since_days.findall(source)
    assert matches == [], (
        f"Found f-string SQL interpolation of since_days in execute() — "
        f"parameterization regression: {matches}"
    )

    # Also verify time_params is present (bound-parameter form exists)
    assert "time_params" in source, (
        "time_params not found in storage.py — parameterized form may have been removed"
    )


# ---------------------------------------------------------------------------
# SC-18: Behavior — dict shape is correct for since_days=30 with seeded data
# ---------------------------------------------------------------------------


def test_sc18_dict_shape_since_days_30(test_db: Path) -> None:
    """
    INVARIANT: get_feedback_statistics(since_days=30) returns dict with expected keys.
    BREAKS: Callers (orchestrator, context_auto_updater, API) fail to parse response.

    Seed 1 upvote row with updated_at within 30 days (default CURRENT_TIMESTAMP).
    Assert returned dict has all 6 expected top-level keys.
    Assert totals sub-dict has expected shape with upvotes >= 1.
    Assert time_period == "last 30 days".
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    _seed_upvote(storage, src_id, "recent-item")

    result = storage.get_feedback_statistics(since_days=30)

    assert isinstance(result, dict), f"Expected dict, got {type(result)}"
    assert set(result.keys()) == _EXPECTED_KEYS, (
        f"Dict keys mismatch. Expected {_EXPECTED_KEYS}, got {set(result.keys())}"
    )

    totals = result["totals"]
    assert set(totals.keys()) == _EXPECTED_TOTALS_KEYS, (
        f"totals keys mismatch: {set(totals.keys())}"
    )
    assert totals["upvotes"] >= 1, f"Expected >= 1 upvote, got {totals['upvotes']}"
    assert totals["total_votes"] >= 1

    assert result["time_period"] == "last 30 days", (
        f"Expected 'last 30 days', got '{result['time_period']}'"
    )

    assert isinstance(result["by_source"], list)
    assert isinstance(result["topics_upvoted"], list)
    assert isinstance(result["topics_downvoted"], list)


# ---------------------------------------------------------------------------
# INV-TIME-FILTER: Time window actually narrows results
# ---------------------------------------------------------------------------


def test_time_filter_narrows_results(test_db: Path) -> None:
    """
    INVARIANT: since_days=N excludes rows with updated_at older than N days.
    BREAKS: Bound parameter not honored by sqlite — since_days filter is a no-op.

    Seed 2 rows: one recent (updated_at = now), one backdated 60 days ago.
    With since_days=30, only the recent row should appear in totals.
    With since_days=365, both rows should appear.
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    recent_id = _seed_upvote(storage, src_id, "recent")
    old_id = _seed_upvote(storage, src_id, "old")

    # Backdate the old item to 60 days ago — outside a 30-day window
    _backdate(storage, old_id, 60)

    result_30 = storage.get_feedback_statistics(since_days=30)
    result_365 = storage.get_feedback_statistics(since_days=365)

    # 30-day window: only the recent item
    assert result_30["totals"]["upvotes"] == 1, (
        f"since_days=30 should count only the recent item. "
        f"Got {result_30['totals']['upvotes']} upvotes. "
        "If 2, the bound parameter is not being honored (time filter is a no-op)."
    )

    # 365-day window: both items
    assert result_365["totals"]["upvotes"] == 2, (
        f"since_days=365 should count both items. "
        f"Got {result_365['totals']['upvotes']} upvotes."
    )

    # Verify the recent_id is still accessible (no data corruption)
    _ = recent_id  # used in setup


# ---------------------------------------------------------------------------
# INV-NONE: since_days=None returns all rows regardless of age
# ---------------------------------------------------------------------------


def test_since_days_none_returns_all_rows(test_db: Path) -> None:
    """
    INVARIANT: since_days=None applies no time filter — all rows are counted.
    BREAKS: None path accidentally applies a filter, under-counting feedback.

    Seed 1 row backdated 400 days ago. since_days=None must include it.
    since_days=30 must exclude it (confirms the backdate worked).
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    old_id = _seed_upvote(storage, src_id, "very-old")
    _backdate(storage, old_id, 400)

    result_none = storage.get_feedback_statistics(since_days=None)
    result_30 = storage.get_feedback_statistics(since_days=30)

    assert result_none["totals"]["upvotes"] == 1, (
        f"since_days=None should include the 400-day-old row. "
        f"Got {result_none['totals']['upvotes']}."
    )
    assert result_none["time_period"] == "all time"

    assert result_30["totals"]["upvotes"] == 0, (
        f"since_days=30 should exclude the 400-day-old row. "
        f"Got {result_30['totals']['upvotes']}."
    )


# ---------------------------------------------------------------------------
# INV-FALSY: since_days=0 is falsy — behaves identically to None (all time)
# ---------------------------------------------------------------------------


def test_since_days_zero_is_falsy_no_filter(test_db: Path) -> None:
    """
    INVARIANT: since_days=0 is falsy in Python — treated as "all time", no filter.
    BREAKS: Any change that makes 0 truthy (e.g., explicit None check) silently
    changes behavior for callers relying on the 0 = all-time convention.

    Seed 1 row backdated 400 days ago.
    since_days=0 must include it (no filter applied).
    time_period must be "all time", not "last 0 days".
    """
    storage = Storage(test_db)
    src_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    old_id = _seed_upvote(storage, src_id, "old-zero-test")
    _backdate(storage, old_id, 400)

    result = storage.get_feedback_statistics(since_days=0)

    assert result["totals"]["upvotes"] == 1, (
        f"since_days=0 (falsy) should include all rows. "
        f"Got {result['totals']['upvotes']}."
    )
    assert result["time_period"] == "all time", (
        f"since_days=0 should yield time_period='all time', "
        f"got '{result['time_period']}'."
    )
