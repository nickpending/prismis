"""The context analyzer reads only checked, clean upvoted items (injection-flag C18, C19).

Invariants protected:
  - with the check configured, the context-suggestion request carries the text of
    upvoted items whose judgement is clean, and never a flagged or unchecked one's
  - when no upvoted item is checked and clean, no request is made and the route says
    how to check them

Real collaborators: the real FastAPI app with its own Storage and Config dependencies
over the sealed database and config.toml, and the real ContextAnalyzer posting to
`local_pipeline_stub`, whose recorded request is what the provider would have read.
"""

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from conftest import (
    TEST_API_KEY,
    configure_decisions_service,
    configure_local_services,
)
from prismis_daemon.api import app
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

pytestmark = pytest.mark.usefixtures("no_network")


def _judgement(flagged: bool) -> dict[str, Any]:
    return {
        "injection": {
            "score": 0.9 if flagged else 0.02,
            "flagged": flagged,
            "chunk": 0,
            "chunks": 1,
            "start": 0,
            "end": 80,
        }
    }


def _upvoted(storage: Storage, source_id: str, title: str, analysis: dict) -> None:
    content_id = storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id=title,
            title=title,
            url=f"https://example.com/{len(title)}",
            content=f"{title}: the article body.",
            summary=f"Summary of {title}.",
            analysis=analysis,
        )
    )
    assert content_id is not None
    assert storage.update_content_status(content_id, user_feedback="up")


def _configured(isolated_xdg_env: Path, local_pipeline_stub: str) -> None:
    """Light service on the stub; a decisions service configured, never called here."""
    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    configure_decisions_service(isolated_xdg_env.parent, "http://127.0.0.1:1/unused")


def test_only_the_clean_upvoted_item_reaches_the_context_request(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    stub_requests: list[dict[str, Any]],
) -> None:
    """
    INVARIANT: of the upvoted items, only checked clean ones reach the model
    BREAKS: a flagged or never-checked item's text is handed to the context analyzer
    """
    _configured(isolated_xdg_env, local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Feed")
    _upvoted(storage, source_id, "Flagged CRDT article", _judgement(True))
    _upvoted(storage, source_id, "Unchecked CRDT article", {})
    _upvoted(storage, source_id, "Clean CRDT article", _judgement(False))

    TestClient(app).post("/api/context", headers={"X-API-Key": TEST_API_KEY})

    assert len(stub_requests) == 1, "one context-analysis request expected"
    sent = str(stub_requests[0])
    assert "Clean CRDT article" in sent
    assert "Flagged CRDT article" not in sent
    assert "Unchecked CRDT article" not in sent


def test_no_request_is_made_when_no_upvoted_item_is_checked_and_clean(
    test_db: Path,
    isolated_xdg_env: Path,
    local_pipeline_stub: str,
    stub_requests: list[dict[str, Any]],
) -> None:
    """
    INVARIANT: with nothing safe to send, the route sends nothing and names the command
               that checks the upvoted items
    BREAKS: the route sends unsafe items for want of safe ones, or fails without saying
            how to recover
    """
    _configured(isolated_xdg_env, local_pipeline_stub)
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Feed")
    _upvoted(storage, source_id, "Flagged CRDT article", _judgement(True))
    _upvoted(storage, source_id, "Unchecked CRDT article", {})

    response = TestClient(app).post("/api/context", headers={"X-API-Key": TEST_API_KEY})

    assert stub_requests == [], "no completion request may be made"
    assert response.status_code == 422, response.text
    assert "prismis-cli analyze injection --upvoted" in response.json()["message"]
