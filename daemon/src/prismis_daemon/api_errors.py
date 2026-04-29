"""Simple, consistent API error handling."""

from fastapi import HTTPException


class APIError(HTTPException):
    """Simple API error with consistent format.

    All errors will be formatted as:
    {"success": false, "message": "error message", "data": null}

    Subclasses may attach a structured `data` payload (e.g. a `reason` code) for
    cases where a single status code covers multiple distinguishable conditions
    that clients need to branch on (see ServiceUnavailableError).
    """

    def __init__(self, status_code: int, message: str, data: dict | None = None):
        """Create an API error.

        Args:
            status_code: HTTP status code
            message: Error message to display
            data: Optional structured payload for client-side branching.
        """
        super().__init__(status_code=status_code, detail=message)
        self.message = message
        self.data = data


# Convenience error classes for common cases
class ValidationError(APIError):
    """422 - Request validation failed."""

    def __init__(self, message: str):
        super().__init__(422, message)


class NotFoundError(APIError):
    """404 - Resource not found."""

    def __init__(self, resource: str, resource_id: str):
        super().__init__(404, f"{resource} not found: {resource_id}")


class AuthenticationError(APIError):
    """403 - Authentication failed."""

    def __init__(self, message: str = "Invalid API key"):
        super().__init__(403, message)


class ServerError(APIError):
    """500 - Internal server error."""

    def __init__(self, message: str):
        super().__init__(500, message)


class ServiceUnavailableError(APIError):
    """503 - Service temporarily unavailable (circuit open, service disabled).

    The optional `reason` argument is a stable, machine-parseable code so clients
    can distinguish conditions that share the same 503 status but require
    different user-facing messages (e.g. `"not_configured"` vs `"circuit_open"`).
    Surfaced in the response body's `data.reason` field.
    """

    def __init__(self, message: str, reason: str | None = None):
        data = {"reason": reason} if reason is not None else None
        super().__init__(503, message, data=data)
        self.reason = reason
