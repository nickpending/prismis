"""Simple LLM configuration validator."""

import logging

import llm_core

logger = logging.getLogger(__name__)


def validate_llm_config(service_name: str) -> None:
    """Validate LLM service configuration can connect to provider.

    Args:
        service_name: Service name from services.toml

    Raises:
        Exception: If health check fails
    """
    llm_core.health_check(service=service_name)


def validate_llm_services(light_service: str, deep_service: str | None) -> dict:
    """Validate both LLM services.

    Light service must succeed (raises on failure). Deep service is optional;
    failure is logged as warning (non-fatal) and returned as status="unreachable".

    Args:
        light_service: Required light service name
        deep_service: Optional deep service name (None = disabled)

    Returns:
        {"light": "ok", "deep": "ok" | "unreachable" | "not_configured"}
    """
    # Light: fatal on failure
    llm_core.health_check(service=light_service)
    result = {"light": "ok"}

    # Deep: non-fatal
    if deep_service is None:
        result["deep"] = "not_configured"
        return result
    try:
        llm_core.health_check(service=deep_service)
        result["deep"] = "ok"
    except Exception as e:
        logger.warning(
            f"Deep service '{deep_service}' unreachable: {e}. "
            f"Deep extraction will be disabled at runtime."
        )
        result["deep"] = "unreachable"
    return result
