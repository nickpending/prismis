"""Each fetch cycle scores against context.md as it is on disk at the start of the cycle.

The daemon used to score with the context Config read at startup, so the user's own
:context edit did nothing until a restart. Every test here drives the orchestrator's real
run_once with the LLM answered by `local_pipeline_stub` and reads what the evaluator sent
from the requests the stub received.
"""

import logging
import os
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from prismis_daemon.api import app, get_storage
from prismis_daemon.config import Config
from prismis_daemon.defaults import DEFAULT_CONTEXT_MD
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.models import ContentItem
from prismis_daemon.orchestrator import DaemonOrchestrator
from prismis_daemon.storage import Storage
from prismis_daemon.summarizer import ContentSummarizer

from conftest import TEST_API_KEY, configure_local_services, message_text

_READABLE = (
    "Researchers at the university published a new study this week examining "
    "long-term outcomes across a decade of patient records, finding a pattern "
    "that held even after controlling for age, income, and health history."
)

_EVALUATE_MARKER = "Evaluate this content and respond with the JSON format"

_FIRST_CONTEXT = (
    "# Personal Context\n\n## High Priority Topics\n- Quantum widget research\n\n"
    "## Medium Priority Topics\n- Gardening\n\n## Low Priority Topics\n- Chess\n\n"
    "## Not Interested\n- Celebrity gossip\n"
)
_NEW_BULLET = "- Zebra husbandry techniques"
_SECOND_CONTEXT = _FIRST_CONTEXT.replace(
    "- Quantum widget research\n", f"- Quantum widget research\n{_NEW_BULLET}\n"
)


class _NoItems:
    def fetch_content(self, source: dict, **kw: object) -> list:
        return []


class _FreshItemPerCycle:
    """A fetcher whose every call returns one item the database has not seen."""

    def __init__(self, source_id: str) -> None:
        self.source_id = source_id
        self.calls = 0

    def fetch_content(self, source: dict, **kw: object) -> list[ContentItem]:
        self.calls += 1
        return [
            ContentItem(
                source_id=self.source_id,
                external_id=f"cycle-item-{self.calls}",
                title=f"Cycle item {self.calls}",
                url=f"https://news.example.com/{self.calls}",
                content=_READABLE,
            )
        ]


def _evaluator_prompts_since(requests: list[dict[str, Any]], seen: int) -> list[str]:
    """User prompts of the evaluator's requests the stub received after the first
    `seen` requests."""
    return [
        message_text(request, "user")
        for request in requests[seen:]
        if _EVALUATE_MARKER in message_text(request, "user")
    ]


@pytest.fixture
def cycle_env(
    test_db: Path, isolated_xdg_env: Path, local_pipeline_stub: str
) -> tuple[DaemonOrchestrator, Path]:
    """An orchestrator built from the sealed config whose context.md lists a High topic,
    and the path of that context.md."""
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    context_path = isolated_xdg_env / "context.md"
    context_path.write_text(_FIRST_CONTEXT)
    config = Config.from_file()
    assert "Quantum widget research" in config.context

    storage = Storage(test_db)
    source_id = storage.add_source("https://news.example.com/feed", "reddit", "News")
    orchestrator = DaemonOrchestrator(
        storage=storage,
        rss_fetcher=_NoItems(),
        reddit_fetcher=_FreshItemPerCycle(source_id),
        youtube_fetcher=_NoItems(),
        file_fetcher=_NoItems(),
        summarizer=ContentSummarizer(config.llm_light_service),
        evaluator=ContentEvaluator(config.llm_light_service),
        config=config,
    )
    return orchestrator, context_path


def test_an_edit_to_context_md_reaches_the_next_cycle_without_a_restart(
    cycle_env: tuple[DaemonOrchestrator, Path],
    stub_requests: list[dict[str, Any]],
) -> None:
    """
    INVARIANT: the evaluator's prompt in a cycle carries context.md as it is on disk when
               the cycle starts, not the copy Config read at startup
    BREAKS: scoring with config.context leaves the user's own :context edit (and any
            accepted carve-out) doing nothing until the daemon restarts
    """
    orchestrator, context_path = cycle_env

    first = orchestrator.run_once()
    first_prompts = _evaluator_prompts_since(stub_requests, 0)
    seen = len(stub_requests)
    context_path.write_text(_SECOND_CONTEXT)
    second = orchestrator.run_once()
    second_prompts = _evaluator_prompts_since(stub_requests, seen)

    assert first["total_new"] == 1, first["errors"]
    assert second["total_new"] == 1, second["errors"]
    assert len(first_prompts) == 1 and len(second_prompts) == 1
    assert "Quantum widget research" in first_prompts[0]
    assert _NEW_BULLET not in first_prompts[0]
    assert _NEW_BULLET in second_prompts[0]


def test_an_unreadable_context_md_keeps_the_text_last_read_and_logs_why(
    cycle_env: tuple[DaemonOrchestrator, Path],
    stub_requests: list[dict[str, Any]],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    INVARIANT: when context.md cannot be read at the start of a cycle, the cycle logs the
               failure with its traceback, scores with the text it last read
               successfully, and completes
    BREAKS: failing the cycle on a transient read error stops all scoring; falling back
            to the startup copy or the default context scores against the wrong interests
    """
    orchestrator, context_path = cycle_env
    context_path.write_text(_SECOND_CONTEXT)
    orchestrator.run_once()
    seen = len(stub_requests)

    context_path.chmod(0)
    try:
        if os.access(context_path, os.R_OK):
            pytest.skip("permission bits do not stop this user reading the file")
        with caplog.at_level(logging.ERROR, logger="prismis_daemon.orchestrator"):
            stats = orchestrator.run_once()
    finally:
        context_path.chmod(0o644)

    prompts = _evaluator_prompts_since(stub_requests, seen)
    assert stats["total_new"] == 1, stats["errors"]
    assert len(prompts) == 1
    assert _NEW_BULLET in prompts[0], "scored with the text last read successfully"
    failures = [r for r in caplog.records if r.exc_info and "context.md" in r.message]
    assert len(failures) == 1, [r.message for r in caplog.records]
    exc_info = failures[0].exc_info
    assert isinstance(exc_info, tuple)
    assert isinstance(exc_info[1], ValueError)
    assert isinstance(exc_info[1].__cause__, PermissionError)


def test_a_missing_context_md_scores_with_the_default_context(
    cycle_env: tuple[DaemonOrchestrator, Path],
    stub_requests: list[dict[str, Any]],
) -> None:
    """A deleted context.md is the default context, as `Config.from_file` has it."""
    orchestrator, context_path = cycle_env
    orchestrator.run_once()
    seen = len(stub_requests)

    context_path.unlink()
    stats = orchestrator.run_once()

    prompts = _evaluator_prompts_since(stub_requests, seen)
    assert stats["total_new"] == 1, stats["errors"]
    assert len(prompts) == 1
    assert DEFAULT_CONTEXT_MD in prompts[0]
    assert "Quantum widget research" not in prompts[0]


def test_context_suggest_reads_context_md_as_it_is_on_disk(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    stub_requests: list[dict[str, Any]],
) -> None:
    """
    INVARIANT: POST /api/context sends the analyzer the context.md on disk when the
               request arrives, through the real get_config dependency
    BREAKS: a dependency that cached its Config would leave :context suggest proposing
            against a file the user has since edited, until the daemon restarts
    """
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), local_pipeline_stub)
    context_path = isolated_xdg_env / "context.md"
    context_path.write_text(_FIRST_CONTEXT)
    storage = Storage(test_db)
    source_id = storage.add_source("https://news.example.com/feed", "rss", "News")
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="flagged",
            title="Flagged",
            url="https://news.example.com/flagged",
            content=_READABLE,
        )
    )
    assert content_id is not None
    storage.update_content_status(content_id, user_feedback="up")
    client = TestClient(app)
    context_path.write_text(_SECOND_CONTEXT)

    async def override_get_storage():  # type: ignore[no-untyped-def]
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage
    try:
        response = client.post("/api/context", headers={"X-API-Key": TEST_API_KEY})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200, response.text
    assert len(stub_requests) == 1
    prompt = message_text(stub_requests[0], "user") + message_text(
        stub_requests[0], "system"
    )
    assert _NEW_BULLET.removeprefix("- ") in prompt
