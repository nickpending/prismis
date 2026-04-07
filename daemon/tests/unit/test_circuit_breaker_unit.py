"""Unit tests for circuit breaker service-keyed registry — SC-14."""

import pytest

from prismis_daemon.circuit_breaker import (
    CircuitBreaker,
    CircuitState,
    get_circuit_breaker,
    reset_circuit_breaker,
)


@pytest.fixture(autouse=True)
def clean_registry() -> None:
    """Reset the circuit breaker registry before and after each test."""
    reset_circuit_breaker()
    yield
    reset_circuit_breaker()


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
