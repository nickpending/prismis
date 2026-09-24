"""Pin PRAW's settings to the defaults it ships with."""

import configparser
from pathlib import Path

import praw.config


def pin_praw_defaults() -> None:
    """Make PRAW read only its bundled praw.ini, never one from the machine.

    PRAW merges its bundled praw.ini with $XDG_CONFIG_HOME/praw.ini and a praw.ini in
    the process working directory, and any of those can set oauth_url or reddit_url,
    which is where the client secret is sent (#69). prismis's Reddit settings live in
    its own config.toml [reddit] section and are passed explicitly; the bundled file
    supplies the required rest (oauth_url, reddit_url, timeout, ratelimit_seconds).
    """
    bundled = configparser.ConfigParser(interpolation=None)
    bundled.read(Path(praw.config.__file__).parent / "praw.ini")
    with praw.config.Config.LOCK:
        praw.config.Config.CONFIG = bundled
