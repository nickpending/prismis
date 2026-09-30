"""Kind classification via Jev, OpenRouter's decisions model (gh #77).

Priority says how much an item matters and topics say what it's about; kind says what
sort of thing it is. Ten kinds, derived from 100 hand-labelled cerebro items (gh #77
comment) and picked by Jev (typesafe/jev-1.13) through OpenRouter's alpha Decisions
endpoint -- a typed pick-one-of-N call, not a chat completion, so this module talks to
that endpoint directly (submit_decision) instead of through llm_client.complete(),
which only speaks the chat-completions shape. 89% accurate on the 72% of items it was
confident (>= CONFIDENCE_THRESHOLD) about; below the threshold, or a choice that isn't
one of the ten kinds, comes back unclassified rather than forced into a wrong bucket.

submit_decision is the one provider boundary here (Principle I: the provider is the
only thing a test may stand in for). It raises on anything that means the call itself
failed -- unreachable endpoint, non-2xx status, a body that isn't even JSON -- exactly
like llm_client.complete() raises on provider failure, so callers can tell "the call
failed" (INV-002: store the item anyway, record the failure) from "the call succeeded
but didn't say enough" (fail closed to unclassified, still a normal return not a raise).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .circuit_breaker import get_circuit_breaker
from .llm_client import load_api_key, resolve_service
from .observability import log as obs_log

logger = logging.getLogger(__name__)

# The ten kinds and their definitions, declared once (SC-1). Fixed by the measured
# evidence on gh #77 -- changing the list or the threshold below needs a new
# measurement, not a code edit.
KINDS: dict[str, str] = {
    "release": "An announcement of a new product, feature, version, or project launch.",
    "experience": (
        "A first-person account of trying, building, or living with something."
    ),
    "question": "A question or discussion thread seeking input or help.",
    "analysis": "An opinion, argument, or analytical take on a topic.",
    "news": "A report of an event happening in the world, without analysis or opinion.",
    "incident": (
        "A write-up of an outage, breach, or operational incident and its handling."
    ),
    "research": "A research paper, study, or scientific finding.",
    "vulnerability": "A disclosed security vulnerability or exploit.",
    "humor": "A joke, meme, or satirical piece.",
    "tutorial": "A how-to guide or step-by-step instructional piece.",
}

# Confidence at or above this counts as classified (measured on gh #77; not a tunable
# knob -- moving it needs new evidence, per the work order's stakes).
CONFIDENCE_THRESHOLD = 0.7

# SC-2: bounds on what goes into the request regardless of the source content's
# length, so the request is about the same size for a 3-line RSS blurb and a
# 700k-character page.
_READING_SUMMARY_CAP = 4000
_RAW_CONTENT_CAP = 1000

_QUESTION_NAME = "kind"
_TIMEOUT_SECONDS = 60.0


@dataclass
class KindResult:
    """The classifier's verdict: a kind name plus its confidence, or unclassified."""

    kind: str | None
    confidence: float | None


@dataclass
class DecisionCall:
    """One decisions-endpoint response, trimmed to what classify() needs."""

    answers: dict[str, Any]
    model: str
    cost: float | None
    duration_ms: int


def _build_state(
    *,
    title: str,
    source_type: str,
    source_name: str,
    summary: str,
    reading_summary: str,
    raw_content: str,
) -> dict[str, str]:
    """Build the bounded state sent to the decisions endpoint (SC-2).

    Bounded regardless of raw_content's length -- from empty to several hundred
    thousand characters -- so the request stays about the same size for any source:
    the reading summary is capped at 4000 characters and only the first 1000
    characters of raw content are sent. Title, source type, source name and the light
    summary pass through whole.
    """
    return {
        "title": title,
        "source_type": source_type,
        "source_name": source_name,
        "summary": summary,
        "reading_summary": reading_summary[:_READING_SUMMARY_CAP],
        "raw_content": raw_content[:_RAW_CONTENT_CAP],
    }


def submit_decision(
    state: dict[str, str], *, service: str, model: str | None = None
) -> DecisionCall:
    """Submit a Decisions request to OpenRouter's alpha decisions endpoint.

    Raises on anything that means the call itself failed -- unresolvable service,
    missing key, unreachable endpoint, non-2xx status, a body that isn't even JSON.
    A response that parses as JSON but doesn't carry the shape classify() expects is
    not this function's problem: it is handed back as-is and classify() fails closed.
    """
    svc = resolve_service(service)
    api_key = load_api_key(svc)
    resolved_model = model or svc.default_model
    if not resolved_model:
        raise ValueError(
            "Model name required: pass model= or set default_model in services.toml"
        )

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if svc.app_title is not None:
        headers["X-OpenRouter-Title"] = svc.app_title
    if svc.app_url is not None:
        headers["HTTP-Referer"] = svc.app_url

    payload = {
        "model": resolved_model,
        "state": state,
        "questions": {
            _QUESTION_NAME: {
                "type": "choice",
                "instructions": (
                    "Pick the single kind of information this item primarily is."
                ),
                "criteria": KINDS,
            }
        },
    }

    start = time.monotonic()
    response = httpx.post(
        svc.base_url, json=payload, headers=headers, timeout=_TIMEOUT_SECONDS
    )
    response.raise_for_status()
    body = response.json()
    duration_ms = int((time.monotonic() - start) * 1000)

    if not isinstance(body, dict):
        raise ValueError(f"Decisions endpoint returned a non-object body: {body!r}")

    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None

    return DecisionCall(
        answers=body.get("answers") or {},
        model=body.get("model") or resolved_model,
        cost=cost,
        duration_ms=duration_ms,
    )


# SC-1: a small, fixed state for the health-check probe -- not real content, just
# enough shape to run the same request classify() would build.
_HEALTH_CHECK_STATE = _build_state(
    title="Health check",
    source_type="rss",
    source_name="prismis-health-check",
    summary="A routine reachability probe, not real content.",
    reading_summary="",
    raw_content="",
)


def health_check(service: str) -> None:
    """Verify the kind service is reachable and its decisions model actually classifies (SC-1).

    llm_client.health_check cannot serve this service: it lists models, OpenRouter's
    model list does not include typesafe/jev-1.13, and the decisions endpoint only
    answers a POST (a GET returns 404). One classify call, made through the same
    submit_decision() the real classify() call uses -- not a second request builder
    -- is the only check that exercises the configured base_url, key and model
    together.

    Raises on anything submit_decision raises (unresolvable service, missing key,
    unreachable endpoint, non-2xx status, a body that isn't even JSON) and also
    raises when the call succeeds but the answer doesn't name one of the ten kinds --
    a health check that "succeeds" on an answer naming no kind proves nothing.
    """
    try:
        result = submit_decision(_HEALTH_CHECK_STATE, service=service)
    except Exception as e:
        obs_log(
            "llm.call",
            action="classify_kind",
            model=service,
            status="error",
            error=str(e),
        )
        raise

    answer = result.answers.get(_QUESTION_NAME)
    choice = answer.get("choice") if isinstance(answer, dict) else None
    named_a_kind = choice in KINDS

    obs_log(
        "llm.call",
        action="classify_kind",
        model=result.model,
        cost_usd=result.cost,
        duration_ms=result.duration_ms,
        status="success" if named_a_kind else "error",
    )

    if not named_a_kind:
        raise ValueError(
            f"Kind service '{service}' answered with a choice that names no kind: "
            f"{choice!r}"
        )


def _parse_kind(answers: dict[str, Any]) -> KindResult:
    """Fail-closed shape validation (SC-1): a response that can't be trusted to name
    one of the ten kinds at sufficient confidence comes back unclassified -- never
    raised, since the call itself already succeeded by the time this runs."""
    answer = answers.get(_QUESTION_NAME)
    if not isinstance(answer, dict):
        return KindResult(kind=None, confidence=None)

    choice = answer.get("choice")
    confidence = answer.get("confidence")
    if not isinstance(confidence, int | float) or isinstance(confidence, bool):
        confidence = None

    if choice not in KINDS or confidence is None or confidence < CONFIDENCE_THRESHOLD:
        return KindResult(kind=None, confidence=confidence)

    return KindResult(kind=choice, confidence=float(confidence))


class KindClassifier:
    """Classifies an item's primary kind via Jev, OpenRouter's decisions model."""

    def __init__(self, service_name: str) -> None:
        """Initialize the classifier with the decisions-endpoint LLM service.

        Args:
            service_name: Service name from ~/.config/llm-core/services.toml
        """
        self.service_name = service_name

        logger.info(f"KindClassifier initialized with service: {self.service_name}")

    def classify(
        self,
        *,
        title: str = "",
        source_type: str = "",
        source_name: str = "",
        summary: str = "",
        reading_summary: str = "",
        raw_content: str = "",
    ) -> KindResult:
        """Classify one item's primary kind.

        Returns KindResult(kind=None, ...) -- unclassified -- when the endpoint's
        answer is missing, malformed, below CONFIDENCE_THRESHOLD, or not one of the
        ten declared kinds.

        Raises on a failed call (circuit open, network, auth, a non-JSON body): the
        caller (the orchestrator, INV-002) is responsible for storing the item
        without a kind and recording the failure rather than losing the item.
        """
        state = _build_state(
            title=title,
            source_type=source_type,
            source_name=source_name,
            summary=summary,
            reading_summary=reading_summary,
            raw_content=raw_content,
        )

        # Check circuit breaker before the call, like the other LLM-backed steps.
        circuit = get_circuit_breaker(self.service_name)
        if not circuit.check_can_proceed():
            status = circuit.get_status()
            obs_log(
                "llm.call",
                action="classify_kind",
                model=self.service_name,
                status="circuit_open",
            )
            raise RuntimeError(
                f"LLM circuit breaker is open (quota exhausted). "
                f"Recovery in {status.get('recovery_in_seconds', 'unknown')}s"
            )

        try:
            result = submit_decision(state, service=self.service_name)

            obs_log(
                "llm.call",
                action="classify_kind",
                model=result.model,
                cost_usd=result.cost,
                duration_ms=result.duration_ms,
                status="success",
            )

            circuit.record_success()

        except Exception as e:
            circuit.record_failure(e)

            obs_log(
                "llm.call",
                action="classify_kind",
                model=self.service_name,
                status="error",
                error=str(e),
            )
            raise

        return _parse_kind(result.answers)
