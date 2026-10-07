"""The record/replay mechanism behind `local_pipeline_stub`.

Every test enters through `llm_client.complete`, the call production makes, against the
real stub server and the real recorder; nothing here stands in for the code under test.
The recordings are written into a per-test temp directory, so what a replay does is
decided by the file the test wrote, not by the committed ones.
"""

import http.server
import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import openai
import pytest

from conftest import (
    LOCAL_LIGHT_SERVICE,
    LlmRecorder,
    RecordTarget,
    configure_local_services,
    resolve_record_target,
)
from prismis_daemon import llm_client
from prismis_daemon.llm_client import ConfigError

MODULE = "test_llm_recording_unit"
RECORDED_MODEL = "openai/gpt-5.4-nano"


@pytest.fixture
def llm_recordings_dir(tmp_path: Path) -> Path:
    return tmp_path / "recordings"


@pytest.fixture
def llm_record_target(request: pytest.FixtureRequest) -> RecordTarget | None:
    """Replay unless the test brings its own provider."""
    if "fake_provider" in request.fixturenames:
        target = request.getfixturevalue("fake_provider").target
        assert isinstance(target, RecordTarget)
        return target
    return None


class FakeProvider:
    """A local server standing in for the operator's real provider in record mode."""

    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self.headers: list[dict[str, str]] = []
        self.target: RecordTarget | None = None


@pytest.fixture
def fake_provider() -> Iterator[FakeProvider]:
    provider = FakeProvider()

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            provider.received.append(json.loads(raw))
            provider.headers.append({k.lower(): v for k, v in self.headers.items()})
            body = json.dumps(
                {
                    "id": "real",
                    "object": "chat.completion",
                    "model": "provider/real-model-0613",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"real reply {len(provider.received)}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 4},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    provider.target = RecordTarget(
        base_url=f"http://127.0.0.1:{server.server_address[1]}/v1",
        api_key="provider-secret",
        model="provider/chosen-model",
        headers={"X-OpenRouter-Title": "bench"},
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield provider
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def live_request(prompt: str) -> dict[str, Any]:
    """What `llm_client.complete(prompt, service=LOCAL_LIGHT_SERVICE)` puts on the wire."""
    return {
        "model": "stub-model",
        "messages": [{"role": "user", "content": prompt}],
    }


def write_recording(directory: Path, test_name: str, entries: list[dict[str, Any]]) -> Path:
    path = directory / MODULE / f"{test_name}.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(entries))
    return path


def entry(prompt: str, reply: str, model: str = RECORDED_MODEL) -> dict[str, Any]:
    return {
        "request": {**live_request(prompt), "model": model},
        "reply": reply,
        "model": model,
    }


def call(stub: str, isolated_xdg_env: Path, prompt: str) -> llm_client.CompleteResult:
    configure_local_services(isolated_xdg_env.parent, stub)
    return llm_client.complete(prompt, service=LOCAL_LIGHT_SERVICE)


def recording_path(directory: Path, request: pytest.FixtureRequest) -> Path:
    return directory / MODULE / f"{request.node.name}.json"


@pytest.mark.recorded_llm
def test_replay_serves_recorded_replies_in_call_order_under_no_network(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    write_recording(
        llm_recordings_dir,
        request.node.name,
        [entry("first", "reply one"), entry("second", "reply two", "other/model")],
    )
    first = call(local_pipeline_stub, isolated_xdg_env, "first")
    second = call(local_pipeline_stub, isolated_xdg_env, "second")
    assert (first.text, first.model) == ("reply one", RECORDED_MODEL)
    assert (second.text, second.model) == ("reply two", "other/model")


def test_no_network_still_stops_every_non_loopback_request(no_network: None) -> None:
    with pytest.raises(httpx.ConnectError):
        httpx.get("http://203.0.113.1/", timeout=5)


def test_unmarked_test_keeps_the_canned_reply(
    local_pipeline_stub: str, isolated_xdg_env: Path, no_network: None
) -> None:
    result = call(local_pipeline_stub, isolated_xdg_env, "anything")
    assert "A stubbed summary." in result.text
    assert result.model == "stub-model"


@pytest.mark.recorded_llm
def test_changed_prompt_text_is_refused_naming_file_and_record_command(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    llm_recorder: LlmRecorder,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    path = write_recording(
        llm_recordings_dir, request.node.name, [entry("the recorded prompt", "stale")]
    )
    with pytest.raises(openai.BadRequestError) as refused:
        call(local_pipeline_stub, isolated_xdg_env, "a newer prompt")
    message = str(refused.value)
    assert str(path) in message
    assert "PRISMIS_RECORD_LLM" in message
    assert "recorded request" in message
    assert len(llm_recorder.failures) == 1, "a refusal must also be kept for teardown"
    assert "stale" not in message
    llm_recorder.failures.clear()


@pytest.mark.recorded_llm
def test_missing_recording_is_refused_not_skipped_or_canned(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    llm_recorder: LlmRecorder,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    path = recording_path(llm_recordings_dir, request)
    assert not path.exists()
    with pytest.raises(openai.BadRequestError) as refused:
        call(local_pipeline_stub, isolated_xdg_env, "anything")
    message = str(refused.value)
    assert str(path) in message
    assert "PRISMIS_RECORD_LLM" in message
    assert len(llm_recorder.failures) == 1
    llm_recorder.failures.clear()


@pytest.mark.parametrize(
    ("recorded_prompt", "live_prompt", "recorded_model"),
    [
        ("Today is 2026-01-02.", "Today is 2027-05-06.", RECORDED_MODEL),
        ("Posted March 3, 2025.", "Posted October 12, 2026.", RECORDED_MODEL),
        ("Same text.", "Same text.", "some/other-model"),
    ],
    ids=["iso-date", "long-date", "model-name"],
)
@pytest.mark.recorded_llm
def test_date_or_model_only_difference_replays(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    no_network: None,
    request: pytest.FixtureRequest,
    recorded_prompt: str,
    live_prompt: str,
    recorded_model: str,
) -> None:
    write_recording(
        llm_recordings_dir,
        request.node.name,
        [entry(recorded_prompt, "recorded reply", recorded_model)],
    )
    result = call(local_pipeline_stub, isolated_xdg_env, live_prompt)
    assert result.text == "recorded reply"


@pytest.mark.recorded_llm
def test_a_number_that_is_not_a_date_is_not_ignored(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    llm_recorder: LlmRecorder,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    write_recording(
        llm_recordings_dir, request.node.name, [entry("Version 1.2.3 shipped.", "r")]
    )
    with pytest.raises(openai.BadRequestError):
        call(local_pipeline_stub, isolated_xdg_env, "Version 1.2.4 shipped.")
    llm_recorder.failures.clear()


@pytest.mark.recorded_llm
def test_call_beyond_the_recording_is_refused(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    llm_recorder: LlmRecorder,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    write_recording(llm_recordings_dir, request.node.name, [entry("only", "one")])
    assert call(local_pipeline_stub, isolated_xdg_env, "only").text == "one"
    with pytest.raises(openai.BadRequestError, match="holds 1"):
        call(local_pipeline_stub, isolated_xdg_env, "only")
    llm_recorder.failures.clear()


def test_teardown_fails_a_recording_the_test_did_not_use_up(tmp_path: Path) -> None:
    path = tmp_path / MODULE / "t.json"
    write_recording(tmp_path, "t", [entry("a", "1"), entry("b", "2")])
    recorder = LlmRecorder(path, "tests/x.py::t", None)
    assert recorder.handle(live_request("a"))[0] == 200
    with pytest.raises(pytest.fail.Exception, match="recording holds 2"):
        recorder.finalize()


def test_teardown_fails_when_the_recording_is_missing_and_no_call_was_made(
    tmp_path: Path,
) -> None:
    recorder = LlmRecorder(tmp_path / MODULE / "absent.json", "tests/x.py::absent", None)
    with pytest.raises(pytest.fail.Exception, match="absent.json"):
        recorder.finalize()


@pytest.mark.recorded_llm
def test_record_mode_forwards_to_the_service_and_writes_the_recording(
    local_pipeline_stub: str,
    llm_recordings_dir: Path,
    isolated_xdg_env: Path,
    fake_provider: FakeProvider,
    no_network: None,
    request: pytest.FixtureRequest,
) -> None:
    first = call(local_pipeline_stub, isolated_xdg_env, "first prompt")
    second = call(local_pipeline_stub, isolated_xdg_env, "second prompt")

    # the provider's reply, not a canned or replayed one
    assert (first.text, second.text) == ("real reply 1", "real reply 2")
    assert first.model == "provider/real-model-0613"

    # the request went to that service, with the chosen model and its credentials
    assert [r["model"] for r in fake_provider.received] == ["provider/chosen-model"] * 2
    assert fake_provider.received[0]["messages"] == live_request("first prompt")["messages"]
    assert fake_provider.headers[0]["authorization"] == "Bearer provider-secret"
    assert fake_provider.headers[0]["x-openrouter-title"] == "bench"

    # request, real reply and model written in call order
    written = json.loads(recording_path(llm_recordings_dir, request).read_text())
    assert [(e["reply"], e["model"]) for e in written] == [
        ("real reply 1", "provider/real-model-0613"),
        ("real reply 2", "provider/real-model-0613"),
    ]
    assert written[0]["request"] == fake_provider.received[0]
    assert written[1]["request"]["messages"] == live_request("second prompt")["messages"]


def test_record_target_comes_from_the_services_toml_named(tmp_path: Path) -> None:
    (tmp_path / "services.toml").write_text(
        '[services.real]\nadapter = "openai"\nbase_url = "http://provider.example/v1/"\n'
        'key_required = false\ndefault_model = "svc/default"\napp_title = "bench"\n'
    )
    target = resolve_record_target("real", None, tmp_path)
    assert target == RecordTarget(
        base_url="http://provider.example/v1",
        api_key=None,
        model="svc/default",
        headers={"X-OpenRouter-Title": "bench"},
    )
    assert resolve_record_target("real", "chosen/model", tmp_path).model == "chosen/model"
    with pytest.raises(ConfigError, match="Unknown service"):
        resolve_record_target("missing", None, tmp_path)
