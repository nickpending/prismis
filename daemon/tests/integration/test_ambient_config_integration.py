"""No config file outside prismis's own changes where Reddit or YouTube requests go (#69).

Invariant protected:
  - a praw.ini in the working directory or $XDG_CONFIG_HOME cannot redirect the Reddit
    client, and a yt-dlp.conf in the working directory cannot redirect yt-dlp

Each rogue file points its client at a local listener that counts connections and
closes them. Every other destination goes to a proxy port nothing listens on, so a
client that ignores the rogue file fails fast without leaving the machine, and only a
client that obeyed it can reach the listener.
"""

import socket
import threading
from collections.abc import Iterator
from pathlib import Path

import praw.config
import pytest

from prismis_daemon.fetchers.reddit import RedditFetcher
from prismis_daemon.fetchers.youtube import YouTubeFetcher
from prismis_daemon.validator import SourceValidator

from conftest import make_config


class _Trap:
    """Accepts connections, counts them, and closes them at once."""

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self.url = "http://127.0.0.1:{}".format(self._sock.getsockname()[1])
        self.hits = 0
        threading.Thread(target=self._accept, daemon=True).start()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                return
            self.hits += 1
            conn.close()

    def close(self) -> None:
        self._sock.close()


@pytest.fixture
def trap(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[_Trap]:
    """A trap on loopback, a dead proxy for everything else, and a clean working dir."""
    t = _Trap()
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1")
    monkeypatch.chdir(tmp_path)
    yield t
    t.close()


@pytest.mark.parametrize("location", ["cwd", "xdg"])
def test_a_rogue_praw_ini_cannot_redirect_the_reddit_client(
    location: str, trap: _Trap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: The Reddit client talks to reddit.com whatever praw.ini is lying around
    BREAKS: A praw.ini in the daemon's working directory or ~/.config sets reddit_url,
            and the client secret goes to whatever host it names
    """
    config = make_config(
        reddit_client_id="prismis-not-a-real-client-id",
        reddit_client_secret="prismis-not-a-real-client-secret",
    )
    xdg = tmp_path / "xdg"
    xdg.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    target = tmp_path if location == "cwd" else xdg
    (target / "praw.ini").write_text(
        f"[DEFAULT]\nreddit_url={trap.url}\noauth_url={trap.url}\n"
    )
    # A fresh process has not loaded PRAW's config yet; neither has this one, now.
    monkeypatch.setattr(praw.config.Config, "CONFIG", None)

    fetcher = RedditFetcher(config=config, fetch_budget=2.0)
    with pytest.raises(Exception):  # noqa: B017 — any failure; the trap is the claim
        fetcher.fetch_content({"url": "https://www.reddit.com/r/python", "id": "s"})

    assert trap.hits == 0, f"the {location} praw.ini redirected the client"
    assert fetcher.reddit is not None
    assert fetcher.reddit.config.oauth_url == "https://oauth.reddit.com"


def test_a_rogue_praw_ini_cannot_redirect_the_source_validator(
    trap: _Trap, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    INVARIANT: Validating a reddit source never sends the secret where praw.ini says
    BREAKS: The add-source path builds its own PRAW client and inherits the file
    """
    validator = SourceValidator(
        make_config(
            reddit_client_id="prismis-not-a-real-client-id",
            reddit_client_secret="prismis-not-a-real-client-secret",
        )
    )
    validator.timeout = 2.0
    (tmp_path / "praw.ini").write_text(
        f"[DEFAULT]\nreddit_url={trap.url}\noauth_url={trap.url}\n"
    )
    monkeypatch.setattr(praw.config.Config, "CONFIG", None)

    is_valid, _error, _metadata = validator.validate_source("reddit://python", "reddit")

    assert is_valid is False
    assert trap.hits == 0, "the working-directory praw.ini redirected the validator"


def test_a_rogue_yt_dlp_conf_cannot_redirect_yt_dlp(
    trap: _Trap, tmp_path: Path
) -> None:
    """
    INVARIANT: yt-dlp ignores a yt-dlp.conf in the working directory
    BREAKS: A config file prismis never documents changes proxies, formats or output
            for every YouTube fetch
    """
    (tmp_path / "yt-dlp.conf").write_text(f"--proxy {trap.url}\n")
    fetcher = YouTubeFetcher(config=make_config())

    # Discovery failures come back as an empty list; the trap is the claim.
    fetcher.fetch_content(
        {"url": "https://www.youtube.com/@prismis-no-such-channel", "id": "s"}
    )

    assert trap.hits == 0, "yt-dlp read the working-directory yt-dlp.conf"
