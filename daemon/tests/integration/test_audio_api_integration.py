"""Integration tests for audio briefing API - protecting invariants."""

import os
import stat
import time

import pytest
from pathlib import Path
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from prismis_daemon import api, audio
from prismis_daemon.storage import Storage
from prismis_daemon.models import ContentItem
from conftest import TEST_API_KEY, configure_local_services

app = api.app


@pytest.fixture
def api_client() -> TestClient:
    """Create test client for API."""
    return TestClient(app)


def test_audio_fails_without_high_priority(
    api_client: TestClient, test_db: Path
) -> None:
    """
    INVARIANT: AudioScriptGenerator requires HIGH priority content
    BREAKS: User gets confusing error vs actionable message
    """
    # Ensure database has content but no HIGH priority
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test Feed")

    # Add only MEDIUM priority content
    item = ContentItem(
        source_id=source_id,
        external_id="test-medium-1",
        title="Medium Priority Article",
        url="https://example.com/article",
        content="Test content",
        summary="Test summary",
        priority="medium",
        published_at=datetime.now(),
    )
    storage.add_content(item)
    storage.close()

    # Call audio endpoint
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": TEST_API_KEY},
    )

    # Should fail with ValidationError and helpful message
    assert response.status_code == 422
    data = response.json()
    assert data["success"] is False
    assert "high priority" in data["message"].lower()
    assert "Add content sources" in data["message"] or "adjust" in data["message"]


def _install_lspeak(
    bin_dir: Path, monkeypatch: pytest.MonkeyPatch, hang_seconds: int = 0
) -> None:
    """A stand-in `lspeak` first on PATH that writes some bytes to its -o file.

    The briefing script comes from a recorded real LLM reply; only the TTS binary is
    stood in for, so the test needs neither a TTS provider nor a machine with lspeak.
    With `hang_seconds`, it becomes `sleep` (exec, so no child outlives the kill)
    and never writes, the way a hung provider call behaves.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    exe = bin_dir / "lspeak"
    body = (
        f"exec sleep {hang_seconds}\n"
        if hang_seconds
        else (
            'out=""\n'
            'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && out="$2"; shift; done\n'
            'echo audio-bytes > "$out"\n'
        )
    )
    exe.write_text("#!/bin/sh\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


@pytest.mark.recorded_llm
def test_audio_generates_with_high_priority(
    api_client: TestClient,
    test_db: Path,
    local_pipeline_stub: str,
    isolated_xdg_env: Path,
    no_network: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    INVARIANT: Audio generation succeeds with HIGH priority content
    BREAKS: Feature unusable if broken

    The script is the production model's recorded reply, replayed by the local stub.
    """
    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    _install_lspeak(tmp_path / "bin", monkeypatch)
    # Add HIGH priority content
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Rust Blog")

    item = ContentItem(
        source_id=source_id,
        external_id="test-high-1",
        title="Important Rust Release",
        url="https://example.com/rust",
        content="Rust 1.80 introduces significant performance improvements and new features for async programming.",
        summary="Rust 1.80 introduces significant performance improvements",
        priority="high",
        # The prompt carries "N hours ago"; a fixed offset from now keeps it "2 hours
        # ago" on any host, so the recorded request keeps matching.
        published_at=datetime.now(timezone.utc) - timedelta(hours=2, minutes=30),
    )
    storage.add_content(item)
    storage.close()

    # Call audio endpoint (stand-in lspeak writes the audio file)
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": TEST_API_KEY},
    )

    # Should succeed
    assert response.status_code == 200, f"Failed: {response.json()}"
    data = response.json()
    assert data["success"] is True
    assert "briefing-" in data["data"]["filename"]
    assert data["data"]["filename"].endswith(".mp3")
    assert data["data"]["high_priority_count"] >= 1
    assert data["data"]["provider"] in ["system", "elevenlabs"]

    # Verify file was actually created
    file_path = Path(data["data"]["file_path"])
    assert file_path.exists(), f"Audio file not created: {file_path}"
    assert file_path.stat().st_size > 0, "Audio file is empty"


@pytest.mark.recorded_llm
def test_a_hung_tts_run_fails_the_briefing_at_the_timeout(
    api_client: TestClient,
    test_db: Path,
    local_pipeline_stub: str,
    isolated_xdg_env: Path,
    no_network: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    INVARIANT: an lspeak run that never finishes is killed at the engine's timeout and
               the endpoint answers with an error
    BREAKS: a hung TTS provider holds the request (and a worker thread) indefinitely
    """
    configure_local_services(isolated_xdg_env.parent, local_pipeline_stub)
    _install_lspeak(tmp_path / "bin", monkeypatch, hang_seconds=30)
    monkeypatch.setattr(audio, "LSPEAK_TIMEOUT_SECONDS", 1.0)
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed", "rss", "Test")
    storage.add_content(
        ContentItem(
            source_id=source_id,
            external_id="test-hung-tts-1",
            title="Rust 1.80 Released",
            url="https://example.com/rust-180",
            content="Rust 1.80 introduces significant performance improvements",
            summary="Rust 1.80 introduces significant performance improvements",
            priority="high",
            published_at=datetime.now(timezone.utc) - timedelta(hours=2, minutes=30),
        )
    )
    storage.close()

    start = time.monotonic()
    response = api_client.post(
        "/api/audio/briefings", headers={"X-API-Key": TEST_API_KEY}
    )
    elapsed = time.monotonic() - start

    assert elapsed < 15, f"the hung lspeak held the request for {elapsed:.1f}s"
    assert response.status_code == 500, response.text
    assert "Audio generation failed" in response.json()["message"]


def test_audio_requires_authentication(api_client: TestClient) -> None:
    """
    INVARIANT: Audio endpoint requires API key authentication
    BREAKS: Unauthorized access to resource-intensive operation
    """
    # Without API key
    response = api_client.post("/api/audio/briefings")
    assert response.status_code == 403
    data = response.json()
    assert data["success"] is False
    assert "API key" in data["message"]

    # With invalid API key
    response = api_client.post(
        "/api/audio/briefings",
        headers={"X-API-Key": "wrong-key"},
    )
    assert response.status_code == 403
