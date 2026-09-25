"""Live, credential-gated integration test for llm_client -- SC-3.

Runs one real completion against the operator's actual ~/.config/llm-core/services.toml
and apiconf key store -- the one place in the suite allowed to leave the sealed test
environment and hit a real, billed provider. Skipped unless PRISMIS_LIVE_LLM_TESTS=1;
never runs in CI (gh #60), same convention test_daemon_integration.py and
test_context_api.py already use for their own live paths.

Pins the SC-3 design choice explicitly: prismis-pt-luna (OpenRouter) returns a real,
non-null cost; prismis-openai (api.openai.com) returns cost=None, not a locally computed
estimate -- a static pricing table is exactly the broken mechanism this migration
removes, and reintroducing one for api.openai.com alone would resurrect the same
staleness problem for half the fleet (docs/work/wo-openai-sdk-migration.md, Why).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from prismis_daemon import llm_client, observability
from prismis_daemon.summarizer import ContentSummarizer

# Real, un-sealed HOME -- captured at import time, before conftest's autouse
# isolated_xdg_env fixture monkeypatches HOME/XDG_CONFIG_HOME to a sealed temp dir for
# every other test in the suite.
_REAL_HOME = Path(os.path.expanduser("~"))

pytestmark = pytest.mark.skipif(
    os.environ.get("PRISMIS_LIVE_LLM_TESTS") != "1",
    reason="Requires a live LLM service (services.toml + provider key); "
    "set PRISMIS_LIVE_LLM_TESTS=1 to run. Tracked: gh #60",
)


@pytest.fixture(autouse=True)
def _real_config_env(isolated_xdg_env: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Undo the sealed HOME/XDG_CONFIG_HOME for just this file.

    Depends on isolated_xdg_env so it runs after it (pytest resolves a fixture's own
    dependencies before the fixture itself), then points HOME back at the operator's
    real home and drops the XDG_CONFIG_HOME override so llm_client._config_dir() and
    apiconf's Path.home()-based lookup both land on the real
    ~/.config/llm-core/services.toml and ~/.config/apiconf/config.toml.
    """
    monkeypatch.setenv("HOME", str(_REAL_HOME))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)


def test_openrouter_service_returns_real_cost() -> None:
    """SC-3: prismis-pt-luna (OpenRouter) returns a real, non-null billed cost."""
    result = llm_client.complete(
        prompt="Reply with exactly the word: ok",
        service="prismis-pt-luna",
        max_tokens=5,
    )
    assert result.text
    assert isinstance(result.cost, float), (
        f"expected a real float cost from OpenRouter's usage.include, got {result.cost!r}"
    )
    assert result.cost > 0


def test_openai_service_returns_no_cost() -> None:
    """SC-3 pin: api.openai.com services get cost=None, never a local estimate."""
    result = llm_client.complete(
        prompt="Reply with exactly the word: ok",
        service="prismis-openai",
        max_tokens=5,
    )
    assert result.text
    assert result.cost is None


def test_llm_call_observability_event_records_the_real_cost(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """SC-3: the llm.call observability event records the real OpenRouter cost.

    Routes observability writes at a scratch XDG_DATA_HOME so this test reads only its
    own event, then drives the real ContentSummarizer -> llm_client.complete() ->
    prismis-pt-luna path and reads the JSONL event straight off disk -- no mock of
    prismis_daemon.observability, which is not on the constitution's allowed-fake list.
    """
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    observability.reset_logger()

    ContentSummarizer("prismis-pt-luna").summarize_with_analysis(
        content=(
            "OpenRouter aggregates access to hundreds of language models behind one "
            "API. This short article covers how usage-based billing works and why "
            "per-call cost visibility matters for anyone running it in production."
        ),
        title="Understanding OpenRouter billing",
    )

    today = datetime.now().strftime("%Y-%m-%d")
    log_file = tmp_path / "data" / "prismis" / "observability" / f"{today}_events.jsonl"
    assert log_file.exists(), "expected an observability event file to be written"

    events = [json.loads(line) for line in log_file.read_text().splitlines() if line]
    llm_calls = [
        e
        for e in events
        if e.get("event") == "llm.call"
        and e.get("action") == "summarize"
        and e.get("status") == "success"
    ]
    assert llm_calls, f"expected a successful llm.call summarize event, got: {events}"

    cost_usd = llm_calls[-1].get("cost_usd")
    assert isinstance(cost_usd, float), (
        f"expected the llm.call event to record a real float cost_usd, got {cost_usd!r}"
    )
    assert cost_usd > 0
