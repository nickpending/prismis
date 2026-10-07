"""`analyze repair` records a cut article the way the daemon pipeline does
(bounded-llm-content SC-4).

Invariants protected:
- an item whose content is over the bound, repaired through `analyze repair`, is stored
  with its full content and analysis `content_bounded` {sent_bytes, total_bytes}
- an item under the bound, repaired the same way, carries no `content_bounded` key
- the model is sent no more than MAX_CONTENT_BYTES bytes of the article

Per constitution Principle I only `prismis_daemon.llm_call.complete` (the shared LLM-call
boundary) is stood in for; Storage, Config, ContentSummarizer and ContentEvaluator run
for real against a real temp database and a real sealed config.toml.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest
from typer.testing import CliRunner

from cli.analyze import app as analyze_app
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.database import init_db
from prismis_daemon.defaults import DEFAULT_CONFIG_TOML, DEFAULT_CONTEXT_MD
from prismis_daemon.llm_call import MAX_CONTENT_BYTES
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

from conftest import TEST_API_KEY

_PATCH_COMPLETE = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock

runner = CliRunner()

_SUMMARY: dict[str, Any] = {
    "summary": "A short summary.",
    "reading_summary": "# Title\n\nSomething happened.",
    "alpha_insights": [],
    "patterns": [],
    "quotes": [],
    "tools": [],
    "urls": [],
}
_EVAL: dict[str, Any] = {
    "priority": "medium",
    "matched_interests": [],
    "reasoning": "matches",
}


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


@pytest.fixture
def local_db(isolated_xdg_env: Path) -> Path:
    isolated_xdg_env.joinpath("config.toml").write_text(
        DEFAULT_CONFIG_TOML.format(api_key=TEST_API_KEY)
    )
    isolated_xdg_env.joinpath("context.md").write_text(DEFAULT_CONTEXT_MD)
    db_path = Path(os.environ["XDG_DATA_HOME"]) / "prismis" / "prismis.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    return db_path


def _fake(payload: dict[str, Any]) -> MagicMock:  # claudex-guard: allow-mock
    fake = MagicMock()  # claudex-guard: allow-mock
    fake.text = json.dumps(payload)
    fake.tokens.input = 10
    fake.tokens.output = 5
    fake.cost = 0.0
    fake.model = "gpt-4.1-mini"
    fake.duration_ms = 1
    return fake


def _repair(db_path: Path, content: str) -> tuple[dict[str, Any], list[str]]:
    """Repair one item holding `content`; return its stored row and the prompts sent."""
    storage = Storage(db_path)
    source_id = storage.add_source("https://feeds.example.com/rss", "rss", "Feed")
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=str(uuid.uuid4()),
            title="Needs Repair",
            url="http://example.com/x",
            content=content,
        )
    )
    assert content_id is not None
    storage.close()

    prompts: list[str] = []
    replies = [_fake(_SUMMARY), _fake(_EVAL)]

    def _complete(*, prompt: str, **_rest: object) -> MagicMock:  # claudex-guard: allow-mock
        prompts.append(prompt)
        return replies.pop(0)

    with patch(_PATCH_COMPLETE, side_effect=_complete):  # claudex-guard: allow-mock
        result = runner.invoke(analyze_app, ["repair", "--force", "--limit", "1"])
    assert result.exit_code == 0, result.output

    reread = Storage(db_path)
    stored = reread.get_content_by_id(content_id)
    reread.close()
    assert stored is not None
    return stored, prompts


def test_repair_of_an_over_bound_item_records_content_bounded(local_db: Path) -> None:
    """
    SC-4: repair of an article over the bound stores the full content, sends the model
    at most the bound, and records content_bounded.
    BREAKS: repair assembling its analysis without the cut record, or sending the model
    the whole article.
    """
    content = "─" * 550_000 + "word " * 110_000

    stored, prompts = _repair(local_db, content)

    assert stored["content"] == content
    assert stored["analysis"]["content_bounded"] == {
        "sent_bytes": MAX_CONTENT_BYTES - 2,
        "total_bytes": len(content.encode()),
    }
    assert all(len(p.encode()) < MAX_CONTENT_BYTES + 5_000 for p in prompts)


def test_repair_of_an_under_bound_item_records_no_content_bounded(
    local_db: Path,
) -> None:
    """
    SC-4: repair of a fitting article carries no content_bounded key.
    BREAKS: the key written unconditionally, so every repaired item looks cut.
    """
    stored, _ = _repair(local_db, "A short but real article. " * 40)

    assert "content_bounded" not in stored["analysis"]
