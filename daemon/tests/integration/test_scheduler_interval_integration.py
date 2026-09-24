"""The fetch cycle runs at the configured fetch_interval (#71).

Invariant protected:
  - the scheduler's fetch job fires every `config.fetch_interval` minutes

This asserts the trigger the scheduler was built with, not elapsed time: waiting out a
real interval is impractical, and the claim here is only that the configured value
reaches the trigger, which was hardcoded to 30 minutes from the first commit.
Real collaborators: the production orchestrator and a real Storage.
"""

import dataclasses
import os
from datetime import timedelta
from pathlib import Path

import pytest
from rich.console import Console

from prismis_daemon.__main__ import build_scheduler
from prismis_daemon.config import Config
from prismis_daemon.verify_chain import build_orchestrator, setup_isolated_run


@pytest.mark.parametrize("minutes", [45, 120])  # neither is the old hardcoded 30
def test_fetch_job_runs_at_the_configured_interval(
    minutes: int, tmp_path: Path
) -> None:
    """
    INVARIANT: The fetch job's interval is config.fetch_interval minutes
    BREAKS: The setting is parsed and validated and then ignored, so an operator who
            sets 120 gets 30 with no error and no warning
    """
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")
    storage, _source = setup_isolated_run("http://127.0.0.1:1/feed.xml", "rss")
    config = dataclasses.replace(Config.from_file(), fetch_interval=minutes)
    orchestrator = build_orchestrator(config, storage, Console(quiet=True))

    scheduler, interval_msg = build_scheduler(config, orchestrator, storage)

    job = scheduler.get_job("fetch_and_analyze")
    assert job is not None, "the fetch job must be registered"
    assert job.trigger.interval == timedelta(minutes=minutes)
    assert interval_msg == f"{minutes} minutes"
