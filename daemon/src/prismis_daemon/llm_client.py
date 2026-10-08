"""Direct openai-SDK LLM client: complete(), health_check(), config.toml service resolution.

All six prismis services speak one wire format -- OpenAI's chat-completions API, whether
the host is api.openai.com or openrouter.ai -- so this module owns the whole LLM surface:
a [services.<name>] resolver over prismis's own config.toml, an environment-variable key
read, complete(), health_check() and a JSON extractor. No other prismis module talks to
the openai SDK or resolves a service.

A service's `api_key` is always `env:NAME`: the key is read from the process environment
at call time and never lives in a config file. A service with no `api_key` is keyless
(a local model server).
"""

from __future__ import annotations

import json as jsonlib
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import Omit, OpenAI, omit
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.shared_params.response_format_json_object import (
    ResponseFormatJSONObject,
)

from .config import ServiceConfig, load_services

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised for service configuration problems: an unknown service, a missing key variable."""


@dataclass
class TokenUsage:
    """Token counts for a completion."""

    input: int
    output: int


@dataclass
class CompleteResult:
    """Result of a completion -- the shape every call site already reads.

    Field names are the original client's (text, model, provider, tokens.input/output,
    finish_reason, duration_ms, cost); callers were built against that shape and this
    migration does not rename it.
    """

    text: str
    model: str
    provider: str
    tokens: TokenUsage
    finish_reason: str  # "stop" | "max_tokens"
    duration_ms: int
    cost: float | None


_ENV_PREFIX = "env:"


def resolve_service(name: str, config_path: Path | None = None) -> ServiceConfig:
    """Resolve a named service from the [services.*] tables of config.toml.

    Raises ConfigError if config.toml is missing/unparseable or the name is not
    defined there. `config_path` defaults to the XDG prismis config.toml.
    """
    try:
        services = load_services(config_path)
    except (OSError, ValueError) as e:
        raise ConfigError(f"Failed to load services from config.toml: {e}") from e
    if name not in services:
        available = ", ".join(services)
        raise ConfigError(
            f'Unknown service: "{name}". Define [services.{name}] in config.toml. '
            f"Defined: [{available}]"
        )
    return services[name]


def load_api_key(service: ServiceConfig) -> str | None:
    """Read the API key for a service from the environment.

    Returns None for a keyless service (no `api_key`, e.g. a local model server).
    Public (not the chat-completions path's private detail): kind_classifier.py
    resolves the same way for its own, differently-shaped provider call, and this is
    the one key-loading mechanism prismis has.

    Raises ConfigError naming the service and the variable when the variable is unset
    or empty, and when `api_key` is anything but an `env:NAME` reference -- a literal
    provider key must never sit in a config file.
    """
    ref = service.api_key
    if ref is None:
        return None
    if not ref.startswith(_ENV_PREFIX) or not ref[len(_ENV_PREFIX) :]:
        raise ConfigError(
            f'Service "{service.name}": api_key must be an env:NAME reference '
            f"(e.g. api_key = \"env:OPENROUTER_API_KEY\"); a key value does not belong "
            f"in config.toml."
        )
    var = ref[len(_ENV_PREFIX) :]
    value = os.environ.get(var)
    if not value:
        raise ConfigError(
            f'Service "{service.name}" needs the environment variable {var}, which is '
            f"not set. Export it or add {var}=... to ~/.config/prismis/.env"
        )
    return value


def _client_for(service: ServiceConfig, api_key: str | None) -> OpenAI:
    """Build a sync OpenAI client for the resolved service.

    Sync, not AsyncOpenAI: api.py's FastAPI handlers call complete() synchronously from
    inside a running event loop, and the orchestrator runs it from APScheduler's
    threadpool. A sync httpx-based client needs no bridge for either caller (F-ASYNC).
    max_retries=3 plus a request timeout stands in for a 3-attempt, 1/2/4s retry.
    """
    return OpenAI(base_url=service.base_url, api_key=api_key or "not-required", max_retries=3, timeout=60.0)


def _is_openrouter(base_url: str) -> bool:
    return "openrouter.ai" in base_url


_JSON_FENCE = re.compile(r"```(?:json)?\s*\n?([\s\S]*?)\n?```")


def extract_json(text: str) -> dict[str, Any] | None:
    """Parse JSON from a completion, tolerating a ```json fenced reply.

    Strips a markdown code fence if present, then parses. Returns None (never raises)
    on unparseable input -- callers decide whether a bad reply is fatal. Values stay
    Any, same as json.loads() itself: callers already validate their own required
    fields (summarizer.py, evaluator.py, ...) rather than trusting a schema here.
    """
    clean = text.strip()
    match = _JSON_FENCE.search(clean)
    if match:
        clean = match.group(1).strip()
    try:
        parsed = jsonlib.loads(clean)
    except jsonlib.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def complete(
    prompt: str,
    *,
    service: str,
    system_prompt: str | None = None,
    model: str | None = None,
    max_tokens: int | None = None,
    json: bool = False,
) -> CompleteResult:
    """Execute a single-turn completion against the resolved service.

    Raises the openai SDK's typed exceptions (RateLimitError, APIStatusError, ...) on
    provider failure. Never catches and returns empty text -- a swallowed failure here
    would look like real output everywhere downstream.
    """
    # D-TEMP (docs/work/wo-openai-sdk-migration.md): no sampling-warmth kwarg here or on
    # any call site, ever. A controlled probe found it changes nothing on the
    # reasoning-class models prismis actually runs (openai/gpt-5.6-luna: identical
    # output at the low and high end of the range), and qwen/qwen3.7-flash rejects the
    # parameter outright with HTTP 400 on every call. Dropped, not ported -- do not
    # reintroduce it.
    start = time.monotonic()

    svc = resolve_service(service)
    api_key = load_api_key(svc)
    resolved_model = model or svc.model
    if not resolved_model:
        raise ValueError(
            "Model name required: pass model= in complete() or set model in [services.<name>] of config.toml"
        )

    messages: list[ChatCompletionMessageParam] = []
    if system_prompt is not None:
        messages.append(
            ChatCompletionSystemMessageParam(role="system", content=system_prompt)
        )
    messages.append(ChatCompletionUserMessageParam(role="user", content=prompt))

    response_format: ResponseFormatJSONObject | Omit = (
        ResponseFormatJSONObject(type="json_object") if json else omit
    )

    extra_headers: dict[str, str] = {}
    if svc.app_title is not None:
        extra_headers["X-OpenRouter-Title"] = svc.app_title
    if svc.app_url is not None:
        extra_headers["HTTP-Referer"] = svc.app_url

    # OpenRouter's `usage.include` extension returns the real billed cost on the
    # response (SC-3; proven without live network by
    # test_complete_extracts_real_cost_for_an_openrouter_shaped_base_url,
    # tests/unit/test_llm_client_unit.py). api.openai.com rejects the same extension as
    # an unrecognized request argument (BadRequestError) -- proven against the real
    # endpoint, gated on real credentials, by
    # test_openai_service_rejects_extra_body_usage_include,
    # tests/integration/test_llm_client_live_integration.py -- so it is sent only to
    # openrouter.ai. Cost for api.openai.com services stays None: a static
    # pricing table is exactly the broken mechanism the old client had, and
    # reintroducing a local estimate for one host only would resurrect the same
    # staleness problem for half the fleet.
    extra_body: dict[str, object] | None = (
        {"usage": {"include": True}} if _is_openrouter(svc.base_url) else None
    )

    client = _client_for(svc, api_key)
    response = client.chat.completions.create(
        model=resolved_model,
        messages=messages,
        max_tokens=max_tokens if max_tokens is not None else omit,
        response_format=response_format,
        extra_headers=extra_headers or None,
        extra_body=extra_body,
    )

    choice = response.choices[0]
    text = choice.message.content or ""
    finish_reason = "max_tokens" if choice.finish_reason == "length" else "stop"

    usage = response.usage
    tokens = TokenUsage(
        input=usage.prompt_tokens if usage is not None else 0,
        output=usage.completion_tokens if usage is not None else 0,
    )
    cost = getattr(usage, "cost", None) if usage is not None else None

    duration_ms = int((time.monotonic() - start) * 1000)

    return CompleteResult(
        text=text,
        model=response.model,
        provider=svc.adapter,
        tokens=tokens,
        finish_reason=finish_reason,
        duration_ms=duration_ms,
        cost=cost,
    )


def health_check(service: str) -> None:
    """Verify the service is reachable AND its configured model actually exists.

    A bare endpoint ping only proves the endpoint answered; listing models and
    checking the service's model against the result also proves the model prismis will
    actually call exists, which is strictly more useful (SC-7).

    Raises ConfigError naming the service and model if the model is absent from the
    provider's list. Raises the openai SDK's own typed exception if the endpoint is
    unreachable or auth fails.
    """
    svc = resolve_service(service)
    api_key = load_api_key(svc)
    client = _client_for(svc, api_key)

    models = client.models.list()
    ids = {m.id for m in models}

    if svc.model is not None and svc.model not in ids:
        raise ConfigError(
            f'Service "{service}" configured model "{svc.model}" was not found '
            f"in the provider's model list."
        )
