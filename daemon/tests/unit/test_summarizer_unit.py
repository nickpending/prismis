"""Unit tests for ContentSummarizer logic functions."""

import dataclasses
import http.server
import json
import os
import threading
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch  # claudex-guard: allow-mock

import pytest
from conftest import LOCAL_LIGHT_SERVICE, configure_local_services

from prismis_daemon.circuit_breaker import (
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
)
from prismis_daemon.summarizer import ContentSummarizer, ContentSummary

# dedup (cluster 11): summarizer.py no longer imports complete directly -- the call
# moved into the shared call_llm_with_circuit_breaker helper in llm_call.py.
_LLM_COMPLETE_MOCK = "prismis_daemon.llm_call.complete"  # claudex-guard: allow-mock

# ContentSummarizer takes a llm-core service name (summarizer.py:39-46); model choice and
# credentials are resolved by llm-core from services.toml, not from a config dict here.
SERVICE = "prismis-openai"


def test_summarizer_initialization_with_service_name() -> None:
    """Test ContentSummarizer records the service it was constructed with."""
    summarizer = ContentSummarizer(SERVICE)

    assert summarizer.service_name == SERVICE


def test_build_prompt_includes_all_fields() -> None:
    """Test prompt building includes title, url, source type, and content."""
    summarizer = ContentSummarizer(SERVICE)

    content = "This is test content about AI."
    title = "Test Article"
    url = "https://example.com/article"
    source_type = "rss"

    prompt = summarizer._build_prompt(content, title, url, source_type, "", {})

    # Verify all fields are included
    assert "Title: Test Article" in prompt
    assert "Source Type: rss" in prompt
    assert "URL: https://example.com/article" in prompt
    assert "This is test content about AI." in prompt
    assert "CONTENT:" in prompt


def test_build_prompt_includes_source_name_and_metadata() -> None:
    """Test prompt building surfaces source name and metadata to the LLM.

    The prompt tells the LLM not to infer metadata, so anything it is allowed to use has
    to be passed through explicitly (summarizer.py:527-537).
    """
    summarizer = ContentSummarizer(SERVICE)

    prompt = summarizer._build_prompt(
        "body",
        "Title",
        "https://example.com",
        "reddit",
        "r/rust",
        {"author": "someone", "subreddit": "rust", "view_count": 1234},
    )

    assert "Source Name: r/rust" in prompt
    assert "Author: someone" in prompt
    assert "Subreddit: r/rust" in prompt
    assert "View Count: 1,234" in prompt


def test_build_prompt_handles_empty_fields() -> None:
    """Test prompt building handles empty optional fields gracefully."""
    summarizer = ContentSummarizer(SERVICE)

    content = "Minimal content"

    prompt = summarizer._build_prompt(content, "", "", "", "", {})

    # Should still have structure
    assert "Title: " in prompt
    assert "Source Type: " in prompt
    assert "URL: " in prompt
    assert "Minimal content" in prompt


def test_system_prompt_contains_required_instructions() -> None:
    """Test system prompt contains all required analysis instructions."""
    summarizer = ContentSummarizer(SERVICE)

    system_prompt = summarizer._get_system_prompt()

    # Verify key instructions present
    assert "400 chars max" in system_prompt  # Summary limit
    assert "reading_summary" in system_prompt  # Reading summary field
    assert "alpha_insights" in system_prompt  # Alpha insights
    assert "patterns" in system_prompt  # Patterns field
    assert "JSON" in system_prompt  # JSON format requirement
    assert "markdown" in system_prompt.lower()  # Markdown formatting
    assert "10-15%" in system_prompt  # Reading summary length guidance


# --- SC-1: entities/hashtag-style tags are gone from both prompts, from
# ContentSummary, and from what a parsed response requires. ---


def test_standard_system_prompt_has_no_entity_tags() -> None:
    """Test the standard/brief/detailed system prompt no longer asks for tags."""
    summarizer = ContentSummarizer(SERVICE)

    system_prompt = summarizer._get_system_prompt()

    assert "entities" not in system_prompt.lower()
    assert "hashtag" not in system_prompt.lower()


def test_diff_system_prompt_has_no_entity_tags() -> None:
    """Test the diff/changelog system prompt no longer asks for tags."""
    summarizer = ContentSummarizer(SERVICE)

    diff_prompt = summarizer._get_diff_system_prompt()

    assert "entities" not in diff_prompt.lower()


def test_content_summary_has_no_entity_tags_field() -> None:
    """Test ContentSummary no longer carries an entities field."""
    field_names = {f.name for f in dataclasses.fields(ContentSummary)}

    assert "entities" not in field_names


def test_summarize_with_analysis_parses_response_with_no_entity_tags_key() -> None:
    """Test a response with no entities key still parses into a complete ContentSummary.

    entities is not (and must not become) a required response field.
    """
    reset_circuit_breaker()
    summarizer = ContentSummarizer(SERVICE)

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = json.dumps(
        {
            "summary": "Test summary",
            "reading_summary": "# Test\n\nContent",
            "alpha_insights": ["insight"],
            "patterns": ["pattern"],
            "quotes": [],
            "tools": [],
            "urls": [],
        }
    )
    fake_result.tokens.input = 100
    fake_result.tokens.output = 50
    fake_result.cost = 0.001
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 500

    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            mock_complete.return_value = fake_result

            result = summarizer.summarize_with_analysis(
                content="Test article content about AI",
                title="AI Test",
                url="https://example.com",
                source_type="rss",
            )
    finally:
        reset_circuit_breaker()

    assert result is not None
    assert result.summary == "Test summary"
    assert result.alpha_insights == ["insight"]
    assert result.patterns == ["pattern"]
    assert result.quotes == []
    assert not hasattr(result, "entities")


# --- cluster 11 (dedup-triage.md): the LLM-call mechanics, including the
# circuit-breaker gating, now live in call_llm_with_circuit_breaker
# (prismis_daemon/llm_call.py). summarizer.py already gated on the circuit breaker
# before the extraction, but nothing here proved it -- these tests prove the
# consolidated caller still refuses, and still records success/failure, exactly as
# it did before the move. ---


def test_circuit_open_refuses_summarize_without_hitting_the_llm() -> None:
    """A summarize call is refused before complete() is ever invoked, once the
    service's circuit breaker is open."""
    reset_circuit_breaker()
    circuit = get_circuit_breaker(SERVICE)
    for _ in range(3):
        circuit.record_failure(RuntimeError("insufficient_quota"))
    assert circuit.check_can_proceed() is False, "setup: circuit must be open"

    summarizer = ContentSummarizer(SERVICE)
    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            with pytest.raises(RuntimeError, match="circuit breaker is open"):
                summarizer.summarize_with_analysis(content="body text", title="t")
            mock_complete.assert_not_called()
    finally:
        reset_circuit_breaker()


def test_summarize_records_a_quota_failure_on_the_circuit_breaker() -> None:
    """Three quota-shaped failures from summarize_with_analysis open the circuit --
    the helper's record_failure call, not just a log line."""
    reset_circuit_breaker()
    summarizer = ContentSummarizer(SERVICE)
    try:
        with patch(
            _LLM_COMPLETE_MOCK, side_effect=RuntimeError("insufficient_quota")
        ):  # claudex-guard: allow-mock
            for _ in range(3):
                with pytest.raises(RuntimeError):
                    summarizer.summarize_with_analysis(content="body text", title="t")
        assert get_circuit_breaker(SERVICE).check_can_proceed() is False
    finally:
        reset_circuit_breaker()


def test_summarize_records_success_and_closes_a_half_open_circuit() -> None:
    """A successful summarize call closes a half-open circuit via record_success."""
    reset_circuit_breaker()
    circuit = get_circuit_breaker(SERVICE)
    circuit.state = CircuitState.HALF_OPEN
    summarizer = ContentSummarizer(SERVICE)

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = json.dumps(
        {
            "summary": "s",
            "reading_summary": "r",
            "alpha_insights": [],
            "patterns": [],
            "quotes": [],
            "tools": [],
            "urls": [],
        }
    )
    fake_result.tokens.input = 10
    fake_result.tokens.output = 5
    fake_result.cost = 0.0
    fake_result.model = "gpt-4.1-mini"
    fake_result.duration_ms = 1

    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            mock_complete.return_value = fake_result
            result = summarizer.summarize_with_analysis(content="body text", title="t")
        assert result is not None
        assert circuit.state == CircuitState.CLOSED
    finally:
        reset_circuit_breaker()


def test_summarize_with_analysis_drops_tools_the_content_never_names() -> None:
    """gh #78: a tool the content never mentions is invented, so it is dropped; a
    tool the content names survives even when case and punctuation differ."""
    reset_circuit_breaker()
    summarizer = ContentSummarizer(SERVICE)

    fake_result = MagicMock()  # claudex-guard: allow-mock
    fake_result.text = json.dumps(
        {
            "summary": "s",
            "reading_summary": "# r",
            "alpha_insights": [],
            "patterns": [],
            "quotes": [],
            "tools": ["kotlin-compose", "ripgrep", "invented-tool"],
            "urls": [],
        }
    )
    fake_result.tokens.input = 1
    fake_result.tokens.output = 1
    fake_result.cost = 0.0
    fake_result.model = "m"
    fake_result.duration_ms = 1

    try:
        with patch(_LLM_COMPLETE_MOCK) as mock_complete:  # claudex-guard: allow-mock
            mock_complete.return_value = fake_result
            result = summarizer.summarize_with_analysis(
                content="We moved the UI to Kotlin Compose and search with ripgrep.",
                title="t",
                url="https://example.com",
                source_type="rss",
            )
    finally:
        reset_circuit_breaker()

    assert result is not None
    assert result.tools == ["kotlin-compose", "ripgrep"]


# --- title-only-reasons SC-4: the `substantive` verdict ---


def test_every_summarize_prompt_asks_for_the_substantive_field() -> None:
    """The standard, brief, detailed and diff prompts all ask for `substantive`.
    BREAKS: a mode whose prompt never asks returns no verdict, so items summarized
    in that mode could never be judged not substantive."""
    summarizer = ContentSummarizer(SERVICE)

    prompts = {
        "standard": summarizer._get_system_prompt(),
        "brief": summarizer._get_brief_system_prompt(),
        "detailed": summarizer._get_detailed_system_prompt(),
        "diff": summarizer._get_diff_system_prompt(),
    }

    for mode, prompt in prompts.items():
        assert "substantive" in prompt, f"{mode} prompt does not ask for substantive"


class _ReplyStub:
    """An OpenAI-shaped completion endpoint at the real HTTP boundary whose reply
    JSON is whatever `reply` holds when the request arrives."""

    def __init__(self) -> None:
        self.reply: dict = {}
        outer = self

        class _Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_args: object) -> None:
                return

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
                payload = {
                    "id": "stub",
                    "object": "chat.completion",
                    "model": "stub-model",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(outer.reply),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
                body = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        host, port = self._server.server_address[0], self._server.server_address[1]
        assert isinstance(host, str)
        self.base_url = f"http://{host}:{port}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2.0)


@pytest.fixture
def reply_stub() -> Iterator[_ReplyStub]:
    stub = _ReplyStub()
    try:
        yield stub
    finally:
        stub.shutdown()
        reset_circuit_breaker()


_REPLY_BASE = {
    "summary": "s",
    "reading_summary": "# r",
    "alpha_insights": [],
    "patterns": [],
    "quotes": [],
    "tools": [],
    "urls": [],
}


@pytest.mark.parametrize(
    ("reply_extra", "expected"),
    [
        ({"substantive": True}, True),
        ({"substantive": False}, False),
        ({}, None),
        ({"substantive": "false"}, None),
        ({"substantive": None}, None),
    ],
    ids=["true", "false", "absent", "non-boolean-string", "null"],
)
def test_summarize_with_analysis_parses_the_substantive_verdict(
    isolated_xdg_env: Path,
    reply_stub: _ReplyStub,
    reply_extra: dict,
    expected: bool | None,
) -> None:
    """
    SC-4: a reply with `substantive` true, false or absent parses to True, False and
    None; a value that is not a boolean is unknown too, never false.
    BREAKS: reading an absent field as false marks every reply from a model that
    skips the field not substantive; requiring it fails the whole parse.
    """
    reply_stub.reply = {**_REPLY_BASE, **reply_extra}
    configure_local_services(
        Path(os.environ["XDG_CONFIG_HOME"]), reply_stub.base_url
    )
    summarizer = ContentSummarizer(LOCAL_LIGHT_SERVICE)

    result = summarizer.summarize_with_analysis(
        content="A paragraph of content for the model to judge.",
        title="t",
        url="https://example.com",
        source_type="rss",
    )

    assert result is not None
    assert result.substantive is expected
