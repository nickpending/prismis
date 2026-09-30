"""Simple LLM configuration validator."""

import logging

from . import kind_classifier, llm_client

logger = logging.getLogger(__name__)


def validate_llm_config(service_name: str) -> None:
    """Validate LLM service configuration can connect to provider.

    Args:
        service_name: Service name from services.toml

    Raises:
        Exception: If health check fails
    """
    llm_client.health_check(service=service_name)


def validate_llm_services(
    light_service: str,
    deep_service: str | None,
    kind_service: str | None = None,
) -> dict:
    """Validate every configured LLM service.

    Light service must succeed (raises on failure). Deep and kind services are each
    optional; failure is logged as a warning (non-fatal) and returned as
    status="unreachable" -- gh #82's point is that the kind service degrades exactly
    like deep, not that it blocks startup.

    Args:
        light_service: Required light service name
        deep_service: Optional deep service name (None = disabled)
        kind_service: Optional kind service name (None = disabled)

    Returns:
        {"light": "ok",
         "deep": "ok" | "unreachable" | "not_configured",
         "kind": "ok" | "unreachable" | "not_configured"}
    """
    # Light: fatal on failure
    llm_client.health_check(service=light_service)
    result = {"light": "ok"}

    # Deep: non-fatal  # noqa: ERA001 - prose, not code
    if deep_service is None:
        result["deep"] = "not_configured"
    else:
        try:
            llm_client.health_check(service=deep_service)
            result["deep"] = "ok"
        except Exception as e:
            logger.warning(
                f"Deep service '{deep_service}' unreachable: {e}. "
                f"build_orchestrator still constructs the deep extractor, so each "
                f"deep-extraction attempt will fail at runtime: the item is stored "
                f"with its light summary only and the failure is counted in "
                f"deep_extract_failures."
            )
            result["deep"] = "unreachable"

    # Kind: non-fatal, same shape as deep (SC-2)
    if kind_service is None:
        result["kind"] = "not_configured"
    else:
        try:
            kind_classifier.health_check(service=kind_service)
            result["kind"] = "ok"
        except Exception as e:
            logger.warning(
                f"Kind service '{kind_service}' unreachable: {e}. "
                f"Each classify call will fail at runtime: the item is stored "
                f"without a kind and the failure is counted in "
                f"kind_classify_failures."
            )
            result["kind"] = "unreachable"

    return result
