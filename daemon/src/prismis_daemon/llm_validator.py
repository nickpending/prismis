"""Simple LLM configuration validator."""

import llm_core


def validate_llm_config(service_name: str) -> None:
    """Validate LLM service configuration can connect to provider.

    Args:
        service_name: Service name from services.toml

    Raises:
        Exception: If health check fails
    """
    llm_core.health_check(service=service_name)
