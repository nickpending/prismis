"""Unit tests for the kind classifier's opt-in gate -- content-kind work order, job 2
(SC-4).

SC-4: given a config whose [llm] section sets no kind_service, items are analysed with
no request made to the decisions endpoint and no kind stored -- installs without a Jev
key behave exactly as before this work order.

Two levels, matching test_orchestrator_deep_unit.py's split for the analogous deep
extraction gate:
  - Config.from_file() maps an absent `kind_service` key to `llm_kind_service = None`,
    and a present one through to the field (config.py wiring).
  - DaemonOrchestrator, run with no KindClassifier at all (the shape __main__.py's own
    `if config.llm_kind_service: KindClassifier(...)` gate produces when the key is
    unset), stores the item with no "kind" key in its analysis and never reaches
    submit_decision -- proven by making the provider boundary itself explode if it is
    ever called, not by reading source.

Real collaborators throughout: the real Summarizer and Evaluator drive the real
`complete()` against a local HTTP stub standing in for the LLM (the one
collaborator the constitution permits faking), real Storage over the sealed test
database, and a real DaemonOrchestrator. Only submit_decision -- kind_classifier's own
provider-boundary call -- is patched, and only to prove it is never reached.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.notifier import Notifier
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import TEST_API_KEY, configure_local_services

# The decisions-endpoint provider boundary itself (Principle I permits faking it);
# patched here only to prove it is never called, never to supply a canned answer.
_PATCH_SUBMIT = "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock


class _NullFetcher:
    """A fetcher whose source is never selected by this test's source dict."""

    def fetch_content(self, source, **kw):
        return []


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


# ---------------------------------------------------------------------------
# Config wiring: kind_service is absent by default, present when set
# ---------------------------------------------------------------------------


def test_config_kind_service_defaults_to_none(isolated_xdg_env: Path) -> None:
    """
    SC-4: the production template ships no `kind_service` key, so a fresh install's
    Config.from_file() carries llm_kind_service = None.
    BREAKS: A stray default service name means every install starts billing Jev
    without the operator ever opting in.
    """
    config = Config.from_file()
    assert config.llm_kind_service is None


def test_config_kind_service_loads_when_present(isolated_xdg_env: Path) -> None:
    """
    SC-4's inverse: when [llm] does set kind_service, Config.from_file() carries it
    through to llm_kind_service, the same way deep_service maps to llm_deep_service.
    BREAKS: The [llm] section gains kind_service but config.py never reads it, so an
    operator who sets it in config.toml gets silently ignored.
    """
    config_path = isolated_xdg_env / "config.toml"
    text = DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    text = text.replace(
        'light_service = "openrouter"\n',
        'light_service = "openrouter"\n'
        'kind_service = "prismis-openrouter-kind"\n',
    )
    config_path.write_text(text)

    config = Config.from_file()
    assert config.llm_kind_service == "prismis-openrouter-kind"


# ---------------------------------------------------------------------------
# Orchestrator: no classifier configured -> no call, no kind stored
# ---------------------------------------------------------------------------


def test_no_kind_classifier_makes_no_call_and_stores_no_kind(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> None:
    """
    SC-4: with the orchestrator built the way __main__.py builds it when
    config.llm_kind_service is unset (kind_classifier=None), an analysed item is
    stored with no "kind" key in its analysis, and the decisions endpoint is never
    touched -- proven by making submit_decision raise if it is ever called.

    BREAKS: The orchestrator calls a classifier attribute unconditionally (AttributeError
    on None), or a default KindClassifier gets constructed somewhere and reaches the
    provider boundary even though no kind_service was configured.
    """
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    configure_local_services(cfg_home, local_pipeline_stub)
    config = Config.from_file()
    assert config.llm_kind_service is None, (
        "setup: configure_local_services must not set a kind service"
    )

    storage = Storage(test_db)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Test Feed")

    pipeline_item = ContentItem(
        source_id=source_id,
        external_id="not-configured-001",
        title="An Ordinary Article",
        url="https://example.com/not-configured",
        content="Some article content, unremarkable.",
        analysis={},
    )

    class _StubRSSFetcher:
        def fetch_content(self, source, **kw):
            return [pipeline_item]

    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_StubRSSFetcher(),
        reddit_fetcher=_NullFetcher(),
        youtube_fetcher=_NullFetcher(),
        file_fetcher=_NullFetcher(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        notifier=Notifier(),
        config=config,
        # No kind_classifier passed at all -- the default, and what __main__.py's own
        # `if config.llm_kind_service:` gate produces when the key is unset.
    )

    source_dict = {
        "id": source_id,
        "url": "https://feeds.example.com/rss",
        "type": "rss",
        "name": "Test Feed",
        "active": True,
    }

    with patch(_PATCH_SUBMIT, side_effect=AssertionError("must never be called")):
        stats = orchestrator.fetch_source_content(source_dict)

    assert stats["errors"] == [], stats["errors"]
    assert stats["kind_classify_failures"] == [], stats["kind_classify_failures"]

    row = storage.conn.execute(
        "SELECT id FROM content WHERE external_id = ?", ("not-configured-001",)
    ).fetchone()
    assert row is not None, "create_or_update_content must have stored the item"

    stored = storage.get_content_by_id(row["id"])
    assert stored is not None
    stored_analysis = stored.get("analysis") or {}
    assert "kind" not in stored_analysis, (
        "SC-4: no kind_service configured must leave no kind key in analysis"
    )
    assert "kind_confidence" not in stored_analysis
