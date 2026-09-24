"""RedditFetcher's client is bounded and makes no request it was not asked to (#67).

Invariants protected:
  - a fetch against a peer that never answers ends near the fetcher's budget, not
    prawcore's 16 seconds per attempt
  - the budget restarts on every fetch, because the client lives for the whole process
  - constructing the fetcher contacts nothing (PRAW's pypi.org update check is off)

Every request is routed through a local proxy port this file owns, so nothing reaches
Reddit or PyPI. Real sockets, a real clock, the real PRAW client; nothing is mocked.
"""

import socket
import threading
import time
from collections.abc import Iterator

import praw
import pytest

from prismis_daemon.fetchers.reddit import RedditFetcher

from conftest import make_config

_SOURCE = {"url": "https://www.reddit.com/r/python", "id": "source-1"}


class _Listener:
    """Accepts connections, counts them, and never answers."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.address = "{}:{}".format(*self._sock.getsockname())
        self.accepted: list[socket.socket] = []
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.accepted.append(conn)

    def close(self) -> None:
        self._sock.close()
        for conn in self.accepted:
            conn.close()


@pytest.fixture
def silent_proxy(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Listener]:
    """Route every outbound request at a counting listener that never replies."""
    listener = _Listener()
    monkeypatch.setenv("HTTP_PROXY", f"http://{listener.address}")
    monkeypatch.setenv("HTTPS_PROXY", f"http://{listener.address}")
    monkeypatch.setenv("NO_PROXY", "")
    yield listener
    listener.close()


def _fetcher(fetch_budget: float = 1.0) -> RedditFetcher:
    return RedditFetcher(
        config=make_config(
            reddit_client_id="prismis-not-a-real-client-id",
            reddit_client_secret="prismis-not-a-real-client-secret",
        ),
        fetch_budget=fetch_budget,
    )


def test_a_stalled_reddit_is_cut_off_at_the_fetch_budget(
    silent_proxy: _Listener,
) -> None:
    """
    INVARIANT: A fetch against a peer that never answers ends near fetch_budget
    BREAKS: prawcore's 16s per attempt, three attempts, applies to every request, so one
            stalled subreddit holds the daemon's cycle for minutes
    NOTE: The upper bound allows for prawcore's two retry sleeps (at most 6s together),
          which happen outside the transport; without the budget the first attempt
          alone takes 16s.
    """
    fetcher = _fetcher(fetch_budget=1.0)

    started = time.monotonic()
    with pytest.raises(Exception):  # noqa: B017 — any failure; the clock is the claim
        fetcher.fetch_content(_SOURCE)
    elapsed = time.monotonic() - started

    assert elapsed >= 0.8, f"returned in {elapsed:.2f}s, before waiting on the socket"
    assert elapsed < 10, f"took {elapsed:.2f}s, so the 16s prawcore default applied"


def test_the_budget_restarts_on_every_fetch(silent_proxy: _Listener) -> None:
    """
    INVARIANT: A fetch made after the previous budget ran out still reaches the network
    BREAKS: The deadline is armed once at construction, so every fetch after the first
            budget has elapsed is refused before it sends anything, forever
    """
    fetcher = _fetcher(fetch_budget=0.3)
    time.sleep(0.5)  # the construction-time deadline has now passed

    with pytest.raises(Exception):  # noqa: B017 — the connection count is the claim
        fetcher.fetch_content(_SOURCE)

    assert silent_proxy.accepted, "no request reached the network: budget not rearmed"


def test_constructing_the_fetcher_contacts_nothing(
    silent_proxy: _Listener, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: Building the fetcher makes no outbound request
    BREAKS: PRAW's constructor fetches pypi.org/pypi/praw/json once per process, an
            unbounded call to a third party on the daemon's startup path
    """
    monkeypatch.setattr(praw.Reddit, "update_checked", False)

    _fetcher()

    assert not silent_proxy.accepted, "the constructor opened a connection"
