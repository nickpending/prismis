"""Authentication middleware for FastAPI."""

import logging
from typing import Optional
from fastapi import Security
from fastapi.security import APIKeyHeader
from .config import Config
from .api_errors import AuthenticationError, ServerError

logger = logging.getLogger(__name__)

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# The response says only that the config would not load. The reason it would not load
# goes to the log instead: this runs on every authenticated request, so it re-reads the
# file while the daemon is up, and a config edited into a TOML parse error produces an
# exception whose text quotes the offending line of that file — the one holding the API
# key and the Reddit credentials. Sending that back to an unauthenticated caller would
# put a secret in a response body.
CONFIG_UNAVAILABLE_MESSAGE = (
    "Failed to load API configuration - check the daemon log and config.toml"
)


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
        logger.error(f"Failed to load API configuration: {e}")
        raise ServerError(CONFIG_UNAVAILABLE_MESSAGE) from e

    raise AuthenticationError("Invalid API key")
