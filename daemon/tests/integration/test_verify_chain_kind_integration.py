"""verify --chain's kind link -- content-kind work order, job 4 (SC-9).

SC-9: given a real source and the classifier configured, when `prismis-daemon verify
--chain` runs, the report has a kind link with its status and cost, mined from the
observability events like the other LLM-backed links, and reports it as not
configured when [llm] kind_service is unset.

Real collaborators throughout: the real RSSFetcher over HTTP against
`local_pipeline_stub`, the real Summarizer/Evaluator driving `complete()`
against that same stub, real Storage, real Embedder, the real DaemonOrchestrator built
by the chain's own `build_orchestrator`. Only `submit_decision` -- kind_classifier's
own provider boundary, since the decisions endpoint isn't chat-completions shaped and
never goes through llm_client.complete() -- is stood in for, per Principle I.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest
from rich.console import Console

from prismis_daemon.verify_chain import execute_chain

from conftest import configure_local_services

# The decisions-endpoint provider boundary itself (Principle I's one permitted fake).
_PATCH_SUBMIT = (
    "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock
)

_KIND_SERVICE = "prismis-verify-stub-kind"


class _FakeDecisionCall:
    """Minimal stand-in for kind_classifier.DecisionCall."""

    def __init__(self, answers: dict, cost: float = 0.00007) -> None:
        self.answers = answers
        self.model = "typesafe/jev-1.13-test"
        self.cost = cost
        self.duration_ms = 42


def _kind_answer(choice: str, confidence: float) -> dict:
    return {"kind": {"type": "choice", "choice": choice, "confidence": confidence}}


def _configure_kind_service(cfg_home: Path, service_name: str) -> None:
    """Add `kind_service = "..."` to the sealed config's [llm] section.

    submit_decision -- kind_classifier's own provider boundary -- is patched in every
    test below that needs a configured service, so this name need never resolve in
    config.toml's services tables; it only has to be non-empty for Config.llm_kind_service to be
    truthy and for build_orchestrator to wire a KindClassifier.
    """
    path = cfg_home / "prismis" / "config.toml"
    text = path.read_text()
    marker = "auto_extract = "
    assert marker in text, "the sealed config template must still hold this key"
    path.write_text(
        text.replace(marker, f'kind_service = "{service_name}"\n{marker}', 1)
    )


def test_kind_link_reports_not_configured_when_kind_service_is_unset(
    local_pipeline_stub: str, isolated_xdg_env: Path, tmp_path: Path
) -> None:
    """
    SC-9: with no [llm] kind_service set, the chain makes no classify_kind call at
    all, and the report says the link is not configured -- not empty, not an error.
    BREAKS: an unconfigured kind service reads as an error or an empty result, or the
    kind link is silently absent from the report.
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")

    exit_code, links = execute_chain(
        f"{local_pipeline_stub}/feed.xml",
        "rss",
        full=False,
        console=Console(quiet=True),
    )

    by_name = {link.name: link for link in links}
    assert "kind" in by_name, (
        f"the report must carry a kind link, got {sorted(by_name)}"
    )
    assert by_name["kind"].status == "skipped", by_name["kind"].detail
    assert "not configured" in by_name["kind"].detail
    assert by_name["kind"].cost_usd is None
    assert exit_code == 0, "an unconfigured optional link must not fail the run"


def test_kind_link_reports_status_and_cost_from_classify_kind_events(
    local_pipeline_stub: str, isolated_xdg_env: Path, tmp_path: Path
) -> None:
    """
    SC-9: with a kind service configured, the kind link's status, cost and model are
    mined from the classify_kind llm.call events the real classifier logs, exactly
    like the other LLM-backed links.
    BREAKS: the kind link stays blank despite a configured classifier, or reports a
    cost that is not the sum of what the classifier's own events carried.
    """
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, local_pipeline_stub)
    _configure_kind_service(cfg_home, _KIND_SERVICE)
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")

    fake = _FakeDecisionCall(_kind_answer("release", 0.92), cost=0.00007)
    with patch(_PATCH_SUBMIT, return_value=fake):
        exit_code, links = execute_chain(
            f"{local_pipeline_stub}/feed.xml",
            "rss",
            full=False,
            console=Console(quiet=True),
        )

    by_name = {link.name: link for link in links}
    # The stub feed carries two items (conftest._STUB_FEED); each is classified once.
    assert by_name["kind"].status == "ran", by_name["kind"].detail
    assert by_name["kind"].cost_usd == pytest.approx(0.00014)
    assert by_name["kind"].model == fake.model
    assert exit_code == 0


def test_kind_link_call_error_is_reported_as_error(
    local_pipeline_stub: str, isolated_xdg_env: Path, tmp_path: Path
) -> None:
    """
    SC-9, with INV-002 alongside it: when submit_decision itself fails, the failure
    never blocks the run -- summarize still ran and items were still stored -- but
    the kind link reports the failure rather than reading as "produced nothing".
    BREAKS: a classifier outage is invisible in the chain's own report.
    """
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, local_pipeline_stub)
    _configure_kind_service(cfg_home, _KIND_SERVICE)
    os.environ["XDG_DATA_HOME"] = str(tmp_path / "data")

    with patch(_PATCH_SUBMIT, side_effect=RuntimeError("connection refused")):
        exit_code, links = execute_chain(
            f"{local_pipeline_stub}/feed.xml",
            "rss",
            full=False,
            console=Console(quiet=True),
        )

    by_name = {link.name: link for link in links}
    assert by_name["kind"].status == "error", by_name["kind"].detail
    assert "connection refused" in by_name["kind"].detail
    assert by_name["summarize"].status == "ran", "the light pass must still have run"
    assert by_name["store"].status == "ran", "items must still be stored (INV-002)"
    assert exit_code == 1, "an errored link must fail the run"
