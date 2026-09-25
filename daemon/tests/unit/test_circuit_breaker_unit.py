"""Unit tests for circuit breaker service-keyed registry — SC-14."""

from collections.abc import Iterator

import httpx2
import openai
import pytest

from prismis_daemon.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
)


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    """Reset the circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


def _status_error(
    status_code: int, cls: type[openai.APIStatusError] = openai.APIStatusError
) -> openai.APIStatusError:
    """Build a real openai SDK status error via its own constructor -- no network call.

    httpx2 is the openai SDK's own vendored httpx; APIStatusError.__init__ reads
    response.status_code directly off it, so this produces the exact object shape
    circuit_breaker.is_quota_error sees from a real provider failure.
    """
    request = httpx2.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx2.Response(
        status_code, request=request, json={"error": {"message": "stub"}}
    )
    return cls("stub error", response=response, body={"message": "stub"})


def test_is_quota_error_recognizes_rate_limit_error_type() -> None:
    """
    SC-4: is_quota_error must recognize the openai SDK's own RateLimitError by type,
    not only by matching a substring in str(error).
    BREAKS: A RateLimitError whose message text happens not to contain a quota keyword
    (a terse provider message) is never counted, and the circuit never opens.
    """
    cb = CircuitBreaker()
    err = _status_error(429, openai.RateLimitError)
    assert cb.is_quota_error(err) is True


def test_is_quota_error_recognizes_402_payment_required_status() -> None:
    """
    SC-4: is_quota_error must recognize APIStatusError(status_code=402) -- OpenRouter's
    shape for "out of credit" -- by its real status_code, not by string matching.
    BREAKS: An out-of-credit response whose message text doesn't literally contain
    "payment_required" (OpenRouter's own wording varies) is never counted.
    """
    cb = CircuitBreaker()
    err = _status_error(402)
    assert cb.is_quota_error(err) is True


def test_is_quota_error_typed_status_error_other_code_falls_through() -> None:
    """
    A non-quota APIStatusError (500) is not misclassified as a quota error by type
    alone -- with a message carrying none of the substring patterns either, it must
    read as not-a-quota-error, same as before typed classification was added.
    """
    cb = CircuitBreaker()
    err = _status_error(500)
    assert cb.is_quota_error(err) is False


def test_different_service_names_return_different_instances() -> None:
    """
    SC-14: Service isolation — different services get independent circuit breakers.
    BREAKS: One service's quota errors silence a different service.
    """
    cb_a = get_circuit_breaker("prismis-openai")
    cb_b = get_circuit_breaker("prismis-openai-deep")

    assert cb_a is not cb_b, (
        "Different service names must return distinct CircuitBreaker instances"
    )


def test_same_service_name_returns_same_instance() -> None:
    """
    SC-14: Singleton per service — repeated lookups return the same instance.
    BREAKS: State is lost between calls; circuit never opens despite repeated failures.
    """
    cb_first = get_circuit_breaker("prismis-openai")
    cb_second = get_circuit_breaker("prismis-openai")

    assert cb_first is cb_second, (
        "Same service name must return the identical CircuitBreaker instance"
    )


def test_quota_error_on_one_service_does_not_open_other() -> None:
    """
    SC-14: Failure isolation — quota errors on service A do not affect service B.
    BREAKS: A quota spike on one service causes silent failures on unrelated services.
    """
    cb_a = get_circuit_breaker("prismis-openai")
    cb_b = get_circuit_breaker("prismis-openai-deep")

    # Drive service A to open state (threshold=3 by default)
    quota_error = Exception("quota exhausted — 429 too many requests")
    for _ in range(3):
        cb_a.record_failure(quota_error)

    assert cb_a.state == CircuitState.OPEN, (
        "Service A should be open after 3 quota errors"
    )
    assert cb_b.state == CircuitState.CLOSED, (
        "Service B must remain closed — unrelated to A's failures"
    )
    assert cb_b.check_can_proceed(), "Service B must still allow calls"


def test_reset_specific_service_leaves_others_intact() -> None:
    """
    reset_circuit_breaker(name) removes only the named service.
    BREAKS: Resetting one service for testing clears unrelated service state.
    """
    cb_a = get_circuit_breaker("prismis-openai")
    cb_b = get_circuit_breaker("prismis-openai-deep")

    # Open service A
    quota_error = Exception("billing quota exceeded")
    for _ in range(3):
        cb_a.record_failure(quota_error)

    assert cb_a.state == CircuitState.OPEN

    # Reset only service A
    reset_circuit_breaker("prismis-openai")

    # B is untouched — same instance, same state
    assert cb_b.state == CircuitState.CLOSED

    # A is gone from registry — next lookup creates fresh instance
    cb_a_new = get_circuit_breaker("prismis-openai")
    assert cb_a_new.state == CircuitState.CLOSED, (
        "Freshly created breaker must start closed"
    )
    assert cb_a_new is not cb_a, "Reset must produce a new instance"


def test_reset_all_clears_registry() -> None:
    """
    reset_circuit_breaker(None) wipes the entire registry.
    BREAKS: Test isolation fails if previous test's open circuit bleeds into next.
    """
    get_circuit_breaker("service-x")
    get_circuit_breaker("service-y")

    reset_circuit_breaker()  # No argument = reset all

    # Both lookups produce fresh (closed) instances
    assert get_circuit_breaker("service-x").state == CircuitState.CLOSED
    assert get_circuit_breaker("service-y").state == CircuitState.CLOSED


def test_circuit_opens_after_threshold_and_enters_half_open_after_timeout() -> None:
    """
    SC-14 full lifecycle: CLOSED → OPEN → HALF_OPEN after recovery timeout.
    BREAKS: Circuit never recovers; all calls blocked indefinitely after quota spike.
    """
    # Short timeout for test speed
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout_seconds=0)

    assert cb.state == CircuitState.CLOSED
    assert cb.check_can_proceed()

    quota_error = Exception("insufficient_quota — payment required")
    for _ in range(3):
        cb.record_failure(quota_error)

    assert cb.state == CircuitState.OPEN
    # With 0-second timeout, elapsed >= 0 immediately — next check transitions to half-open
    assert cb.check_can_proceed(), "Should allow one attempt in HALF_OPEN state"
    assert cb.state == CircuitState.HALF_OPEN
