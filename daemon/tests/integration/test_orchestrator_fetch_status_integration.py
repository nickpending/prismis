"""A cycle records what happened to each source's fetch in the source row.

Invariant protected:
  - `run_once` stamps a source whose fetch failed as failed, and one whose fetch
    succeeded as succeeded, so the error status every client shows is true (#75)

Real collaborators throughout: the production orchestrator from `build_orchestrator`,
real Storage over a sealed database, a real RSSFetcher over real sockets. The failing
source points at a port nothing listens on; the succeeding one at the local stub feed.
"""

import os
from pathlib import Path

from rich.console import Console

from prismis_daemon.config import Config
from prismis_daemon.verify_chain import build_orchestrator, setup_isolated_run


def _cycle(url: str, tmp_path: Path) -> dict:
    """Run one real cycle over a single RSS source and return its stored row."""
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")
    storage, source = setup_isolated_run(url, "rss")
    orchestrator = build_orchestrator(
        Config.from_file(), storage, Console(quiet=True)
    )

    orchestrator.run_once()

    (row,) = [s for s in storage.get_all_sources() if s["id"] == source["id"]]
    return row


def test_a_failed_fetch_is_recorded_as_a_failure(tmp_path: Path) -> None:
    """
    INVARIANT: A source whose fetch raises gets error_count incremented and last_error set
    BREAKS: The failure is stamped as a success every cycle, so a dead source reads as
            healthy everywhere and the five-strike deactivation can never fire
    """
    row = _cycle("http://127.0.0.1:1/feed.xml", tmp_path)

    assert row["error_count"] == 1, f"a failed fetch must count, got {row}"
    assert row["last_error"], "the failure's cause must be stored"
    assert row["last_fetched_at"] is None, "a failed fetch is not a fetch"


def test_a_successful_fetch_is_recorded_as_a_success(
    local_pipeline_stub: str, tmp_path: Path
) -> None:
    """
    INVARIANT: A source whose fetch returns stamps last_fetched_at and no error
    BREAKS: The fix for #75 over-reaches and marks healthy sources failed
    """
    row = _cycle(f"{local_pipeline_stub}/feed.xml", tmp_path)

    assert row["error_count"] == 0, f"a clean fetch must not count, got {row}"
    assert row["last_error"] is None
    assert row["last_fetched_at"] is not None, "a clean fetch must be stamped"
