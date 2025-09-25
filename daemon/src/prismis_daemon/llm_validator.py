"""Simple LLM configuration validator."""

import asyncio
import litellm
from typing import Dict, Any


def validate_llm_config(config: Dict[str, Any]) -> None:
    """Validate LLM configuration can connect to the provider.

    Args:
        config: Dict with provider, model, api_key, and optional api_base

    Raises:
        ValueError: If configuration is invalid
        Exception: If LLM connection fails
    """
    # Check required fields
    if not config.get("model"):
        raise ValueError("Model must be specified in config")

    # Check Ollama-specific requirements
    provider = config.get("provider", "openai").lower()
    if provider == "ollama" and not config.get("api_base"):
        raise ValueError(
            "Ollama provider requires 'api_base' in config (e.g., 'http://localhost:11434')"
        )

    # Test actual connection
    model_params = {"model": config["model"]}
    if config.get("api_key"):
        model_params["api_key"] = config["api_key"]
    if config.get("api_base"):
        model_params["api_base"] = config["api_base"]

    # Use LiteLLM's health check
    asyncio.run(litellm.ahealth_check(model_params))
