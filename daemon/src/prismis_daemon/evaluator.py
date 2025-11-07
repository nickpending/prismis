"""Content interest evaluation against user context using LLM."""

import json
import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Any, List, Optional

import litellm
from litellm import completion_cost

try:
    from .observability import log as obs_log
except ImportError:
    from observability import log as obs_log

logger = logging.getLogger(__name__)


class PriorityLevel(str, Enum):
    """Content priority levels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ContentEvaluation:
    """Result of evaluating content against user interests."""

    priority: Optional[PriorityLevel]  # Can be None for unprioritized content
    matched_interests: List[str]
    reasoning: Optional[str] = None


class ContentEvaluator:
    """Evaluates content against user interests using LLM integration."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the content evaluator.

        Args:
            config: Optional configuration dict with:
                - model: LLM model name (default: gpt-4o-mini)
                - api_key: API key for the provider
        """
        self.config = config or {}
        if not self.config or "model" not in self.config:
            raise ValueError("Model must be specified in config")
        self.model = self.config["model"]

        # Store credentials for direct passing to litellm
        self.api_key = self.config.get("api_key")
        self.api_base = self.config.get("api_base")

        # Validate Ollama configuration
        provider = self.config.get("provider", "openai").lower()
        if provider == "ollama" and not self.api_base:
            raise ValueError(
                "Ollama provider requires 'api_base' in config (e.g., 'http://localhost:11434')"
            )

        # Configure LiteLLM settings
        litellm.drop_params = True  # Drop unsupported params automatically
        self.temperature = 0.3  # Fixed for evaluation

        logger.info(f"ContentEvaluator initialized with model: {self.model}")

    def evaluate_content(
        self, content: str, title: str, url: str, context: str
    ) -> ContentEvaluation:
        """Evaluate content relevance against user context.

        Args:
            content: Content text to evaluate
            title: Title of the content
            url: URL of the content
            context: User's personal context for evaluation

        Returns:
            ContentEvaluation with priority level and matched interests
        """
        logger.debug(f"Evaluating content '{title[:50]}...' against user context")

        try:
            messages = self._build_evaluation_prompt(content, title, url, context)
            response = self._call_llm(messages)
            return self._parse_evaluation_response(response)
        except Exception as e:
            logger.error(f"Content evaluation failed: {e}", exc_info=True)
            # Re-raise to stop processing completely per requirements
            raise

    def _build_evaluation_prompt(
        self, content: str, title: str, url: str, context: str
    ) -> List[Dict[str, str]]:
        """Build the evaluation prompt for the LLM.

        Args:
            content: Content text to evaluate
            title: Title of the content
            url: URL of the content
            context: User's personal context

        Returns:
            List of messages for the LLM
        """
        system_prompt = """You are an expert content analyst who evaluates articles for personalized relevance to a specific user.

Your task is to evaluate how relevant and interesting this content is to the user based on their personal context.

Respond with ONLY valid JSON in this exact format:

{
  "priority": "high" | "medium" | "low" | null,
  "matched_interests": ["specific user interest 1", "specific user interest 2", ...],
  "reasoning": "One sentence describing content and which interest it relates to (10-15 words)"
}

CRITICAL EVALUATION RULES:
1. If matched_interests is empty (no matches found), you MUST return priority: null
2. If content matches "Not Interested" topics, you MUST return priority: null
3. Only assign a priority (high/medium/low) if content ACTUALLY matches something in the user's context

Priority Assignment Logic:
- high: ONLY if it matches topics in "High Priority Topics" section
- medium: ONLY if it matches topics in "Medium Priority Topics" section  
- low: ONLY if it matches topics in "Low Priority Topics" section
- null: If NO interests match OR if it matches "Not Interested" topics

IMPORTANT: Most content should be null. Be selective - only assign priorities to content that clearly matches the user's stated interests.

Examples:
- Random AI discussion with no security relevance → priority: null, matched_interests: []
- Security tool that matches high priority → priority: "high", matched_interests: ["LLM-driven security tools"]
- BJJ training video → priority: "low", matched_interests: ["Brazilian Jiu-Jitsu training approaches"]
- Basic password management article → priority: null, matched_interests: [] (matches Not Interested)"""

        user_prompt = f"""User's Personal Context:
{context}

Content to Evaluate:
Title: {title}
URL: {url}

Content Text:
{content}

Evaluate this content and respond with the JSON format specified."""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _call_llm(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        """Call LiteLLM with the evaluation prompt.

        Args:
            messages: Messages to send to the LLM

        Returns:
            Parsed JSON response from the LLM

        Raises:
            Exception: If LLM call fails
        """
        try:
            logger.debug(f"Calling {self.model} for content evaluation")

            # Build kwargs for litellm call
            kwargs = {
                "model": self.model,
                "messages": messages,
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},  # Request JSON response
            }

            # Add credentials if provided
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            # Track LLM call timing and cost for observability
            start_time = time.time()

            try:
                response = litellm.completion(**kwargs)

                # Calculate duration
                duration_ms = int((time.time() - start_time) * 1000)

                # Extract token usage
                tokens = {
                    "prompt": response.usage.prompt_tokens if response.usage else 0,
                    "completion": response.usage.completion_tokens
                    if response.usage
                    else 0,
                    "total": response.usage.total_tokens if response.usage else 0,
                }

                # Calculate cost
                try:
                    cost_usd = completion_cost(response)
                except Exception:
                    cost_usd = 0.0

                # Log successful LLM call
                obs_log(
                    "llm.call",
                    action="evaluate",
                    model=self.model,
                    tokens=tokens,
                    cost_usd=cost_usd,
                    duration_ms=duration_ms,
                    status="success",
                )

            except Exception as e:
                # Log failed LLM call
                duration_ms = int((time.time() - start_time) * 1000)
                obs_log(
                    "llm.call",
                    action="evaluate",
                    model=self.model,
                    status="error",
                    error=str(e),
                    duration_ms=duration_ms,
                )
                raise  # Re-raise to preserve existing error handling

            # Extract the response text
            response_text = response.choices[0].message.content

            # Parse JSON response
            return json.loads(response_text)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            raise ValueError(f"Invalid JSON response from LLM: {e}")
        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            raise

    def _parse_evaluation_response(self, response: Dict[str, Any]) -> ContentEvaluation:
        """Parse the LLM response into a ContentEvaluation.

        Args:
            response: Parsed JSON response from LLM

        Returns:
            ContentEvaluation object
        """
        try:
            # Parse priority - can be null now!
            priority_str = response.get("priority")

            # Parse matched interests first to validate priority
            matched_interests = response.get("matched_interests", [])

            # Validate matched_interests format
            if matched_interests and not isinstance(matched_interests, list):
                logger.warning("matched_interests should be a list, converting")
                matched_interests = []

            # Handle null priority or empty matched interests
            if priority_str is None or (
                not matched_interests and priority_str != "low"
            ):
                # NULL priority - content doesn't match any interests
                priority = None
                logger.debug("Content has no priority (null) - no interests matched")
            else:
                # Validate and convert to enum
                priority_str = priority_str.lower() if priority_str else "medium"
                try:
                    priority = PriorityLevel(priority_str)
                except ValueError:
                    # Invalid priority, but has matched interests - default to medium
                    if matched_interests:
                        logger.warning(
                            f"Invalid priority level from LLM: {priority_str}, using MEDIUM"
                        )
                        priority = PriorityLevel.MEDIUM
                    else:
                        # No matches and invalid priority - set to null
                        priority = None
                        logger.debug(
                            "Invalid priority and no matches - setting to null"
                        )

            # Parse reasoning
            reasoning = response.get("reasoning")

            return ContentEvaluation(
                priority=priority,
                matched_interests=matched_interests,
                reasoning=reasoning,
            )

        except Exception as e:
            logger.error(f"Failed to parse evaluation response: {e}")
            # Return a safe default - no priority assignment on parse errors
            return ContentEvaluation(
                priority=None,
                matched_interests=[],
                reasoning=str(e),
            )
