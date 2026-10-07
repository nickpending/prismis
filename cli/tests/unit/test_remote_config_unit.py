"""A config.toml that exists but cannot be read is an error, not "use localhost" (gh #58).

Invariant protected:
  - a missing config.toml, or one with no [remote] section, resolves the local daemon
  - a config.toml that is malformed, unreadable or has a [remote] that is not a table stops
    the command with an error naming config.toml and the cause

A silent fallback would send the command to the local daemon with no API key, and the
operator would be told the key was wrong, or the daemon was not running, instead of that
their config file is broken.
"""

from pathlib import Path

import pytest

from cli.api_client import APIClient
from cli.remote import (
    get_remote_key,
    get_remote_url,
    is_remote_mode,
    set_remote_url,
)

LOCALHOST = "http://localhost:8989"


@pytest.fixture(autouse=True)
def _no_remote_flag() -> None:
    set_remote_url(None)


def test_a_missing_config_resolves_the_local_daemon(isolated_xdg_env: Path) -> None:
    """
    INVARIANT: no config.toml means the local daemon, with no remote key
    BREAKS: a fresh install, which has no config yet, cannot resolve a daemon at all
    """
    assert not (isolated_xdg_env / "config.toml").exists()

    assert get_remote_url() == LOCALHOST
    assert get_remote_key() is None
    assert is_remote_mode() is False


def test_a_config_without_a_remote_section_resolves_the_local_daemon(
    isolated_xdg_env: Path,
) -> None:
    """
    INVARIANT: a readable config with no [remote] means the local daemon
    BREAKS: the fix over-reaches and every local-only config is rejected
    """
    (isolated_xdg_env / "config.toml").write_text('[api]\nkey = "k"\n')

    assert get_remote_url() == LOCALHOST
    assert is_remote_mode() is False


def test_a_config_with_a_remote_section_resolves_the_remote(
    isolated_xdg_env: Path,
) -> None:
    """
    INVARIANT: [remote] url and key are what a readable config resolves to
    BREAKS: the narrowed read loses the remote it exists to return
    """
    (isolated_xdg_env / "config.toml").write_text(
        '[remote]\nurl = "http://cerebro:8989"\nkey = "remote-key"\n'
    )

    assert get_remote_url() == "http://cerebro:8989"
    assert get_remote_key() == "remote-key"
    assert is_remote_mode() is True


def test_a_malformed_config_stops_the_command_naming_the_file_and_cause(
    isolated_xdg_env: Path,
) -> None:
    """
    INVARIANT: a malformed config.toml fails URL resolution, naming config.toml and the cause
    BREAKS: _load_remote_config returns (None, None), so the command quietly talks to
            http://localhost:8989 with no key and reports a key or daemon problem
    """
    (isolated_xdg_env / "config.toml").write_text("[remote\nurl = ")

    with pytest.raises(RuntimeError, match=r"config\.toml") as raised:
        get_remote_url()

    assert type(raised.value.__cause__).__name__ == "TOMLDecodeError"
    assert str(raised.value.__cause__) in str(raised.value), "the cause is in the message"


def test_a_malformed_config_stops_client_construction(isolated_xdg_env: Path) -> None:
    """
    INVARIANT: building the API client, which every network command does, fails on a
               malformed config.toml instead of defaulting to localhost
    BREAKS: the command runs against the wrong daemon
    """
    (isolated_xdg_env / "config.toml").write_text("[remote\nurl = ")

    with pytest.raises(RuntimeError, match=r"config\.toml"):
        APIClient()


def test_a_config_that_cannot_be_read_stops_the_command(isolated_xdg_env: Path) -> None:
    """
    INVARIANT: a config.toml that exists but cannot be opened fails, naming the file
    BREAKS: a permissions problem reads as "no remote configured"
    """
    config_path = isolated_xdg_env / "config.toml"
    config_path.write_text('[remote]\nurl = "http://cerebro:8989"\n')
    config_path.chmod(0o000)
    try:
        with pytest.raises(RuntimeError, match=r"config\.toml") as raised:
            get_remote_url()
    finally:
        config_path.chmod(0o644)

    assert isinstance(raised.value.__cause__, PermissionError)


def test_a_remote_that_is_not_a_table_stops_the_command(isolated_xdg_env: Path) -> None:
    """
    INVARIANT: `remote = "x"` is a broken config, not "no remote"
    BREAKS: the AttributeError the old blanket handler swallowed turns into localhost
    """
    (isolated_xdg_env / "config.toml").write_text('remote = "http://cerebro:8989"\n')

    with pytest.raises(RuntimeError, match=r"\[remote\] must be a table"):
        get_remote_url()
