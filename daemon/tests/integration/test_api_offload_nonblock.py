"""Non-blocking proof for every API handler that makes a slow synchronous call.

Invariant: a slow embedding, LLM or TTS call inside an `async def` handler runs in a
worker thread (`asyncio.to_thread`), so the event loop keeps serving other routes.
Without the offload the call runs on the loop thread and every other request waits.

Shape (same as test_extract_endpoint_race.py): the ASGI app under httpx, the slow
request and a probe request as two tasks in one event loop. Nothing of prismis is
replaced (constitution Principle I); the slowness is produced at the real boundary
each call crosses, and every one of those blocks the calling thread:
- LLM calls (briefing script, context analysis): the local stub LLM server sleeps
  before answering, triggered by a delay marker in the item title that travels into
  the real prompt (see conftest.local_pipeline_stub).
- TTS: a real `lspeak` executable first on PATH, a shell script that sleeps.
- Embeddings: the third-party SentenceTransformer's `encode` is a `time.sleep`
  (`asyncio.sleep` would yield the loop and could not tell an offloaded handler from
  a blocking one); its constructor is a no-op so no model is downloaded.

The probe's elapsed time is measured from `start`, captured before the tasks are
scheduled: a blocked loop stalls both the probe's delay and the probe itself, so a
clock started inside the probe would absorb the stall and pass regardless. The slow
request must also take at least the blocking interval, which proves the slow path
was reached rather than skipped.
"""

from __future__ import annotations

import asyncio
import os
import stat
import time
from collections.abc import Generator
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from httpx import ASGITransport, AsyncClient, Response
from numpy.typing import NDArray
from sentence_transformers import SentenceTransformer

from conftest import (
    DEEP_EXTRACT_DELAY_PREFIX,
    LOCAL_DEEP_SERVICE,
    TEST_API_KEY,
    add_new_content,
    configure_local_services,
)
from prismis_daemon.api import _extract_locks, app, get_storage
from prismis_daemon.circuit_breaker import reset_circuit_breaker
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# How long the slow call blocks, when to fire the probe, and the bound the probe must
# beat. A blocked loop holds the probe until ~BLOCK_SECONDS; a free loop answers it
# within a few ms of PROBE_DELAY. The bound sits between the two with wide margins.
BLOCK_SECONDS = 2.0
PROBE_DELAY = 0.5
PROBE_BOUND = 1.2
SLOW_FLOOR = BLOCK_SECONDS * 0.9

_SLOW_MARKER = f"{DEEP_EXTRACT_DELAY_PREFIX}{BLOCK_SECONDS}"


@pytest.fixture(autouse=True)
def clean_state() -> Generator[None]:
    reset_circuit_breaker()
    _extract_locks.clear()
    yield
    reset_circuit_breaker()
    _extract_locks.clear()
    app.dependency_overrides.clear()


def _override_storage(storage: Storage) -> None:
    def override_get_storage() -> Generator[Storage]:
        yield storage

    app.dependency_overrides[get_storage] = override_get_storage


def _add_item(
    storage: Storage,
    external_id: str,
    priority: str | None,
    title: str = "Offload Test Article",
) -> str:
    source_id = storage.add_source("https://example.com/rss", "rss", "Offload Feed")
    item = ContentItem(
        source_id=source_id,
        external_id=external_id,
        title=title,
        url="https://example.com/offload",
        content="Article body text for offload testing.",
        summary="Light summary.",
        priority=priority,
        published_at=datetime.now(),
    )
    return add_new_content(storage, item)


def _point_llm_at_stub(base_url: str) -> None:
    configure_local_services(Path(os.environ["XDG_CONFIG_HOME"]), base_url)


async def _slow_then_probe(
    method: str, path: str, params: dict[str, str] | None = None
) -> tuple[Response, float, float]:
    """Fire the slow request, then a /health probe PROBE_DELAY later.

    Returns the slow response, its elapsed seconds, and the probe's elapsed seconds,
    both measured from `start`.
    """
    elapsed: dict[str, float] = {}
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        start = time.monotonic()

        async def slow() -> Response:
            r = await client.request(
                method,
                path,
                headers={"X-API-Key": TEST_API_KEY},
                timeout=30.0,
                params=params,
            )
            elapsed["slow"] = time.monotonic() - start
            return r

        async def probe() -> None:
            await asyncio.sleep(PROBE_DELAY)
            r = await client.get("/health", timeout=30.0)
            elapsed["probe"] = time.monotonic() - start
            assert r.status_code == 200, r.text

        slow_response, _ = await asyncio.gather(slow(), probe())
    return slow_response, elapsed["slow"], elapsed["probe"]


def _assert_probe_free(probe_elapsed: float, slow_elapsed: float, what: str) -> None:
    assert slow_elapsed >= SLOW_FLOOR, (
        f"{what}: the slow request finished in {slow_elapsed:.3f}s, under the "
        f"{BLOCK_SECONDS}s block -- the slow call was never reached"
    )
    assert probe_elapsed < PROBE_BOUND, (
        f"/health took {probe_elapsed:.3f}s (bound {PROBE_BOUND}s) while {what} "
        f"blocked for {BLOCK_SECONDS}s -- the event loop was held; the call is not "
        f"offloaded"
    )


class _SlowEncoder:
    """Counts `encode` calls so a test can prove the handler reached the embedder."""

    hits = 0


@pytest.fixture
def slow_embedding(monkeypatch: pytest.MonkeyPatch) -> type[_SlowEncoder]:
    """Make the third-party model's encode block; never downloads a model."""
    _SlowEncoder.hits = 0

    def encode(
        self: SentenceTransformer, *args: object, **kwargs: object
    ) -> NDArray[np.float64]:
        _SlowEncoder.hits += 1
        time.sleep(BLOCK_SECONDS)
        return np.zeros(384)

    monkeypatch.setattr(SentenceTransformer, "__init__", lambda self, *a, **k: None)
    monkeypatch.setattr(SentenceTransformer, "encode", encode)
    return _SlowEncoder


@pytest.mark.asyncio
async def test_semantic_search_does_not_block_loop(
    test_db: Path, slow_embedding: type[_SlowEncoder]
) -> None:
    storage = Storage(test_db)
    _override_storage(storage)

    response, slow_elapsed, probe_elapsed = await _slow_then_probe(
        "GET", "/api/search", params={"q": "offload"}
    )

    assert slow_embedding.hits == 1
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True
    _assert_probe_free(probe_elapsed, slow_elapsed, "the search embedding")


@pytest.mark.asyncio
async def test_extract_regen_embedding_does_not_block_loop(
    test_db: Path, local_pipeline_stub: str, slow_embedding: type[_SlowEncoder]
) -> None:
    storage = Storage(test_db)
    content_id = _add_item(storage, "offload-extract-1", "high")
    _override_storage(storage)
    _point_llm_at_stub(local_pipeline_stub)
    app.state.deep_extractor = ContentDeepExtractor(LOCAL_DEEP_SERVICE)

    response, slow_elapsed, probe_elapsed = await _slow_then_probe(
        "POST", f"/api/entries/{content_id}/extract"
    )

    assert slow_embedding.hits == 1
    assert response.status_code == 200, response.text
    assert response.json()["message"] == "Deep extraction generated"
    _assert_probe_free(probe_elapsed, slow_elapsed, "the regenerated embedding")


def _install_lspeak(
    bin_dir: Path, monkeypatch: pytest.MonkeyPatch, sleep_seconds: float
) -> None:
    """A real `lspeak` executable first on PATH: sleeps, then writes the -o file."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "lspeak"
    exe.write_text(
        "#!/bin/sh\n"
        'out=""\n'
        'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
        f"sleep {sleep_seconds}\n"
        ': > "$out"\n'
    )
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.parametrize("slow_call", ["generate_script", "tts_generate"])
@pytest.mark.asyncio
async def test_audio_briefing_does_not_block_loop(
    test_db: Path,
    local_pipeline_stub: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    slow_call: str,
) -> None:
    storage = Storage(test_db)
    slow_script = slow_call == "generate_script"
    _add_item(
        storage,
        "offload-audio-1",
        "high",
        title=f"Briefing item {_SLOW_MARKER}" if slow_script else "Briefing item",
    )
    _override_storage(storage)
    _point_llm_at_stub(local_pipeline_stub)
    _install_lspeak(tmp_path / "bin", monkeypatch, 0 if slow_script else BLOCK_SECONDS)

    response, slow_elapsed, probe_elapsed = await _slow_then_probe(
        "POST", "/api/audio/briefings"
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"]["high_priority_count"] == 1
    _assert_probe_free(probe_elapsed, slow_elapsed, slow_call)


@pytest.mark.asyncio
async def test_context_analysis_does_not_block_loop(
    test_db: Path, local_pipeline_stub: str
) -> None:
    storage = Storage(test_db)
    content_id = _add_item(
        storage, "offload-context-1", None, title=f"Flagged item {_SLOW_MARKER}"
    )
    storage.update_content_status(content_id, user_feedback="up")
    _override_storage(storage)
    _point_llm_at_stub(local_pipeline_stub)

    response, slow_elapsed, probe_elapsed = await _slow_then_probe(
        "POST", "/api/context"
    )

    assert response.status_code == 200, response.text
    assert response.json()["data"] == {"suggested_topics": []}
    _assert_probe_free(probe_elapsed, slow_elapsed, "the context analysis")
