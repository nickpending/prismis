"""Circuit breaker for LLM quota exhaustion - prevents cascading failures."""

import logging
import time
from datetime import datetime
from enum import Enum

try:
    from .observability import log as obs_log
except ImportError:
    from observability import log as obs_log

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Circuit breaker states."""

    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Circuit breaker for LLM quota exhaustion.

    Opens after repeated quota errors to prevent wasted API calls.
    Closes after recovery timeout to allow retry.
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        recovery_timeout_seconds: int = 3600,  # 1 hour per task spec
    ):
        """Initialize circuit breaker.

        Args:
            failure_threshold: Quota errors to trigger open state
            recovery_timeout_seconds: Time before half-open retry
        """
        self.state = CircuitState.CLOSED
        self.failure_threshold = failure_threshold
        self.recovery_timeout_seconds = recovery_timeout_seconds

        self.failure_count = 0
        self.opened_at: float | None = None
        self.last_failure_time: float | None = None

    def is_quota_error(self, error: Exception) -> bool:
        """Detect if error indicates quota/rate limit exhaustion.

        Args:
            error: Exception to check

        Returns:
            True if error indicates quota exhaustion
        """
        error_str = str(error).lower()

        quota_patterns = [
            "quota",
            "insufficient_quota",
            "billing",
            "payment_required",
            "rate limit",
            "rate_limit",
            "ratelimit",
            "429",
            "too many requests",
        ]

        return any(pattern in error_str for pattern in quota_patterns)

    def check_can_proceed(self) -> bool:
        """Check if LLM call should proceed.

        Returns:
            True if circuit allows calls, False if open
        """
        if self.state == CircuitState.CLOSED:
            return True

        if self.state == CircuitState.OPEN:
            if self.opened_at is None:
                return True  # Safety reset

            elapsed = time.time() - self.opened_at
            if elapsed >= self.recovery_timeout_seconds:
                self.state = CircuitState.HALF_OPEN
                obs_log(
                    "circuit_breaker.state",
                    state="half_open",
                    elapsed_seconds=int(elapsed),
                )
                logger.info("Circuit breaker HALF_OPEN: attempting recovery")
                return True

            return False

        # HALF_OPEN: allow one attempt
        return True

    def record_failure(self, error: Exception) -> None:
        """Record a quota failure and potentially open circuit.

        Args:
            error: The quota error that occurred
        """
        if not self.is_quota_error(error):
            return  # Not a quota error, don't count

        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.state == CircuitState.HALF_OPEN:
            # Failed during recovery attempt, reopen
            self.state = CircuitState.OPEN
            self.opened_at = time.time()
            obs_log(
                "circuit_breaker.state",
                state="open",
                reason="half_open_failure",
                failure_count=self.failure_count,
            )
            logger.warning("Circuit breaker OPEN: recovery failed")
            return

        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            self.opened_at = time.time()
            obs_log(
                "circuit_breaker.state",
                state="open",
                reason="threshold_exceeded",
                failure_count=self.failure_count,
                threshold=self.failure_threshold,
            )
            logger.warning(
                f"Circuit breaker OPEN: {self.failure_count} quota errors "
                f"(threshold: {self.failure_threshold})"
            )

    def record_success(self) -> None:
        """Record successful LLM call.

        Closes circuit if in half-open state.
        """
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.opened_at = None
            obs_log("circuit_breaker.state", state="closed", reason="recovery_success")
            logger.info("Circuit breaker CLOSED: service recovered")

    def get_status(self) -> dict:
        """Get current circuit breaker status.

        Returns:
            Dict with state, failure_count, time info
        """
        status = {
            "state": self.state.value,
            "failure_count": self.failure_count,
            "failure_threshold": self.failure_threshold,
        }

        if self.opened_at is not None:
            elapsed = time.time() - self.opened_at
            remaining = max(0, self.recovery_timeout_seconds - elapsed)
            status["opened_at"] = datetime.fromtimestamp(self.opened_at).isoformat()
            status["recovery_in_seconds"] = int(remaining)

        return status


# Global singleton instance
_breaker: CircuitBreaker | None = None


def get_circuit_breaker() -> CircuitBreaker:
    """Get global circuit breaker instance (singleton)."""
    global _breaker
    if _breaker is None:
        _breaker = CircuitBreaker()
    return _breaker


def reset_circuit_breaker() -> None:
    """Reset circuit breaker (for testing)."""
    global _breaker
    _breaker = None
