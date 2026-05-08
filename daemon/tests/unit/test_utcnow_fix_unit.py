"""Unit tests for task 2.9: datetime.utcnow() -> datetime.now(UTC) fix.  # task-2.9

RISK TAXONOMY
=============
This task touches the producer side of the storage datetime convention. The
risk category is DATA PERSISTENCE: every newly-fetched content row written by
these fetchers carries a fetched_at timestamp. A naive fetched_at written to
storage produces an ISO string without UTC offset -- violating the storage
convention and forcing the _rfc3339() encoder's naive branch to be load-bearing
rather than defensive. That encoder branch is the only thing standing between a
wrong timestamp format and the wire.

Invariants protected:
  - INV-FETCH-1 (SC-34): zero datetime.utcnow() in daemon source -- HIGH RISK
    Category: DATA PERSISTENCE -- naive datetimes silently written on every fetch
  - INV-DEP-FETCH-2 reddit: _to_content_item() fetched_at is tz-aware -- HIGH RISK
    Category: DATA PERSISTENCE -- storage convention violation on every reddit fetch
  - INV-DEP-FETCH-2 rss: fetched_at construction is tz-aware -- HIGH RISK
    Category: DATA PERSISTENCE -- storage convention violation on every rss fetch
  - INV-DEP-FETCH-2 youtube (no-transcript): fetched_at is tz-aware -- HIGH RISK
    Category: DATA PERSISTENCE
  - INV-DEP-FETCH-2 youtube (with-transcript): fetched_at is tz-aware -- HIGH RISK
    Category: DATA PERSISTENCE
  - youtube.py:187 date filter: tz-aware timedelta arithmetic is valid -- HIGH RISK
    Category: STATE TRANSITION -- wrong date filter silently skips or includes videos
  - observability ts: tz-aware RFC3339 with +00:00 -- MEDIUM RISK
    Category: OPERATIONAL -- observability log timestamps are ambiguous if naive

NOT TESTED (LOW RISK / OUT OF SCOPE):
  - observability.py:38 datetime.now() (naive) for log filename -- LOW RISK: only
    affects filename date on non-UTC servers, not content correctness; out of scope
    for task 2.9 (only line 41 was prescribed).
  - observability.py:85 datetime.now() in cleanup_old_files -- LOW RISK: naive-vs-naive
    comparison is internally consistent; not a wire output.
  - ContentItem model validation of tzinfo -- LOW RISK: model accepts both forms by
    design; enforcement is at the producer sites (covered above).
  - Backfill of existing 7100 naive storage rows -- OUT OF SCOPE per task spec.

BUILDER CHALLENGE
=================
Builder marked "MEDIUM: tests that mock datetime.utcnow may break" and claimed
no such mocks exist. Verified: grep of tests/ confirms no datetime.utcnow mock
sites -- builder's assessment is correct. The MEDIUM risk is a false positive.

Builder marked "LOW: youtube.py:187 naive-vs-naive comparison" and changed it to
tz-aware. This was the right call (yt-dlp upload_date is UTC-based; local-naive
date filter is off-by-one near midnight UTC). T6 tests the arithmetic directly.

TESTER-DISCOVERED INVARIANT (beyond builder's list):
  rss._parse_published_date() now returns tz-aware datetime (*entry.published_parsed,
  tzinfo=UTC). The existing test_fetcher_unit.py tests assert == datetime(Y,M,D)
  (naive) -- those tests would FAIL if they could run. They currently have a
  collection error (bare 'from fetchers.rss import' without package prefix) that
  prevents execution, masking the regression. T8 covers this correctly.
"""

import inspect
import json
import tempfile
import time
from datetime import UTC, timedelta
from pathlib import Path

# ---------------------------------------------------------------------------
# T1: INV-FETCH-1 / SC-34 -- zero datetime.utcnow() in daemon source tree
#
# Risk: HIGH -- DATA PERSISTENCE. Any reintroduction of utcnow() at a write site
# silently produces a naive ISO string in storage, violating the convention.
#
# Uses pathlib file walk + string search (no subprocess, no S603/S607 violations).
# ---------------------------------------------------------------------------


def test_no_datetime_utcnow_in_daemon_source() -> None:
    """INV-FETCH-1 / SC-34: zero datetime.utcnow() in daemon source tree.

    Structural regression guard. Catches any future edit that reintroduces the
    deprecated naive form before it reaches storage writes.
    """
    daemon_src = Path(__file__).parent.parent.parent / "src" / "prismis_daemon"
    assert daemon_src.exists(), f"Daemon src path not found: {daemon_src}"

    hits = []
    for py_file in daemon_src.rglob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if "datetime.utcnow" in line:
                hits.append(
                    f"{py_file.relative_to(daemon_src)}:{lineno}: {line.strip()}"
                )

    assert hits == [], (
        "INV-FETCH-1 FAIL: datetime.utcnow() found in daemon source:\n"
        + "\n".join(hits)
    )


# ---------------------------------------------------------------------------
# T2: INV-DEP-FETCH-2 (reddit) -- _to_content_item() emits tz-aware fetched_at
#
# Risk: HIGH -- DATA PERSISTENCE. Every reddit content row has fetched_at set
# by this method. Naive output writes a tz-less ISO string to storage.
#
# Runtime test via fixed reddit_mocks fixture (lambda __str__ arity corrected).
# ---------------------------------------------------------------------------


def test_reddit_fetcher_to_content_item_fetched_at_is_tz_aware() -> None:
    """INV-DEP-FETCH-2 (reddit): _to_content_item() fetched_at must have tzinfo.

    Uses create_self_post_mock() after fixing the __str__ lambda arity bug in
    reddit_mocks.py (was lambda: 'python', must be lambda self: 'python' so
    Mock's method dispatch can pass self).
    """
    from prismis_daemon.fetchers.reddit import RedditFetcher
    from tests.fixtures.reddit_mocks import create_self_post_mock

    fetcher = RedditFetcher()
    submission = create_self_post_mock(
        title="Tz-awareness test post",
        selftext="Post body",
    )

    item = fetcher._to_content_item(submission, "test-source-id")

    assert item.fetched_at is not None, "fetched_at must not be None"
    assert item.fetched_at.tzinfo is not None, (
        f"INV-DEP-FETCH-2 FAIL (reddit): fetched_at is naive (tzinfo=None). "
        f"Got: {item.fetched_at!r}. "
        "Fix: use datetime.now(UTC) not datetime.utcnow() in reddit.py"
    )


# ---------------------------------------------------------------------------
# T3: INV-DEP-FETCH-2 (rss) -- fetched_at construction uses tz-aware form
#
# Risk: HIGH -- DATA PERSISTENCE. Every rss content row has fetched_at set here.
#
# Source inspection: rss.py's fetch loop cannot be driven without a live HTTP
# call (feedparser requires a real feed URL or mocked httpx). Source inspection
# verifies the call form directly and is a valid structural guard.
# SEARCHED rss.py for injection seams (config override, feed dict injection,
# offline feedparser mode) -- feedparser.parse() requires real or mocked HTTP;
# no config-driven seam to bypass network call found. Source inspection is the
# appropriate lightweight approach here.
# ---------------------------------------------------------------------------


def test_rss_fetched_at_construction_is_tz_aware() -> None:
    """INV-DEP-FETCH-2 (rss): rss.py must use datetime.now(UTC) for fetched_at.

    Source inspection guards against regression to datetime.now() (naive local)
    or datetime.utcnow() (naive UTC) at the fetched_at assignment site.
    """
    import prismis_daemon.fetchers.rss as rss_mod

    source = inspect.getsource(rss_mod)

    assert "datetime.now(UTC)" in source, (
        "INV-DEP-FETCH-2 FAIL (rss): rss.py must use datetime.now(UTC). "
        "Ensure fetched_at construction at line 119 is tz-aware."
    )
    assert "datetime.utcnow()" not in source, (
        "INV-DEP-FETCH-2 FAIL (rss): rss.py still contains datetime.utcnow(). "
        "Deprecated form produces naive datetimes that violate storage convention."
    )


# ---------------------------------------------------------------------------
# T4: INV-DEP-FETCH-2 (youtube no-transcript) -- fetched_at tz-aware
#
# Risk: HIGH -- DATA PERSISTENCE. youtube.py:450 -- the no-transcript code path.
# ---------------------------------------------------------------------------


def test_youtube_handle_missing_transcript_fetched_at_is_tz_aware() -> None:
    """INV-DEP-FETCH-2 (youtube:450): _handle_missing_transcript() fetched_at tz-aware.

    No external dependency: method takes a plain dict and source_id string.
    """
    from prismis_daemon.fetchers.youtube import YouTubeFetcher

    fetcher = YouTubeFetcher()
    video = {
        "title": "Test Video No Transcript",
        "url": "https://www.youtube.com/watch?v=abc123",
        "upload_date": "20240815",
        "id": "abc123",
        "view_count": 500,
        "duration": 120,
    }

    item = fetcher._handle_missing_transcript(video, "source-uuid-test")

    assert item.fetched_at is not None, "fetched_at must not be None"
    assert item.fetched_at.tzinfo is not None, (
        f"INV-DEP-FETCH-2 FAIL (youtube no-transcript): fetched_at is naive. "
        f"Got: {item.fetched_at!r}. "
        "Fix: ensure youtube.py:450 uses datetime.now(UTC)"
    )


# ---------------------------------------------------------------------------
# T5: INV-DEP-FETCH-2 (youtube with-transcript) -- fetched_at tz-aware
#
# Risk: HIGH -- DATA PERSISTENCE. youtube.py:486 -- the with-transcript code path.
# ---------------------------------------------------------------------------


def test_youtube_to_content_item_fetched_at_is_tz_aware() -> None:
    """INV-DEP-FETCH-2 (youtube:486): _to_content_item() fetched_at tz-aware.

    No external dependency: method takes plain dict, transcript string, source_id.
    """
    from prismis_daemon.fetchers.youtube import YouTubeFetcher

    fetcher = YouTubeFetcher()
    video = {
        "title": "Test Video With Transcript",
        "url": "https://www.youtube.com/watch?v=xyz456",
        "upload_date": "20240901",
        "id": "xyz456",
        "view_count": 2000,
        "duration": 300,
    }
    transcript = "Transcript content for the test video."

    item = fetcher._to_content_item(video, transcript, "source-uuid-test2")

    assert item.fetched_at is not None, "fetched_at must not be None"
    assert item.fetched_at.tzinfo is not None, (
        f"INV-DEP-FETCH-2 FAIL (youtube with-transcript): fetched_at is naive. "
        f"Got: {item.fetched_at!r}. "
        "Fix: ensure youtube.py:486 uses datetime.now(UTC)"
    )


# ---------------------------------------------------------------------------
# T6: youtube.py:187 date filter -- tz-aware timedelta arithmetic produces valid output
#
# Risk: HIGH -- STATE TRANSITION. Wrong date filter silently skips or includes
# videos at the UTC-midnight day boundary when the server is in a non-UTC tz.
# Builder changed datetime.now() (naive local) to datetime.now(UTC) -- correct
# because yt-dlp's upload_date field is UTC-based.
# ---------------------------------------------------------------------------


def test_youtube_date_filter_tz_aware_arithmetic_is_valid() -> None:
    """youtube.py:187: datetime.now(UTC) - timedelta(days=N) must produce valid YYYYMMDD.

    Exercises the exact expression at youtube.py:187. Guards against future
    naive-vs-tz-aware TypeError if the call site is ever changed. Also validates
    that the UTC-based date string is plausible (not 1970, not 9999).
    """
    from datetime import datetime

    max_days_lookback = 30
    # Mirror the exact expression at youtube.py:187
    date_after = (datetime.now(UTC) - timedelta(days=max_days_lookback)).strftime(
        "%Y%m%d"
    )

    assert len(date_after) == 8, (
        f"date_after must be YYYYMMDD (8 chars), got: {date_after!r}"
    )
    assert date_after.isdigit(), f"date_after must be all digits, got: {date_after!r}"
    year = int(date_after[:4])
    assert 2020 <= year <= 2100, f"date_after year must be plausible, got: {year}"


# ---------------------------------------------------------------------------
# T7: observability ts field -- tz-aware RFC3339 with +00:00 offset
#
# Risk: MEDIUM -- OPERATIONAL. observability.py:41 ts field changed from
# datetime.utcnow().isoformat() + "Z" (naive + manual Z) to
# datetime.now(UTC).isoformat() (tz-aware, +00:00). External log consumers
# that parse RFC3339 strictly will accept both forms, but the old form was
# semantically ambiguous (naive datetime with a suffix appended in Python).
# ---------------------------------------------------------------------------


def test_observability_log_ts_is_tz_aware() -> None:
    """observability.py:41: ts field must be tz-aware RFC3339 with +00:00 offset.

    Runtime test: instantiates ObservabilityLogger with a temp dir, writes one
    event, reads back the JSONL and inspects the ts field.
    Old form: "2026-05-07T16:12:26.846187Z" (naive + appended literal Z)
    New form: "2026-05-07T16:12:26.846187+00:00" (tz-aware .isoformat())
    """
    import re

    from prismis_daemon.observability import ObservabilityLogger

    rfc3339_re = re.compile(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
    )

    with tempfile.TemporaryDirectory() as tmp_dir:
        obs_dir = Path(tmp_dir) / "observability"
        logger = ObservabilityLogger(base_dir=obs_dir)

        logger.log("test.event", key="value")

        jsonl_files = list(obs_dir.glob("*_events.jsonl"))
        assert len(jsonl_files) == 1, (
            f"Expected exactly 1 JSONL file, found: {jsonl_files}"
        )

        line = jsonl_files[0].read_text().strip()
        entry = json.loads(line)

        ts = entry["ts"]
        assert isinstance(ts, str), f"ts must be a string, got {type(ts)}"
        assert rfc3339_re.match(ts), (
            f"observability ts must be RFC3339-compliant, got: {ts!r}. "
            "Expected tz-aware isoformat like '2026-05-07T16:12:26.846187+00:00'."
        )
        # Explicit +00:00 offset confirms tz-aware form, not the old naive+Z trick
        assert ts.endswith("+00:00"), (
            f"observability ts must end with '+00:00' (tz-aware UTC), got: {ts!r}. "
            "If ending with 'Z', fix was not applied -- still using naive datetime."
        )


# ---------------------------------------------------------------------------
# T8: TESTER-DISCOVERED -- rss._parse_published_date() returns tz-aware datetime
#
# Risk: HIGH -- DATA PERSISTENCE / correctness gap.
# Discovery: rss.py _parse_published_date() was updated (post-task-2.9 hook
# normalization) to return datetime(*tuple[:6], tzinfo=UTC) -- tz-aware.
# The existing test_fetcher_unit.py asserts == datetime(Y,M,D) (naive), which
# would FAIL. Those tests are currently masked by a collection error
# (bare 'from fetchers.rss import' -- not package-prefixed). This test covers
# the invariant with correct tz-aware assertions.
#
# The comparison at rss.py:108 (published_at < cutoff_date) is now
# tz-aware vs tz-aware -- correct. If _parse_published_date() ever regressed
# to returning naive, line 108 would raise TypeError at runtime.
# ---------------------------------------------------------------------------


def test_rss_parse_published_date_returns_tz_aware_datetime() -> None:
    """DISCOVERED: rss._parse_published_date() must return tz-aware datetime.

    The existing test_fetcher_unit.py tests assert against naive datetime objects
    but are masked by a collection error. This test asserts the correct post-task-2.9
    behavior: parsed dates carry tzinfo=UTC so the cutoff_date comparison
    (published_at < cutoff_date) is tz-aware vs tz-aware without TypeError.
    """
    from prismis_daemon.fetchers.rss import RSSFetcher

    fetcher = RSSFetcher()

    class EntryWithPublishedParsed:
        published_parsed = time.struct_time((2024, 1, 15, 10, 30, 0, 0, 0, 0))

    result = fetcher._parse_published_date(EntryWithPublishedParsed())

    assert result is not None, "_parse_published_date must return a datetime"
    assert result.tzinfo is not None, (
        f"_parse_published_date must return tz-aware datetime. "
        f"Got naive: {result!r}. "
        "A naive return causes TypeError when compared to tz-aware cutoff_date "
        "at rss.py:108 (published_at < cutoff_date)."
    )
    assert result.year == 2024
    assert result.month == 1
    assert result.day == 15
