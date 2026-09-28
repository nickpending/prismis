"""Unit tests for kind_classifier -- content-kind work order, job 1 (SC-1, SC-2).

Invariants protected:
- SC-1: classify() gates on CONFIDENCE_THRESHOLD (0.7): a choice at or above the
  threshold comes back classified, below the threshold or not one of the ten declared
  kinds comes back unclassified. The ten kinds and their definitions live once, in
  KINDS.
- SC-2: _build_state() bounds the request regardless of raw_content's length: title,
  source type, source name and the light summary pass through whole; reading_summary
  is capped at 4000 chars and raw_content at 1000, for content from empty to ~300k
  chars.

Only submit_decision (the decisions-endpoint boundary) is stood in for, per Principle
I; _build_state and classify()'s own circuit breaker / parsing logic run for real,
unmocked.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import patch  # claudex-guard: allow-mock

import pytest

from prismis_daemon.circuit_breaker import get_circuit_breaker, reset_circuit_breaker
from prismis_daemon.kind_classifier import (
    CONFIDENCE_THRESHOLD,
    KINDS,
    KindClassifier,
    _build_state,
)

# Patch target -- the decisions-endpoint provider boundary itself, which the
# constitution permits standing in for (Principle I).
_PATCH_SUBMIT = "prismis_daemon.kind_classifier.submit_decision"  # claudex-guard: allow-mock


@pytest.fixture(autouse=True)
def clean_circuit_registry() -> Iterator[None]:
    """Reset circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


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
