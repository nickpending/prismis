"""Authentication middleware for FastAPI."""

from typing import Optional
from fastapi import Security
from fastapi.security import APIKeyHeader
from .config import Config
from .api_errors import AuthenticationError, ServerError


api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


async def verify_api_key(api_key: Optional[str] = Security(api_key_header)) -> str:
    """Verify the API key from request headers.

    Args:
        api_key: API key from X-API-Key header

    Returns:
        The validated API key

    Raises:
        AuthenticationError: 403 if API key is invalid or missing
        ServerError: 500 if config loading fails
    """
    if not api_key:
        raise AuthenticationError("Missing API key. Please provide X-API-Key header")

    # Load config to get the expected API key
    try:
        config = Config.from_file()
        expected_key = config.api_key

        if api_key == expected_key:
            return api_key

    except Exception as e:
        # If config loading fails, provide helpful error
        raise ServerError(f"Failed to load API configuration: {str(e)}")

    raise AuthenticationError("Invalid API key")
