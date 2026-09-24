"""An open deep-service circuit leaves a record and is reported as such (#72).

Invariant protected:
  - when the deep circuit is open, each refused item emits an llm.call event with
    status "circuit_open", and the chain reports deep_extract as circuit-open while
    summarize still ran (the stats half is asserted in test_orchestrator_deep_unit)

The deep breaker is opened by feeding the real CircuitBreaker three quota errors, the
same path a provider's insufficient_quota takes. Everything else is the local chain:
real orchestrator, real fetcher over HTTP, real Storage, the stub LLM endpoint.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.verify_chain import execute_chain

from conftest import LOCAL_DEEP_SERVICE, configure_local_services


@pytest.fixture(autouse=True)
def clean_circuits() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def test_an_open_deep_circuit_is_recorded_and_reported(
    local_pipeline_stub: str, isolated_xdg_env: Path, tmp_path: Path
) -> None:
    """
    INVARIANT: A deep extraction refused by an open circuit is distinguishable from
               one never attempted, in the events, the stats and the report
    BREAKS: A quota-exhausted deep service looks exactly like deep extraction being
            switched off, and the operator never learns the quota ran out
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")
    breaker = get_circuit_breaker(LOCAL_DEEP_SERVICE)
    for _ in range(breaker.failure_threshold):
        breaker.record_failure(RuntimeError("insufficient_quota"))

    exit_code, links = execute_chain(
        f"{local_pipeline_stub}/feed.xml", "rss", full=True, console=Console(quiet=True)
    )

    by_name = {link.name: link for link in links}
    assert by_name["summarize"].status == "ran", by_name["summarize"].detail
    assert by_name["deep_extract"].status == "circuit-open", (
        f"deep_extract reported {by_name['deep_extract'].status!r}: "
        f"{by_name['deep_extract'].detail}"
    )
    assert exit_code == 1, "a circuit-open link fails the run"
