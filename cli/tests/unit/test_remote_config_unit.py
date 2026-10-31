"""A config.toml that exists but cannot be read is an error, not "use localhost" (gh #58).

Invariant protected:
  - a missing config.toml, or one with no [remote] section, resolves the local daemon
  - a config.toml that is malformed, unreadable or has a [remote] that is not a table stops
    the command with an error naming config.toml and the cause
  - an [agent] table changes nothing [remote] resolves to, and config.toml is opened in one
    place, read_config_toml, for both the [remote] and the [agent] read

A silent fallback would send the command to the local daemon with no API key, and the
operator would be told the key was wrong, or the daemon was not running, instead of that
their config file is broken.
"""

import ast
from pathlib import Path

import pytest

from cli.agent import load_agent_config
from cli.api_client import APIClient
from cli.remote import (
    get_remote_key,
    get_remote_url,
    is_remote_mode,
    set_remote_url,
)
from conftest import TEST_API_KEY
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML

LOCALHOST = "http://localhost:8989"
CLI_SRC = Path(__file__).resolve().parents[2] / "src" / "cli"
AGENT_TABLE = (
    '\n[agent]\ntarget = "herdr"\ncommand = "ember claude"\nkind = "pi"\n'
    'prompt = "Summarise it."\n'
)


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
    assert str(raised.value.__cause__) in str(raised.value), (
        "the cause is in the message"
    )


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


def _full_config() -> str:
    """The production template with a real [remote] url and key."""
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    assert '# url = "' in text and '# key = "' in text, "template layout moved"
    lines = []
    for line in text.splitlines():
        if line.startswith('# url = "'):
            line = 'url = "http://cerebro:8989"'
        elif line.startswith('# key = "'):
            line = 'key = "remote-key"'
        lines.append(line)
    return "\n".join(lines) + "\n"


def test_an_agent_table_leaves_the_remote_resolution_unchanged(
    isolated_xdg_env: Path,
) -> None:
    """
    INVARIANT: the same config with and without an [agent] table of all four keys
               resolves the same [remote] url and key
    BREAKS: adding [agent] for `prismis-cli open` sends every other command elsewhere
    """
    config_path = isolated_xdg_env / "config.toml"
    config_path.write_text(_full_config())
    without = (get_remote_url(), get_remote_key())

    config_path.write_text(_full_config() + AGENT_TABLE)
    with_agent = (get_remote_url(), get_remote_key())

    assert without == ("http://cerebro:8989", "remote-key")
    assert with_agent == without
    assert load_agent_config().command == ["ember", "claude"]


def test_the_agent_read_fails_on_a_malformed_config_like_the_remote_read(
    isolated_xdg_env: Path,
) -> None:
    """
    INVARIANT: the [agent] read raises the same config.toml error, with the parse error
               as its cause, that the [remote] read does
    BREAKS: a broken config.toml reads as "no agent configured"
    """
    (isolated_xdg_env / "config.toml").write_text("[agent\ntarget = ")

    with pytest.raises(RuntimeError, match=r"config\.toml") as raised:
        load_agent_config()

    assert type(raised.value.__cause__).__name__ == "TOMLDecodeError"


def test_config_toml_is_opened_only_in_read_config_toml() -> None:
    """
    INVARIANT: in remote.py and agent.py the one call that opens a file is inside
               read_config_toml
    BREAKS: a second read of config.toml drifts from the first in how it treats a
            missing, malformed or unreadable file
    """
    opens: list[tuple[str, str]] = []
    for name in ("remote.py", "agent.py"):
        tree = ast.parse((CLI_SRC / name).read_text())
        for func in ast.walk(tree):
            if not isinstance(func, ast.FunctionDef):
                continue
            for node in ast.walk(func):
                if not isinstance(node, ast.Call):
                    continue
                callee = node.func
                called = (
                    callee.id
                    if isinstance(callee, ast.Name)
                    else callee.attr
                    if isinstance(callee, ast.Attribute)
                    else ""
                )
                if called in {"open", "load", "read_text", "read_bytes"}:
                    opens.append((name, func.name))

    assert opens == [
        ("remote.py", "read_config_toml"),
        ("remote.py", "read_config_toml"),
    ]
