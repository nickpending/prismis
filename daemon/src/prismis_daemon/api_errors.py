"""Simple, consistent API error handling."""

from fastapi import HTTPException


class APIError(HTTPException):
    """Simple API error with consistent format.

    All errors will be formatted as:
    {"success": false, "message": "error message", "data": null}
    """

    def __init__(self, status_code: int, message: str):
        """Create an API error.

        Args:
            status_code: HTTP status code
            message: Error message to display
        """
        super().__init__(status_code=status_code, detail=message)
        self.message = message


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
