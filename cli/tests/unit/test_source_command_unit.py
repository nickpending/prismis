"""Unit tests for `prismis-cli source add` URL -> source-type detection, and for the
naughty flag through `source add`, `source list` and `source update`.

INVARIANT: The CLI decides only the source type and leaves the URL as typed; the
daemon's normalize_source_url is the one place protocol URLs are expanded.
BREAKS: A misdetected type is fetched by the wrong fetcher. A second expansion in the
CLI is how the same source came to be stored under two URLs depending on which client
added it (#65).

The flag tests drive the real `source` commands against a real daemon (`live_daemon`,
conftest.py). Their sources are file sources, whose validation is format-only.
"""

import json
from typing import Any

import pytest
from typer.testing import CliRunner

from cli.source import app as source_app
from cli.source import detect_source_type

from conftest import LiveDaemon


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("reddit://rust", "reddit"),
        ("reddit://rust/", "reddit"),
        ("https://reddit.com/r/rust", "reddit"),
        ("youtube://@mkbhd", "youtube"),
        ("youtube://UC9-y-6csu5WGm29I7JiwpnA", "youtube"),
        ("youtube://PLabc123", "youtube"),
        ("https://youtube.com/@mkbhd", "youtube"),
        ("https://youtu.be/abc", "youtube"),
        ("https://example.com/CHANGELOG.md", "file"),
        ("https://simonwillison.net/atom/everything/", "rss"),
    ],
)
def test_source_type_detected_from_url(url: str, expected: str) -> None:
    assert detect_source_type(url) == expected


_runner = CliRunner()


def _invoke(*args: str) -> str:
    # Wide enough that Rich renders every table cell whole; at its 80-column default
    # it truncates them.
    result = _runner.invoke(source_app, list(args), env={"COLUMNS": "200"})
    assert result.exit_code == 0, result.output
    return result.stdout


def _json(output: str) -> dict[str, Any] | list[dict[str, Any]]:
    """The JSON document a --json command printed.

    The daemon runs in this process, so its request log and config warnings land in
    the same captured stdout, before or after the document; decoding starts at the
    first line that opens one and stops where it closes.
    """
    lines = output.splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith(("{", "[")))
    document, _end = json.JSONDecoder().raw_decode("".join(lines[start:]))
    assert isinstance(document, dict | list), document
    return document


def _listed(source_id: str) -> dict[str, Any]:
    sources = _json(_invoke("list", "--json"))
    assert isinstance(sources, list), sources
    (source,) = [s for s in sources if s["id"] == source_id]
    return source


def _ua_cell(source_id: str) -> str:
    """The UA cell of `source_id`'s row in the `source list` table."""
    (row,) = [line for line in _invoke("list").splitlines() if source_id in line]
    return row.split("│")[-2].strip()


def test_the_naughty_flag_through_add_list_and_update(live_daemon: LiveDaemon) -> None:
    """
    INVARIANT: `source add --naughty` stores the flag; `source list` shows it as a UA
               column reading browser, and prismis once `source update --no-naughty`
               clears it; `--json` carries it; `source update --name` changes exactly
               the name and `--naughty` exactly the flag
    BREAKS: the flag can be set only through the API, or a rename from the CLI drops
            a source's browser user-agent
    """
    added = _json(
        _invoke("add", "--naughty", "--json", "https://example.com/CHANGELOG.md")
    )
    assert isinstance(added, dict), added
    source_id = added["id"]

    assert _listed(source_id)["naughty"] is True
    assert _ua_cell(source_id) == "browser"

    _invoke("update", source_id, "--no-naughty")
    assert _ua_cell(source_id) == "prismis"
    unmarked = _listed(source_id)
    assert unmarked["naughty"] is False

    _invoke("update", source_id, "--name", "Renamed")
    renamed = _listed(source_id)
    assert renamed == {**unmarked, "name": "Renamed"}

    _invoke("update", source_id, "--naughty")
    assert _listed(source_id) == {**renamed, "naughty": True}


def test_source_edit_renames_and_keeps_the_flag(live_daemon: LiveDaemon) -> None:
    """
    INVARIANT: `source edit <id> <name>` changes exactly the name; a marked source
               stays marked
    BREAKS: the older rename command clears a source's browser user-agent
    """
    added = _json(
        _invoke("add", "--naughty", "--json", "https://example.com/CHANGELOG.md")
    )
    assert isinstance(added, dict), added
    before = _listed(added["id"])

    _invoke("edit", added["id"], "Edited")

    assert _listed(added["id"]) == {**before, "name": "Edited"}


def test_source_update_with_nothing_to_change_is_refused(
    live_daemon: LiveDaemon,
) -> None:
    """
    INVARIANT: `source update <id>` with neither --name nor a flag option exits 1
               without a request
    BREAKS: a mistyped update reports success while changing nothing
    """
    result = _runner.invoke(source_app, ["update", "any-id"])

    assert result.exit_code == 1
    assert "Nothing to update" in result.output
