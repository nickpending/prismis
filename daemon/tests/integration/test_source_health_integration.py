"""A failing source is retried less often, never switched off, and every source's health
is classified by the daemon from what its fetches served and stored in its row.

Invariants protected:
  - a failed fetch never writes `active`; the next fetch waits fetch_interval x 2^(n-1)
    after the nth consecutive failure, capped at 1440 minutes, landing on the cycle
    nearest that time, and one successful fetch returns the source to every cycle
  - health is broken at the failure whose wait first reaches the cap, empty when the
    last successful fetch served zero entries, dormant when the newest item the feed
    served is more than 90 days old, healthy otherwise; the trigger-maintained
    attention flag is set exactly while an active source is flagged and not kept
  - keep answers one state and a URL change resets the source's fetch record

Real collaborators throughout: the production orchestrator from `build_orchestrator`
over real Storage and a sealed database, a real RSSFetcher over real sockets to a local
HTTP server, and the real daemon API on a loopback socket. The LLM is the local stub
(the one permitted stand-in); the clock is injected through the orchestrator's `clock`
attribute, so a test runs days of cycles in seconds.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from rich.console import Console

from conftest import (
    TEST_API_KEY,
    LocalHttpServer,
    configure_local_services,
    make_config,
)
from prismis_daemon.models import ContentItem
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.verify_chain import build_orchestrator

_RSS = "application/rss+xml"


def _feed(name: str, dates: list[datetime], base: str) -> bytes:
    """A valid RSS 2.0 document with one item per date; guids are unique per feed."""
    items = "".join(
        f"<item><title>{name} item {i}</title><link>{base}/{name}-article-{i}</link>"
        f"<description>Body of {name} item {i}.</description>"
        f'<guid isPermaLink="false">{name}-{i}</guid>'
        f"<pubDate>{format_datetime(date)}</pubDate></item>"
        for i, date in enumerate(dates)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
        f"<title>{name}</title><link>{base}/</link><description>local</description>"
        f"{items}</channel></rss>"
    ).encode()


class _Clock:
    """The cycle clock a test advances by hand."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@dataclass
class _Harness:
    """One sealed database, one local feed server, one test clock."""

    storage: Storage
    server: LocalHttpServer
    clock: _Clock
    orchestrators: dict[int, DaemonOrchestrator] = field(default_factory=dict)

    def serve(self, name: str, status: int, body: bytes = b"") -> str:
        """Route /<name>.xml and return its URL."""
        self.server.routes[f"/{name}.xml"] = (status, _RSS, body)
        return f"{self.server.base_url}/{name}.xml"

    def serve_feed(self, name: str, dates: list[datetime]) -> str:
        return self.serve(name, 200, _feed(name, dates, self.server.base_url))

    def add(self, name: str) -> str:
        """Add an RSS source pointed at /<name>.xml (routed or not)."""
        return self.storage.add_source(
            f"{self.server.base_url}/{name}.xml", "rss", name
        )

    def run(self, interval: int) -> None:
        """One run_once of the production orchestrator at the current clock."""
        orchestrator = self.orchestrators.get(interval)
        if orchestrator is None:
            orchestrator = build_orchestrator(
                make_config(fetch_interval=interval), self.storage, Console(quiet=True)
            )
            orchestrator.clock = self.clock
            self.orchestrators[interval] = orchestrator
        orchestrator.run_once()

    def row(self, source_id: str) -> dict[str, Any]:
        return dict(
            self.storage.conn.execute(
                "SELECT * FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
        )

    def hits(self, name: str) -> int:
        return self.server.requests.count(f"/{name}.xml")

    def advance_to_due(self, source_id: str, interval: int) -> None:
        """Move the clock to the source's next_fetch_at, or one interval when null."""
        due = self.row(source_id).get("next_fetch_at")
        if due:
            self.clock.now = datetime.fromisoformat(due)
        else:
            self.clock.now += timedelta(minutes=interval)


@pytest.fixture
def harness(
    test_db: Path,
    local_http_server: LocalHttpServer,
    local_pipeline_stub: str,
    isolated_xdg_env: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[_Harness]:
    # Items a recent feed stores are summarized by the local LLM stub; the embedder has
    # no model in the sealed HOME, and offline it fails at once instead of downloading.
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    storage = Storage(test_db)
    now = datetime.now(UTC).replace(microsecond=0)
    try:
        yield _Harness(storage, local_http_server, _Clock(now))
    finally:
        storage.close()


def _days_ago(days: float) -> datetime:
    return datetime.now(UTC).replace(microsecond=0) - timedelta(days=days)


# Entries older than the default 30-day lookback but newer than 90 days: a successful
# fetch that stores nothing and needs no LLM, whose source is healthy.
_QUIET_BUT_ALIVE = [_days_ago(45), _days_ago(50), _days_ago(60)]
_RECENT = [_days_ago(0.5), _days_ago(1), _days_ago(2)]


def test_a_failing_source_is_never_switched_off(harness: _Harness) -> None:
    """
    INVARIANT: six consecutive failed fetches leave the source active and every one of
               them reaches the feed
    BREAKS: the five-strike UPDATE pauses the source after one bad day, silently and
            permanently, and its sixth request never arrives
    """
    harness.serve("dead", 404)
    source = harness.add("dead")

    for run in range(1, 7):
        if run > 1:
            harness.advance_to_due(source, 120)
        harness.run(120)
        row = harness.row(source)
        assert row["active"] == 1, f"run {run} wrote active: {row}"

    assert harness.hits("dead") == 6, "every one of the six runs must request the feed"
    assert harness.row(source)["error_count"] == 6


def _run_failing_cycles(
    harness: _Harness, name: str, source: str, last_minute: int
) -> list[int]:
    """Run a cycle every 360 minutes from 0 to last_minute; return the minutes whose
    cycle requested the feed, checking next_fetch_at after each failure."""
    start = harness.clock.now
    requested = []
    for minute in range(0, last_minute + 1, 360):
        harness.clock.now = start + timedelta(minutes=minute)
        before = harness.hits(name)
        failures_before = harness.row(source)["error_count"]
        harness.run(360)
        if harness.hits(name) > before:
            requested.append(minute)
            failures = harness.row(source)["error_count"]
            if failures > failures_before:
                wait = min(360 * 2 ** (failures - 1), 1440)
                due = harness.row(source).get("next_fetch_at")
                assert due is not None, f"failure {failures} set no next_fetch_at"
                assert datetime.fromisoformat(due) == harness.clock.now + timedelta(
                    minutes=wait
                ), f"failure {failures} at minute {minute} is due {due}"
    return requested


def test_failures_back_off_doubling_from_the_interval_to_a_daily_cap(
    harness: _Harness,
) -> None:
    """
    INVARIANT: at fetch_interval 360 a source answering 500 is requested at minutes 0,
               360, 1080, 2520 and 3960 of 0..4320, each failure due at its cycle's
               start plus min(360 x 2^(n-1), 1440)
    BREAKS: a dead source is fetched every cycle forever, or (without the cap) waits
            ever longer and a recovered site is picked up days late
    """
    harness.serve("flaky", 500)
    source = harness.add("flaky")

    requested = _run_failing_cycles(harness, "flaky", source, 4320)

    assert requested == [0, 360, 1080, 2520, 3960]


def test_one_success_returns_a_backed_off_source_to_every_cycle(
    harness: _Harness,
) -> None:
    """
    INVARIANT: after four failures, the success at its due cycle clears error_count,
               last_error and next_fetch_at, and the next cycle requests it again
    BREAKS: a recovered source stays on its long wait and its new items arrive late
    """
    harness.serve("recovers", 500)
    source = harness.add("recovers")
    assert _run_failing_cycles(harness, "recovers", source, 2520) == [
        0,
        360,
        1080,
        2520,
    ]
    waiting = harness.row(source)
    assert waiting["error_count"] == 4
    assert waiting.get("next_fetch_at") is not None, "four failures must set a wait"

    harness.serve_feed("recovers", _RECENT)
    harness.advance_to_due(source, 360)
    before = harness.hits("recovers")
    harness.run(360)
    assert harness.hits("recovers") == before + 1, "the due cycle must fetch"
    row = harness.row(source)
    assert (row["error_count"], row["last_error"], row["next_fetch_at"]) == (
        0,
        None,
        None,
    )

    harness.clock.now += timedelta(minutes=360)
    harness.run(360)
    assert harness.hits("recovers") == before + 2, "the next cycle must fetch again"


def test_a_retry_lands_on_the_cycle_nearest_its_due_time(harness: _Harness) -> None:
    """
    INVARIANT: with next_fetch_at T and fetch_interval 120, a cycle starting at T - 1s
               or T - 59m fetches the source and one at T - 61m does not
    BREAKS: a strict next_fetch_at <= start check skips a cycle that starts a second
            early and pushes the retry a whole interval late
    """
    outcomes = {}
    for label, early in (
        ("T-1s", timedelta(seconds=1)),
        ("T-59m", timedelta(minutes=59)),
        ("T-61m", timedelta(minutes=61)),
    ):
        name = f"due-{label}"
        harness.serve(name, 404)
        source = harness.add(name)
        failed_at = harness.clock.now
        harness.run(120)
        due = harness.row(source).get("next_fetch_at")
        target = (
            datetime.fromisoformat(due) if due else failed_at + timedelta(minutes=120)
        )

        harness.clock.now = target - early
        before = harness.hits(name)
        harness.run(120)
        outcomes[label] = harness.hits(name) > before
        harness.storage.remove_source(source)
        harness.clock.now = failed_at

    assert outcomes == {"T-1s": True, "T-59m": True, "T-61m": False}


def _fail_after_one_success(
    harness: _Harness, name: str, interval: int, failures: int
) -> tuple[str, list[dict[str, Any]]]:
    """A source that fetched once, then fails `failures` times at its due cycles;
    returns it and its row after each failure."""
    harness.serve_feed(name, _QUIET_BUT_ALIVE)
    source = harness.add(name)
    harness.run(interval)
    harness.serve(name, 404)
    rows = []
    for _ in range(failures):
        harness.advance_to_due(source, interval)
        harness.run(interval)
        rows.append(harness.row(source))
    return source, rows


@pytest.mark.parametrize(("interval", "broken_at"), [(120, 5), (30, 7)])
def test_broken_is_the_failure_whose_wait_first_reaches_the_cap(
    harness: _Harness, interval: int, broken_at: int
) -> None:
    """
    INVARIANT: a source that fetched once stays healthy (attention 0) through failure
               broken_at - 1 and reads broken (attention 1) at failure broken_at: 5 at
               fetch_interval 120, 7 at 30
    BREAKS: a fixed count calls a 30-minute source broken after two and a half hours, or
            nothing ever says a dead source is dead
    """
    _source, rows = _fail_after_one_success(harness, "dying", interval, broken_at)

    before, at = rows[broken_at - 2], rows[broken_at - 1]
    assert (before["error_count"], before.get("health"), before.get("attention")) == (
        broken_at - 1,
        "healthy",
        0,
    )
    assert (at["error_count"], at.get("health"), at.get("attention")) == (
        broken_at,
        "broken",
        1,
    )


def test_a_feed_serving_zero_entries_is_empty(harness: _Harness) -> None:
    """
    INVARIANT: a successful fetch of a valid feed with no entries reads entries_served
               0, health empty and attention 1
    BREAKS: a feed that died quietly reads exactly like a healthy one
    """
    harness.serve_feed("hollow", [])
    source = harness.add("hollow")

    harness.run(120)

    row = harness.row(source)
    assert row["error_count"] == 0
    assert (row.get("entries_served"), row.get("health"), row.get("attention")) == (
        0,
        "empty",
        1,
    )


def test_a_feed_whose_newest_entry_is_older_than_90_days_is_dormant(
    harness: _Harness,
) -> None:
    """
    INVARIANT: eight entries all older than 90 days store nothing and read
               entries_served 8, newest_item_at the newest entry's date, health dormant
               and attention 1
    BREAKS: the lookback filter hides every entry and a quiet blog is indistinguishable
            from a healthy one, or from an empty one
    """
    dates = [_days_ago(120 + 10 * i) for i in range(8)]
    harness.serve_feed("quiet", dates)
    source = harness.add("quiet")

    harness.run(120)

    stored = harness.storage.conn.execute(
        "SELECT COUNT(*) FROM content WHERE source_id = ?", (source,)
    ).fetchone()[0]
    assert stored == 0
    row = harness.row(source)
    newest = row.get("newest_item_at")
    assert newest is not None and datetime.fromisoformat(newest) == dates[0]
    assert (row.get("entries_served"), row.get("health"), row.get("attention")) == (
        8,
        "dormant",
        1,
    )


def test_health_reads_what_the_feed_served_before_the_pipelines_filters(
    harness: _Harness,
) -> None:
    """
    INVARIANT: a feed whose entries are all older than the lookback but newer than 90
               days, and a feed whose recent entries are all already stored, both read
               healthy with attention 0
    BREAKS: judging from the items left after the lookback and dedup filters calls both
            of them empty
    """
    harness.serve_feed("lookback", _QUIET_BUT_ALIVE)
    old = harness.add("lookback")
    harness.serve_feed("known", _RECENT)
    known = harness.add("known")
    for i, date in enumerate(_RECENT):
        harness.storage.add_content(
            ContentItem(
                source_id=known,
                external_id=f"known-{i}",
                title=f"known item {i}",
                url=f"{harness.server.base_url}/known-article-{i}",
                content="Already stored.",
                published_at=date,
            )
        )

    harness.run(120)

    old_row, known_row = harness.row(old), harness.row(known)
    for row in (old_row, known_row):
        assert (row.get("health"), row.get("attention")) == ("healthy", 0), row
    assert old_row["entries_served"] == len(_QUIET_BUT_ALIVE)
    assert datetime.fromisoformat(old_row["newest_item_at"]) == _QUIET_BUT_ALIVE[0]


def test_a_broken_source_that_recovers_clears_its_own_warning(
    harness: _Harness,
) -> None:
    """
    INVARIANT: a source broken at its fifth failure reads healthy, error_count 0 and
               attention 0 after the cycle in which its feed answers again
    BREAKS: a warning waits for the operator to clear a problem that already went away
    """
    source, rows = _fail_after_one_success(harness, "back", 120, 5)
    assert rows[-1].get("health") == "broken"

    harness.serve_feed("back", _RECENT)
    harness.advance_to_due(source, 120)
    harness.run(120)

    row = harness.row(source)
    assert (row.get("health"), row["error_count"], row.get("attention")) == (
        "healthy",
        0,
        0,
    )


def _patch(
    base_url: str, path: str, json: dict[str, Any] | None = None
) -> httpx.Response:
    return httpx.patch(
        f"{base_url}{path}", json=json, headers={"X-API-Key": TEST_API_KEY}, timeout=30
    )


def _api_source(base_url: str, source_id: str) -> dict[str, Any]:
    response = httpx.get(
        f"{base_url}/api/sources", headers={"X-API-Key": TEST_API_KEY}, timeout=30
    )
    assert response.status_code == 200, response.text
    (source,) = [s for s in response.json()["data"]["sources"] if s["id"] == source_id]
    return source


def test_a_keep_ends_when_the_health_it_answered_changes(
    harness: _Harness, live_web_app: str
) -> None:
    """
    INVARIANT: an empty source kept through the API reads healthy with kept_health null
               once its feed serves again, and empty with attention 1 when it next goes
               empty
    BREAKS: a keep remembered forever hides the next time the feed goes quiet
    """
    harness.serve_feed("sometimes", [])
    source = harness.add("sometimes")
    harness.run(120)

    kept = _patch(live_web_app, f"/api/sources/{source}/keep")
    assert kept.status_code == 200, kept.text
    assert harness.row(source).get("kept_health") == "empty"

    harness.serve_feed("sometimes", _RECENT)
    harness.clock.now += timedelta(minutes=120)
    harness.run(120)
    row = harness.row(source)
    assert (row.get("health"), row.get("kept_health")) == ("healthy", None)

    harness.serve_feed("sometimes", [])
    harness.clock.now += timedelta(minutes=120)
    harness.run(120)
    row = harness.row(source)
    assert (row.get("health"), row.get("attention")) == ("empty", 1)


def test_keep_and_unkeep_routes(harness: _Harness, live_web_app: str) -> None:
    """
    INVARIANT: keep on a healthy source answers 400 naming that there is nothing to keep
               and leaves kept_health null; keep on an unknown id answers 404; unkeep
               on a kept source clears kept_health and attention reads true again
    BREAKS: a keep on a source that needs nothing silently stores a stale state, or an
            unkeep leaves a hidden warning hidden
    """
    harness.serve_feed("fine", _QUIET_BUT_ALIVE)
    fine = harness.add("fine")
    harness.serve_feed("bare", [])
    bare = harness.add("bare")
    harness.run(120)
    assert harness.row(fine).get("health") == "healthy"

    refused = _patch(live_web_app, f"/api/sources/{fine}/keep")
    assert refused.status_code == 400, refused.text
    assert "nothing to keep" in refused.json()["message"]
    assert harness.row(fine).get("kept_health") is None

    unknown = _patch(live_web_app, "/api/sources/no-such-source/keep")
    assert unknown.status_code == 404, unknown.text

    assert _patch(live_web_app, f"/api/sources/{bare}/keep").status_code == 200
    assert _api_source(live_web_app, bare)["attention"] is False
    unkept = _patch(live_web_app, f"/api/sources/{bare}/unkeep")
    assert unkept.status_code == 200, unkept.text
    assert harness.row(bare).get("kept_health") is None
    assert _api_source(live_web_app, bare)["attention"] is True


def test_a_url_change_is_a_fix_and_a_rename_is_not(
    harness: _Harness, live_web_app: str
) -> None:
    """
    INVARIANT: changing a broken, kept source's URL resets error_count, next_fetch_at,
               health and kept_health (attention 0) and the next cycle judges the new
               URL; changing only another broken source's name resets nothing
    BREAKS: the fixed source keeps its five failures and stale warning, or a rename
            wipes the evidence of a source that is still dead
    """
    harness.serve("moved", 404)
    moved = harness.add("moved")
    harness.serve("renamed", 404)
    renamed = harness.add("renamed")
    harness.run(120)
    for _ in range(4):
        harness.advance_to_due(moved, 120)
        harness.run(120)
    assert harness.row(moved).get("health") == "broken"
    assert _patch(live_web_app, f"/api/sources/{moved}/keep").status_code == 200
    before = harness.row(moved)
    assert (before["error_count"], before.get("kept_health")) == (5, "broken")
    assert before.get("next_fetch_at") is not None

    new_url = harness.serve_feed("new-home", _RECENT)
    fixed = _patch(live_web_app, f"/api/sources/{moved}", {"url": new_url})
    assert fixed.status_code == 200, fixed.text
    row = harness.row(moved)
    assert (
        row["error_count"],
        row.get("next_fetch_at"),
        row.get("health"),
        row.get("kept_health"),
        row.get("attention"),
    ) == (0, None, None, None, 0)

    untouched = harness.row(renamed)
    assert (
        _patch(
            live_web_app, f"/api/sources/{renamed}", {"name": "Still dead"}
        ).status_code
        == 200
    )
    after = harness.row(renamed)
    assert after["name"] == "Still dead"
    for column in ("error_count", "health", "next_fetch_at"):
        assert after.get(column) == untouched.get(column), column

    before_hits = harness.hits("new-home")
    harness.run(120)
    assert harness.hits("new-home") > before_hits, "the cycle must fetch the new URL"
    assert harness.row(moved).get("health") == "healthy"


def test_the_source_list_carries_every_stored_health_field(
    harness: _Harness, live_web_app: str
) -> None:
    """
    INVARIANT: GET /api/sources returns health, attention, entries_served,
               newest_item_at, next_fetch_at and kept_health matching each row, and
               every field it carried before keeps its name and value
    BREAKS: a client has to guess health from error_count again, or an old client's
            fields move
    """
    harness.serve("gone", 404)
    gone = harness.add("gone")
    harness.serve_feed("void", [])
    void = harness.add("void")
    harness.serve_feed("ok", _QUIET_BUT_ALIVE)
    ok = harness.add("ok")
    # At fetch_interval 1440 the first failure already waits the cap: broken at once.
    harness.run(1440)

    healths = {}
    for source_id in (gone, void, ok):
        row, listed = harness.row(source_id), _api_source(live_web_app, source_id)
        healths[source_id] = listed.get("health")
        assert listed.get("health") == row.get("health")
        assert listed.get("attention") is bool(row.get("attention"))
        assert listed.get("entries_served") == row.get("entries_served")
        assert listed.get("kept_health") == row.get("kept_health")
        for api_field, column in (
            ("newest_item_at", "newest_item_at"),
            ("next_fetch_at", "next_fetch_at"),
            ("last_fetched", "last_fetched_at"),
        ):
            stored, served = row.get(column), listed.get(api_field)
            assert (served is None) == (stored is None), api_field
            if stored is not None and served is not None:
                assert datetime.fromisoformat(served) == datetime.fromisoformat(stored)
        for name in ("id", "url", "type", "name", "error_count", "last_error"):
            assert listed[name] == row[name], name
        assert listed["active"] is bool(row["active"])
        assert listed["naughty"] is bool(row["naughty"])

    assert healths == {gone: "broken", void: "empty", ok: "healthy"}
