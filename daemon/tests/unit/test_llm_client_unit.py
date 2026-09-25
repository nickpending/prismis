"""Unit tests for llm_client -- the openai-SDK module owning complete(),
health_check(), services.toml resolution and JSON extraction (replacing llm-core).

Real collaborators throughout, per the constitution: a local HTTP server standing in
for the provider (the one boundary permitted to fake) plays every role llm-core's own
provider used to. services.toml is written by each test under the sealed
XDG_CONFIG_HOME (conftest.isolated_xdg_env, autouse) -- no mock of prismis_daemon
internals anywhere in this file.
"""

from __future__ import annotations

import asyncio
import http.server
import json
import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch  # claudex-guard: allow-mock -- httpx2, not prismis_daemon

import httpx2
import openai
import pytest

from prismis_daemon import llm_client
from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.context_analyzer import ContextAnalyzer
from prismis_daemon.deep_extractor import ContentDeepExtractor
from prismis_daemon.evaluator import ContentEvaluator
from prismis_daemon.llm_client import CompleteResult
from prismis_daemon.summarizer import ContentSummarizer

# ---------------------------------------------------------------------------
# services.toml plumbing -- every test points its own named service at its own
# local stub server, under the sealed XDG_CONFIG_HOME conftest.isolated_xdg_env
# (autouse) already provides.
# ---------------------------------------------------------------------------


def _write_service(
    name: str,
    base_url: str,
    *,
    default_model: str = "stub-model",
    key_required: bool = False,
) -> None:
    """Append a [services.<name>] block to the sealed services.toml.

    llm_client.resolve_service() always receives an explicit service name (no call
    site relies on services.toml's own default_service), so this writer never needs
    one either -- appending keeps each test's service resolvable without disturbing
    any other test's block already written to the same sealed file.
    """
    cfg_home = Path(os.environ["XDG_CONFIG_HOME"])
    llm_core_dir = cfg_home / "llm-core"
    llm_core_dir.mkdir(parents=True, exist_ok=True)
    services_path = llm_core_dir / "services.toml"

    existing = services_path.read_text() if services_path.exists() else ""
    block = (
        f"[services.{name}]\n"
        'adapter = "openai"\n'
        f'base_url = "{base_url}/v1"\n'
        f"key_required = {'true' if key_required else 'false'}\n"
        f'default_model = "{default_model}"\n\n'
    )
    services_path.write_text(existing + block)


@pytest.fixture(autouse=True)
def _clean_circuit_registry() -> Iterator[None]:
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


@contextmanager
def _running(handler_cls: type[http.server.BaseHTTPRequestHandler]) -> Iterator[str]:
    """Start a local HTTP server for `handler_cls`, yield its base URL, tear it down."""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str), "loopback bind always yields a str host"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _completion_body(content: str, *, finish_reason: str = "stop") -> bytes:
    payload = {
        "id": "stub-completion",
        "object": "chat.completion",
        "model": "stub-model",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 12, "completion_tokens": 4, "total_tokens": 16},
    }
    return json.dumps(payload).encode()


def _content_handler(
    content: str, *, finish_reason: str = "stop"
) -> type[http.server.BaseHTTPRequestHandler]:
    """A handler answering every chat-completions POST with a fixed `content` string
    and `finish_reason` (the provider's raw value, e.g. "stop" or "length")."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = _completion_body(content, finish_reason=finish_reason)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


# ---------------------------------------------------------------------------
# SC-1: complete() returns the llm-core result shape
# ---------------------------------------------------------------------------


def test_complete_result_shape_matches_call_sites() -> None:
    """SC-1: complete() exposes text, model, provider, tokens.input/output,
    finish_reason, duration_ms, cost -- the attribute paths every call site reads."""
    with _running(_content_handler("Hello from the stub.")) as base_url:
        _write_service("result-shape-svc", base_url)
        result = llm_client.complete(
            prompt="Say hi",
            system_prompt="You are terse.",
            service="result-shape-svc",
        )

    assert result.text == "Hello from the stub."
    assert result.model == "stub-model"
    assert result.provider == "openai"
    assert result.tokens.input == 12
    assert result.tokens.output == 4
    assert result.finish_reason == "stop"
    assert isinstance(result.duration_ms, int)
    assert result.duration_ms >= 0
    # base_url is not openrouter.ai -- no usage.include extension sent, no cost back.
    # The openrouter-shaped counterpart of this assertion is
    # test_complete_extracts_real_cost_for_an_openrouter_shaped_base_url below.
    assert result.cost is None


def test_complete_maps_length_finish_reason_to_max_tokens() -> None:
    """SC-1 / F-1-3: the provider's raw "length" finish_reason -- what it sends when a
    response is truncated by max_tokens -- maps to CompleteResult.finish_reason's other
    documented value, "max_tokens". Every other test in this file stubs "stop"; this is
    the one place the gate exercises the ternary's if-branch, not only its else."""
    with _running(
        _content_handler("Truncated output...", finish_reason="length")
    ) as base_url:
        _write_service("length-finish-reason-svc", base_url)
        result = llm_client.complete(
            prompt="Say a lot", service="length-finish-reason-svc"
        )

    assert result.finish_reason == "max_tokens"


# ---------------------------------------------------------------------------
# SC-3: OpenRouter's usage.include cost survives the round trip into
# CompleteResult.cost -- gate-reachable, no network, no live credentials.
#
# _is_openrouter() matches the literal substring "openrouter.ai" in base_url, which is
# also the actual network destination httpx2 would connect to -- a local stub bound to
# 127.0.0.1 can never satisfy it, so this test drives complete() against a real
# openrouter.ai-shaped base_url (identical in shape to the real services.toml's
# prismis-pt-luna entry) and fakes only the wire: httpx2.Client.send, the openai SDK's
# own vendored httpx, not any prismis_daemon code. resolve_service(), _is_openrouter()
# and the extra_body branch in complete() all run for real; only the TCP connection is
# stood in for. This also proves openai's CompletionUsage pydantic model preserves an
# unmodeled "cost" key from the provider's JSON rather than silently dropping it --
# nothing here would pass if it didn't.
# ---------------------------------------------------------------------------


def _openrouter_response(request: httpx2.Request, *, cost: float) -> httpx2.Response:
    payload = {
        "id": "or-completion",
        "object": "chat.completion",
        "model": "openai/gpt-5.6-luna",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "ok"},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 10,
            "completion_tokens": 2,
            "total_tokens": 12,
            "cost": cost,
        },
    }
    return httpx2.Response(
        200,
        request=request,
        headers={"content-type": "application/json"},
        content=json.dumps(payload).encode(),
    )


def test_is_openrouter_matches_the_real_services_toml_shape() -> None:
    """The gate itself, in isolation: matches OpenRouter's real base_url, not
    api.openai.com's, and not an arbitrary substring collision."""
    assert llm_client._is_openrouter("https://openrouter.ai/api/v1") is True
    assert llm_client._is_openrouter("https://api.openai.com/v1") is False
    assert llm_client._is_openrouter("http://127.0.0.1:8080/v1") is False


def test_complete_extracts_real_cost_for_an_openrouter_shaped_base_url() -> None:
    """SC-3: complete() sends the usage.include extra_body to an openrouter.ai base_url
    and returns the provider's real cost on CompleteResult.cost -- proven end to end
    (gate, extra_body, response parsing) without a live network call."""
    service = "openrouter-shaped-svc"
    _write_service(
        service, "https://openrouter.ai/api", default_model="openai/gpt-5.6-luna"
    )

    sent_requests: list[httpx2.Request] = []

    def _fake_send(
        _self: httpx2.Client, request: httpx2.Request, **_kw: object
    ) -> httpx2.Response:
        sent_requests.append(request)
        return _openrouter_response(request, cost=9.2e-06)

    with patch.object(httpx2.Client, "send", _fake_send):
        result = llm_client.complete(prompt="hi", service=service)

    assert len(sent_requests) == 1
    sent_body = json.loads(sent_requests[0].content)
    assert sent_body.get("usage") == {"include": True}, (
        "the usage.include extension must reach the real outgoing request body"
    )
    assert result.cost == 9.2e-06
    assert result.text == "ok"


def test_complete_does_not_send_extra_body_to_a_non_openrouter_base_url() -> None:
    """Control for the test above: an api.openai.com-shaped base_url gets no
    usage.include extension in the request at all -- not merely an ignored one."""
    service = "openai-shaped-svc"
    _write_service(service, "https://api.openai.com", default_model="gpt-4.1-mini")

    sent_requests: list[httpx2.Request] = []

    def _fake_send(
        _self: httpx2.Client, request: httpx2.Request, **_kw: object
    ) -> httpx2.Response:
        sent_requests.append(request)
        payload = {
            "id": "oa-completion",
            "object": "chat.completion",
            "model": "gpt-4.1-mini",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
        }
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=json.dumps(payload).encode(),
        )

    with patch.object(httpx2.Client, "send", _fake_send):
        result = llm_client.complete(prompt="hi", service=service)

    assert len(sent_requests) == 1
    sent_body = json.loads(sent_requests[0].content)
    assert "usage" not in sent_body
    assert result.cost is None


# ---------------------------------------------------------------------------
# SC-4: a 429 raises, circuit_breaker.is_quota_error recognizes it, and the
# service's breaker opens on the third failure.
# ---------------------------------------------------------------------------


def _rate_limited_handler() -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_POST(self) -> None:
            if not self.path.endswith("/chat/completions"):
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            body = json.dumps(
                {
                    "error": {
                        "message": "Rate limit exceeded",
                        "type": "rate_limit_error",
                    }
                }
            ).encode()
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            # Tells the openai SDK's own retry loop not to retry: this test drives
            # exactly three calls through summarize_with_analysis and needs each one
            # to reach the circuit breaker as a single failure, not be absorbed by the
            # SDK's built-in exponential-backoff retries (max_retries=3 in
            # llm_client._client_for) before it ever gets here.
            self.send_header("x-should-retry", "false")
            self.end_headers()
            self.wfile.write(body)

    return _Handler


def test_quota_error_raises_and_opens_circuit_after_third_call() -> None:
    """SC-4: three 429s each raise, is_quota_error recognizes them, breaker opens."""
    service = "quota-svc"
    raised: list[Exception] = []

    with _running(_rate_limited_handler()) as base_url:
        _write_service(service, base_url)
        summarizer = ContentSummarizer(service)

        for _ in range(3):
            with pytest.raises(openai.APIStatusError) as exc_info:
                summarizer.summarize_with_analysis(
                    content="An article long enough to summarize.", title="T"
                )
            raised.append(exc_info.value)

    assert len(raised) == 3
    breaker = get_circuit_breaker(service)
    for err in raised:
        assert breaker.is_quota_error(err), (
            f"is_quota_error must recognize {err!r} as a quota error"
        )
    assert breaker.get_status()["state"] == "open", (
        "breaker must be open after the third quota error"
    )


# ---------------------------------------------------------------------------
# SC-5: complete() works from inside a running event loop and from a plain thread.
# ---------------------------------------------------------------------------


def test_complete_works_inside_running_event_loop_and_plain_thread() -> None:
    """SC-5: neither call site raises "cannot be called from a running event loop"."""
    with _running(_content_handler("ok from stub")) as base_url:
        _write_service("event-loop-svc", base_url)

        async def _call_inside_loop() -> CompleteResult:
            # Mirrors api.py's async handlers calling the LLM synchronously from
            # inside a running loop (F-ASYNC) -- complete() is a plain sync call.
            return llm_client.complete(prompt="hi", service="event-loop-svc")

        result_from_loop = asyncio.run(_call_inside_loop())

        thread_results: list[CompleteResult] = []

        def _call_in_thread() -> None:
            thread_results.append(
                llm_client.complete(prompt="hi", service="event-loop-svc")
            )

        thread = threading.Thread(target=_call_in_thread)
        thread.start()
        thread.join(timeout=10)

    assert result_from_loop.text == "ok from stub"
    assert len(thread_results) == 1
    assert thread_results[0].text == "ok from stub"


# ---------------------------------------------------------------------------
# F-1-4: response_format actually reaches the outgoing request when json=True, and is
# absent when it's not -- the SDK-level mechanism behind all four of SC-6's call
# sites: the summarizer, evaluator, context_analyzer and deep_extractor modules. Same
# wire-level technique as the extra_body tests above: patch httpx2.Client.send and
# assert on the real serialized request body. Every _content_handler-backed test below
# reads and discards the request body without inspecting it, so on its own the SC-6
# suite would pass identically with a wrong TypedDict shape, a wrong field name, or an
# inverted `if json` condition -- these two tests are what actually proves the
# response_format branch, not just complete()'s parsing of a pre-programmed reply.
# ---------------------------------------------------------------------------


def test_complete_sends_response_format_json_object_when_json_true() -> None:
    """F-1-4: json=True reaches the real request as
    response_format={"type": "json_object"}."""
    service = "json-true-wire-svc"
    _write_service(service, "https://wire-check.invalid", default_model="stub-model")

    sent_requests: list[httpx2.Request] = []

    def _fake_send(
        _self: httpx2.Client, request: httpx2.Request, **_kw: object
    ) -> httpx2.Response:
        sent_requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=_completion_body("{}"),
        )

    with patch.object(httpx2.Client, "send", _fake_send):
        llm_client.complete(prompt="hi", service=service, json=True)

    assert len(sent_requests) == 1
    sent_body = json.loads(sent_requests[0].content)
    assert sent_body.get("response_format") == {"type": "json_object"}, (
        "json=True must reach the real outgoing request body as response_format"
    )


def test_complete_omits_response_format_when_json_false() -> None:
    """Control for the test above: the default json=False sends no response_format
    key at all -- not merely one the provider happens to ignore."""
    service = "json-false-wire-svc"
    _write_service(service, "https://wire-check.invalid", default_model="stub-model")

    sent_requests: list[httpx2.Request] = []

    def _fake_send(
        _self: httpx2.Client, request: httpx2.Request, **_kw: object
    ) -> httpx2.Response:
        sent_requests.append(request)
        return httpx2.Response(
            200,
            request=request,
            headers={"content-type": "application/json"},
            content=_completion_body("ok"),
        )

    with patch.object(httpx2.Client, "send", _fake_send):
        llm_client.complete(prompt="hi", service=service)

    assert len(sent_requests) == 1
    sent_body = json.loads(sent_requests[0].content)
    assert "response_format" not in sent_body


# ---------------------------------------------------------------------------
# SC-6: JSON extraction tolerates a ```json fence, and a no-JSON reply is logged at
# ERROR with its first 200 chars before each of the four former json=True sites
# returns None or raises.
# ---------------------------------------------------------------------------


def test_json_extraction_extract_json_strips_fence() -> None:
    fenced = 'Here is the analysis:\n```json\n{"a": 1, "b": [2, 3]}\n```\n'
    assert llm_client.extract_json(fenced) == {"a": 1, "b": [2, 3]}
    assert llm_client.extract_json('{"a": 1}') == {"a": 1}
    assert llm_client.extract_json("not json at all") is None


_SUMMARIZER_PAYLOAD = {
    "summary": "Brief summary.",
    "reading_summary": "Longer reading summary.",
    "alpha_insights": ["insight one"],
    "patterns": ["pattern one"],
    "entities": ["entity"],
    "quotes": [],
}


def test_json_extraction_summarizer_parses_fenced_reply() -> None:
    fenced = "Here you go:\n```json\n" + json.dumps(_SUMMARIZER_PAYLOAD) + "\n```\n"
    with _running(_content_handler(fenced)) as base_url:
        _write_service("json-summarizer-ok", base_url)
        summary = ContentSummarizer("json-summarizer-ok").summarize_with_analysis(
            content="Article body long enough to summarize.", title="T"
        )

    assert summary is not None
    assert summary.summary == "Brief summary."
    assert summary.alpha_insights == ["insight one"]


def test_json_extraction_summarizer_no_json_logs_and_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _running(_content_handler("Sorry, I can't help with that.")) as base_url:
        _write_service("json-summarizer-fail", base_url)
        with caplog.at_level(logging.ERROR):
            summary = ContentSummarizer("json-summarizer-fail").summarize_with_analysis(
                content="Article body long enough to summarize.", title="T"
            )

    assert summary is None
    assert "Sorry, I can't help with that" in caplog.text


def test_json_extraction_evaluator_parses_fenced_reply() -> None:
    payload: dict[str, object] = {
        "priority": None,
        "matched_interests": [],
        "reasoning": "stub",
    }
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    with _running(_content_handler(fenced)) as base_url:
        _write_service("json-evaluator-ok", base_url)
        evaluation = ContentEvaluator("json-evaluator-ok").evaluate_content(
            content="Article body.", title="T", url="", context="ctx"
        )

    assert evaluation.priority is None  # null priority round-trips through the fence
    assert evaluation.matched_interests == []


def test_json_extraction_evaluator_no_json_logs_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _running(_content_handler("This is not JSON, sorry.")) as base_url:
        _write_service("json-evaluator-fail", base_url)
        with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
            ContentEvaluator("json-evaluator-fail").evaluate_content(
                content="Article body.", title="T", url="", context="ctx"
            )

    assert "This is not JSON, sorry" in caplog.text


def test_json_extraction_context_analyzer_parses_fenced_reply() -> None:
    payload: dict[str, object] = {"suggested_topics": []}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    with _running(_content_handler(fenced)) as base_url:
        _write_service("json-context-ok", base_url)
        result = ContextAnalyzer("json-context-ok").analyze_flagged_items(
            flagged_items=[{"title": "T", "summary": "S", "source_name": "src"}],
            context_text="## High Priority Topics\n- x\n",
        )

    assert result == {"suggested_topics": []}


def test_json_extraction_context_analyzer_no_json_logs_and_raises(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _running(_content_handler("Nope, no JSON here.")) as base_url:
        _write_service("json-context-fail", base_url)
        with caplog.at_level(logging.ERROR), pytest.raises(ValueError):
            ContextAnalyzer("json-context-fail").analyze_flagged_items(
                flagged_items=[{"title": "T", "summary": "S", "source_name": "src"}],
                context_text="## High Priority Topics\n- x\n",
            )

    assert "Nope, no JSON here" in caplog.text


def test_json_extraction_deep_extractor_parses_fenced_reply() -> None:
    payload = {"synthesis": "Deep synthesis text.", "quotables": []}
    fenced = "```json\n" + json.dumps(payload) + "\n```"
    with _running(_content_handler(fenced)) as base_url:
        _write_service("json-deep-ok", base_url)
        result = ContentDeepExtractor("json-deep-ok").extract(
            content="Article body long enough.", title="T"
        )

    assert result is not None
    assert result["synthesis"] == "Deep synthesis text."


def test_json_extraction_deep_extractor_no_json_logs_and_returns_none(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with _running(_content_handler("No JSON in this reply.")) as base_url:
        _write_service("json-deep-fail", base_url)
        with caplog.at_level(logging.ERROR):
            result = ContentDeepExtractor("json-deep-fail").extract(
                content="Article body long enough.", title="T"
            )

    assert result is None
    assert "No JSON in this reply" in caplog.text


# ---------------------------------------------------------------------------
# SC-7: startup validation verifies the configured model, not just the endpoint.
# ---------------------------------------------------------------------------


def _models_handler(
    ids: list[str],
) -> type[http.server.BaseHTTPRequestHandler]:
    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            if not self.path.endswith("/models"):
                self.send_error(404)
                return
            payload = {
                "object": "list",
                "data": [{"id": i, "object": "model"} for i in ids],
            }
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _Handler


def test_health_check_missing_model_fails_naming_service_and_model() -> None:
    """SC-7: a reachable service whose configured model is absent fails, naming both."""
    service = "missing-model-svc"
    with _running(_models_handler(["some-other-model"])) as base_url:
        _write_service(service, base_url, default_model="the-configured-model")

        with pytest.raises(llm_client.ConfigError) as exc_info:
            llm_client.health_check(service=service)

    message = str(exc_info.value)
    assert service in message
    assert "the-configured-model" in message


def test_health_check_passes_when_model_is_listed() -> None:
    """Control for the test above: health_check() is silent when the model exists."""
    service = "present-model-svc"
    with _running(_models_handler(["present-model", "another-model"])) as base_url:
        _write_service(service, base_url, default_model="present-model")

        llm_client.health_check(service=service)  # must not raise
