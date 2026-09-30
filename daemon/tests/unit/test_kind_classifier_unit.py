"""Unit tests for kind_classifier -- content-kind work order, job 1 (SC-1, SC-2).

Invariants protected:
- SC-1: classify() gates on CONFIDENCE_THRESHOLD (0.7): a choice at or above the
  threshold comes back classified, below the threshold or not one of the ten declared
  kinds comes back unclassified. The ten kinds and their definitions live once, in
  KINDS.
- SC-1 (kind-health, gh #82): health_check() proves the configured base_url, key and
  model together through the same submit_decision() classify() uses, and rejects an
  answer that doesn't name one of the ten kinds even though the call itself succeeded.
- SC-2: _build_state() bounds the request regardless of raw_content's length: title,
  source type, source name and the light summary pass through whole; reading_summary
  is capped at 4000 chars and raw_content at 1000, for content from empty to ~300k
  chars.

Only submit_decision (the decisions-endpoint boundary) is stood in for, per Principle
I; _build_state and classify()'s own circuit breaker / parsing logic run for real,
unmocked.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.kind_classifier import (
    CONFIDENCE_THRESHOLD,
    KINDS,
    KindClassifier,
    _build_state,
    health_check,
)
from prismis_daemon.observability import get_logger, reset_logger, set_run_id
from prismis_daemon.verify_chain import read_run_events

# Patch target -- the decisions-endpoint provider boundary itself, which the
# constitution permits standing in for (Principle I).
_PATCH_SUBMIT = "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


@pytest.fixture(autouse=True)
def fresh_observability() -> Iterator[None]:
    """Bind the observability logger to this test's sealed XDG_DATA_HOME (F-1-1).

    Mirrors test_kind_pipeline_integration.py's fixture of the same name: the logger
    caches its base directory at first use, and isolated_xdg_env (tests/conftest.py)
    points XDG_DATA_HOME at a fresh directory per test, so a cached instance from an
    earlier test would write into a directory this test never reads back from.
    """
    reset_logger()
    yield
    set_run_id(None)
    reset_logger()


def _events_for(run_id: str) -> list[dict]:
    """Read back every llm.call event this run_id logged, from the real JSONL file."""
    return read_run_events(get_logger().base_dir, run_id)


def _classify_kind_events(run_id: str) -> list[dict]:
    return [
        e
        for e in _events_for(run_id)
        if e.get("event") == "llm.call" and e.get("action") == "classify_kind"
    ]


# ---------------------------------------------------------------------------
# Lightweight fake objects -- no MagicMock, no external deps. Provider result
# fakes are the constitution's other permitted stand-in.
# ---------------------------------------------------------------------------


class _FakeDecisionCall:
    """Minimal stand-in for kind_classifier.DecisionCall."""

    def __init__(self, answers: dict, model: str = "typesafe/jev-1.13-test") -> None:
        self.answers = answers
        self.model = model
        self.cost = 0.00007
        self.duration_ms = 42


def _kind_answer(choice: object, confidence: object) -> dict:
    """Build a decisions-response answers dict for the single "kind" question."""
    answer: dict = {"type": "choice"}
    if choice is not None:
        answer["choice"] = choice
    if confidence is not None:
        answer["confidence"] = confidence
    return {"kind": answer}


# ---------------------------------------------------------------------------
# SC-1: the ten kinds, declared once
# ---------------------------------------------------------------------------


def test_ten_kinds_are_declared_once_with_a_definition_each() -> None:
    """
    SC-1: the ten kinds and their definitions are declared once, in the classifier
    module, and are exactly the ten named by the work order.
    BREAKS: A second, drifting copy of the kind list appears somewhere else (the API
    layer, the CLI, the TUI) instead of importing this one.
    """
    assert set(KINDS) == {
        "release",
        "experience",
        "question",
        "analysis",
        "news",
        "incident",
        "research",
        "vulnerability",
        "humor",
        "tutorial",
    }
    assert all(
        isinstance(definition, str) and definition.strip()
        for definition in KINDS.values()
    ), "every kind must carry a non-empty definition"


# ---------------------------------------------------------------------------
# SC-1: confidence threshold gating
# ---------------------------------------------------------------------------


def test_classify_returns_the_kind_at_the_confidence_threshold() -> None:
    """
    SC-1: confidence exactly at CONFIDENCE_THRESHOLD (0.7) counts as classified.
    BREAKS: An off-by-one boundary (e.g. strict >) silently unclassifies items the
    measured threshold says should be trusted.
    """
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("release", CONFIDENCE_THRESHOLD))

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="Foo 1.0 released")

    assert result.kind == "release"
    assert result.confidence == CONFIDENCE_THRESHOLD


def test_classify_returns_the_kind_above_the_confidence_threshold() -> None:
    """SC-1: confidence comfortably above the threshold counts as classified."""
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("tutorial", 0.95))

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="How to set up X")

    assert result.kind == "tutorial"
    assert result.confidence == 0.95


def test_classify_returns_unclassified_below_the_confidence_threshold() -> None:
    """
    SC-1: confidence just below CONFIDENCE_THRESHOLD (0.7) comes back unclassified,
    not forced into the endpoint's best guess.
    BREAKS: Low-confidence guesses get stored as if they were trustworthy.
    """
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("release", CONFIDENCE_THRESHOLD - 0.01))

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="Maybe a release?")

    assert result.kind is None
    assert result.confidence == pytest.approx(CONFIDENCE_THRESHOLD - 0.01)


def test_classify_returns_unclassified_when_choice_is_not_one_of_the_ten_kinds() -> None:
    """
    SC-1: a high-confidence choice outside the ten declared kinds still comes back
    unclassified -- the endpoint answering with something we didn't ask for is not
    trusted just because it's confident.
    BREAKS: An unrecognized choice string leaks through as a stored kind.
    """
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("meme", 0.95))  # not a declared kind

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="A meme")

    assert result.kind is None


# ---------------------------------------------------------------------------
# SC-1: fail-closed parsing of a response classify() cannot trust
# ---------------------------------------------------------------------------


def test_classify_fails_closed_when_the_kind_answer_is_absent() -> None:
    """
    A response missing the "kind" answer entirely comes back unclassified, not
    raised -- the call itself succeeded.
    BREAKS: A missing answer key raises a KeyError out of classify() instead of
    failing closed.
    """
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall({})

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="Unparseable")

    assert result.kind is None
    assert result.confidence is None


def test_classify_fails_closed_when_confidence_is_missing() -> None:
    """A choice with no confidence field at all comes back unclassified."""
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("release", None))

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="No confidence given")

    assert result.kind is None


def test_classify_fails_closed_when_confidence_is_not_numeric() -> None:
    """A non-numeric confidence value comes back unclassified, not raised."""
    classifier = KindClassifier("prismis-openrouter-kind")
    fake = _FakeDecisionCall(_kind_answer("release", "very confident"))

    with patch(_PATCH_SUBMIT, return_value=fake):
        result = classifier.classify(title="Bad confidence type")

    assert result.kind is None


# ---------------------------------------------------------------------------
# Provider-boundary failures raise, for the orchestrator's INV-002 handling
# ---------------------------------------------------------------------------


def test_classify_reraises_when_the_decisions_call_fails() -> None:
    """
    A failed call (network, auth, non-2xx) propagates out of classify() rather than
    being swallowed -- the orchestrator (INV-002) is the one responsible for storing
    the item without a kind and recording the failure.
    BREAKS: A transport failure is silently treated the same as a low-confidence
    answer, hiding the failure from the run's stats.
    """
    classifier = KindClassifier("prismis-openrouter-kind")

    with patch(_PATCH_SUBMIT, side_effect=RuntimeError("connection refused")):
        with pytest.raises(RuntimeError, match="connection refused"):
            classifier.classify(title="Boom")


def test_classify_uses_its_own_service_keyed_circuit_breaker() -> None:
    """
    classify() must key get_circuit_breaker off self.service_name -- not a hardcoded
    or copy-pasted constant. Driven with two distinct real CircuitBreaker instances
    from the real service-keyed registry, the same registry test_circuit_breaker_unit.py
    exercises directly: open only the breaker keyed to the kind service's own name,
    and leave an unrelated service's name closed.
    BREAKS: A copy-paste of another step's service name silently routes circuit state
    onto the wrong breaker, so an open kind-classifier circuit never blocks the call.
    """
    kind_service = "prismis-openrouter-kind"
    other_service = "prismis-openai"

    kind_breaker = get_circuit_breaker(kind_service)
    for _ in range(kind_breaker.failure_threshold):
        kind_breaker.record_failure(RuntimeError("insufficient_quota"))
    assert kind_breaker.check_can_proceed() is False, (
        "setup: the kind-keyed breaker must be open"
    )

    other_breaker = get_circuit_breaker(other_service)
    assert other_breaker.check_can_proceed() is True, (
        "setup: the unrelated service's breaker must stay closed"
    )

    classifier = KindClassifier(kind_service)

    with pytest.raises(RuntimeError, match="circuit breaker is open"):
        classifier.classify(title="Should never reach submit_decision")


# ---------------------------------------------------------------------------
# SC-2: the request state is bounded regardless of raw content length
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw_content_length", [0, 1, 500, 4_000, 300_000])
def test_build_state_is_bounded_regardless_of_raw_content_length(
    raw_content_length: int,
) -> None:
    """
    SC-2: raw content from empty to several hundred thousand characters all produce
    a state whose raw_content field is capped at 1000 characters.
    BREAKS: A source with unusually long raw content balloons the request sent to
    the decisions endpoint.
    """
    raw_content = "x" * raw_content_length

    state = _build_state(
        title="A title",
        source_type="rss",
        source_name="Example Feed",
        summary="A short summary.",
        reading_summary="y" * 10_000,
        raw_content=raw_content,
    )

    assert state["title"] == "A title"
    assert state["source_type"] == "rss"
    assert state["source_name"] == "Example Feed"
    assert state["summary"] == "A short summary."
    assert len(state["reading_summary"]) == 4_000
    assert len(state["raw_content"]) == min(raw_content_length, 1_000)


def test_build_state_does_not_truncate_a_reading_summary_under_the_cap() -> None:
    """A reading summary shorter than 4000 chars passes through unchanged."""
    state = _build_state(
        title="T",
        source_type="rss",
        source_name="S",
        summary="sum",
        reading_summary="short reading summary",
        raw_content="",
    )
    assert state["reading_summary"] == "short reading summary"


def test_build_state_handles_empty_raw_content() -> None:
    """SC-2's lower bound: empty raw content produces an empty (not missing) field."""
    state = _build_state(
        title="T",
        source_type="rss",
        source_name="S",
        summary="sum",
        reading_summary="reading",
        raw_content="",
    )
    assert state["raw_content"] == ""


# ---------------------------------------------------------------------------
# SC-1 (gh #82): health_check() -- the kind-service reachability probe used by
# `prismis-daemon verify` and startup validation.
# ---------------------------------------------------------------------------


def test_health_check_succeeds_when_the_call_names_one_of_the_ten_kinds() -> None:
    """
    SC-1: a call that succeeds and answers with one of the ten declared kinds passes
    the health check -- no exception -- and is logged as an llm.call event with its
    cost, exactly like every other classify call (the work order's stakes).
    BREAKS: A working kind service is reported as unreachable because health_check
    rejects a perfectly good answer, or the call succeeds silently with no
    observability event -- a regression that drops health_check's obs_log call would
    pass this test if it only checked for the absence of an exception.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    fake = _FakeDecisionCall(_kind_answer("release", 0.95))

    with patch(_PATCH_SUBMIT, return_value=fake):
        health_check("prismis-openrouter-kind")  # must not raise

    events = _classify_kind_events(run_id)
    assert len(events) == 1, f"expected exactly one classify_kind event, got {events}"
    event = events[0]
    assert event["status"] == "success"
    assert event["cost_usd"] == pytest.approx(fake.cost)
    assert event["model"] == fake.model


def test_health_check_raises_when_the_call_itself_fails() -> None:
    """
    SC-1: an unreachable endpoint, non-2xx status, or any other submit_decision
    failure propagates out of health_check -- exactly what submit_decision raises --
    and is logged as an llm.call event naming the error.
    BREAKS: A misconfigured base_url/key/model is swallowed, so verify and startup
    validation both report the kind service as healthy, or the failure is raised
    without a matching observability event -- silent to anyone reading the logs
    rather than watching the process exit.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)

    with patch(_PATCH_SUBMIT, side_effect=RuntimeError("connection refused")):
        with pytest.raises(RuntimeError, match="connection refused"):
            health_check("prismis-openrouter-kind")

    events = _classify_kind_events(run_id)
    assert len(events) == 1, f"expected exactly one classify_kind event, got {events}"
    event = events[0]
    assert event["status"] == "error"
    assert "connection refused" in event["error"]


def test_health_check_raises_when_the_answer_names_no_kind() -> None:
    """
    SC-1: a call that succeeds but answers with a choice outside the ten declared
    kinds raises -- a health check that "succeeds" on an answer naming no kind proves
    nothing (the work order's stakes) -- and the logged event reflects that failure
    (status="error"), not the call's own HTTP success.
    BREAKS: health_check reuses classify()'s fail-closed-to-None parsing and reports
    "healthy" on a response that never actually named a kind, or logs status="success"
    for a call that health_check itself is about to reject.
    """
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    fake = _FakeDecisionCall(_kind_answer("not-a-real-kind", 0.95))

    with patch(_PATCH_SUBMIT, return_value=fake):
        with pytest.raises(ValueError, match="names no kind"):
            health_check("prismis-openrouter-kind")

    events = _classify_kind_events(run_id)
    assert len(events) == 1, f"expected exactly one classify_kind event, got {events}"
    assert events[0]["status"] == "error"


def test_health_check_raises_when_the_kind_answer_is_absent() -> None:
    """A response missing the "kind" answer entirely also fails the health check,
    and is logged as an llm.call event with status="error"."""
    run_id = str(uuid.uuid4())
    set_run_id(run_id)
    fake = _FakeDecisionCall({})

    with patch(_PATCH_SUBMIT, return_value=fake):
        with pytest.raises(ValueError, match="names no kind"):
            health_check("prismis-openrouter-kind")

    events = _classify_kind_events(run_id)
    assert len(events) == 1, f"expected exactly one classify_kind event, got {events}"
    assert events[0]["status"] == "error"


def test_health_check_uses_submit_decision_not_a_second_request_builder() -> None:
    """
    SC-1: the health check is a single call made through kind_classifier's own
    submit_decision -- not a hand-rolled second request builder that could drift
    from what the real classify() call sends.
    BREAKS: A duplicated httpx call bypasses submit_decision's error handling and
    diverges from the real decisions request.
    """
    calls: list[tuple[dict, str]] = []

    def _capture(state: dict, *, service: str, model: str | None = None):
        calls.append((state, service))
        return _FakeDecisionCall(_kind_answer("release", 0.95))

    with patch(_PATCH_SUBMIT, side_effect=_capture):
        health_check("prismis-openrouter-kind")

    assert len(calls) == 1, "health_check must call submit_decision exactly once"
    assert calls[0][1] == "prismis-openrouter-kind"
