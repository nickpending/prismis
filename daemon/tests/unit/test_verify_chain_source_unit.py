"""Unit tests for the chain's isolated-run setup and synthetic source construction.

Invariants protected:
  - The schema is created before Storage is constructed; the reverse order fails,
    because Storage's constructor opens a connection immediately despite its lazy
    comment
  - Everything the chain creates lands under the temp XDG_DATA_HOME, never the
    operator's real data directory
  - The source dict handed to fetch_source_content carries the keys it reads, and the
    row it names really exists in the sources table
  - An unsupported --type is rejected before anything is written

Success criteria covered:
  SC-1b (schema before collaborators), SC-2 (the live database is never opened)

Real collaborators: real init_db, real Storage, real SQLite.
"""

import os
from pathlib import Path

import pytest

from prismis_daemon.storage import Storage
from prismis_daemon.verify_chain import (
    VALID_SOURCE_TYPES,
    build_source,
    setup_isolated_run,
)

_URL = "https://example.com/feed.xml"


@pytest.fixture
def empty_data_home(tmp_path: Path, monkeypatch) -> Path:
    """A genuinely empty XDG_DATA_HOME — no prismis directory, no database file."""
    data_home = tmp_path / "empty-data"
    data_home.mkdir()
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    return data_home


def test_storage_alone_fails_against_an_empty_data_home(empty_data_home: Path) -> None:
    """
    SC-1b: establishes that the ordering setup_isolated_run enforces is load-bearing.

    Without this, the sibling test below would pass whether or not init_db ran first,
    because there would be no evidence that constructing Storage against a fresh
    directory fails at all.

    BREAKS: the ordering guarantee is untested and the chain's first real run dies in a
    stack trace instead.
    """
    with pytest.raises(FileNotFoundError):
        Storage()

    assert not (empty_data_home / "prismis" / "prismis.db").exists()


def test_setup_isolated_run_initializes_schema_before_storage(
    empty_data_home: Path,
) -> None:
    """
    SC-1b / SC-2: against a genuinely empty temp data home the chain sets itself up,
    and everything it creates lands under that temp directory.

    BREAKS: the chain cannot start at all, or it writes into the operator's real data
    directory — the one thing this work order forbids outright.
    """
    storage, source = setup_isolated_run(_URL, "rss")

    db_path = empty_data_home / "prismis" / "prismis.db"
    assert db_path.exists()
    assert Path(storage.conn.execute("PRAGMA database_list").fetchone()[2]) == db_path

    real_data_home = Path(os.environ["HOME"]) / ".local" / "share" / "prismis"
    assert not real_data_home.exists()

    assert source["id"]
    assert source["url"] == _URL
    assert source["type"] == "rss"
    assert source["name"]


def test_build_source_row_really_exists_in_the_sources_table(test_db) -> None:
    """
    INVARIANT: the source dict names a row that exists — the content table's foreign
    key on source_id rejects anything else, so a dict-only source would fail at store
    time rather than at setup time.

    BREAKS: every run dies at link 6 with a foreign-key error.
    """
    storage = Storage()

    source = build_source(storage, _URL, "reddit")

    row = storage.conn.execute(
        "SELECT url, type, name FROM sources WHERE id = ?", (source["id"],)
    ).fetchone()
    assert row is not None
    assert row["url"] == _URL
    assert row["type"] == "reddit"
    assert row["name"] == source["name"]


def test_invalid_source_type_is_rejected(empty_data_home: Path) -> None:
    """
    INVARIANT: an unsupported --type is refused, and refused before any database work.
    BREAKS: a typo'd type silently falls through to the RSS fetcher.
    """
    assert "gopher" not in VALID_SOURCE_TYPES

    with pytest.raises(ValueError, match="Invalid source type"):
        setup_isolated_run(_URL, "gopher")

    assert not (empty_data_home / "prismis" / "prismis.db").exists()


def test_default_run_cannot_deep_extract_whatever_the_config_says(tmp_path) -> None:
    """
    INVARIANT: Without --full the deep extractor is not constructed, so the deep
               service cannot be billed no matter what auto_extract is set to
    BREAKS: The first real run on the deploy host reported deep_extract as
            "skipped-by-flag" while the deep service was actually being billed. The
            orchestrator decides to deep-extract from its own auto_extract config
            ("high" on that host), so a constructed extractor runs on a HIGH item
            regardless of what this command intended, and the report asserted the
            opposite of what happened.
    NOTE: This asserts the collaborator is absent rather than observing an absent LLM
          call, because observing the call needs credentials this machine does not
          have. It is not a proxy: orchestrator.py gates the whole deep-extract branch
          on `if self.deep_extractor and ...`, so None makes the branch unreachable
          rather than merely unlikely.
    """
    from conftest import init_db, make_config
    from prismis_daemon.storage import Storage
    from prismis_daemon.verify_chain import build_orchestrator
    from rich.console import Console

    db = tmp_path / "prismis.db"
    init_db(db)
    storage = Storage(db_path=db)
    config = make_config(
        llm_deep_service="a-deep-service-that-would-cost-money",
        auto_extract="high",
    )

    default_run = build_orchestrator(config, storage, Console(quiet=True))
    assert default_run.deep_extractor is None, (
        "a default run built a deep extractor; the deep service can be billed while "
        "the report claims the link was skipped by flag"
    )

    full_run = build_orchestrator(config, storage, Console(quiet=True), full=True)
    assert full_run.deep_extractor is not None, (
        "--full must build the extractor, or the flag does nothing"
    )
