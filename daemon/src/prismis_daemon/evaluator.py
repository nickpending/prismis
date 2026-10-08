"""Content interest evaluation against user context using LLM."""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .llm_call import bound_content, build_messages, call_llm_with_circuit_breaker
from .llm_client import extract_json

logger = logging.getLogger(__name__)


class PriorityLevel(str, Enum):
    """Content priority levels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class ContentEvaluation:
    """Result of evaluating content against user interests."""

    priority: PriorityLevel | None  # Can be None for unprioritized content
    matched_interests: list[str]
    reasoning: str | None = None
    preference_influenced: bool = (
        False  # True if learned preferences affected evaluation
    )


class ContentEvaluator:
    """Evaluates content against user interests using LLM integration."""

    def __init__(self, service_name: str):
        """Initialize the content evaluator.

        Args:
            service_name: Name of a [services.<name>] table in config.toml
        """
        self.service_name = service_name

        logger.info(f"ContentEvaluator initialized with service: {self.service_name}")

    def evaluate_content(
        self,
        content: str,
        title: str,
        url: str,
        context: str,
        learned_preferences: str | None = None,
    ) -> ContentEvaluation:
        """Evaluate content relevance against user context.

        Args:
            content: Content text to evaluate
            title: Title of the content
            url: URL of the content
            context: User's personal context for evaluation
            learned_preferences: Optional learned preferences from user feedback (for_llm_context)

        Returns:
            ContentEvaluation with priority level and matched interests
        """
        logger.debug(f"Evaluating content '{title[:50]}...' against user context")
        if learned_preferences:
            logger.debug("Including learned preferences in evaluation")

        try:
            messages = self._build_evaluation_prompt(
                content, title, url, context, learned_preferences
            )
            response = self._call_llm(messages)
            evaluation = self._parse_evaluation_response(response)
            # Mark as preference-influenced if learned preferences were used
            if learned_preferences:
                evaluation.preference_influenced = True
            return evaluation
        except Exception as e:
            logger.error(f"Content evaluation failed: {e}", exc_info=True)
            # Re-raise to stop processing completely per requirements
            raise

    def _build_evaluation_prompt(
        self,
        content: str,
        title: str,
        url: str,
        context: str,
        learned_preferences: str | None = None,
    ) -> list[dict[str, str]]:
        """Build the evaluation prompt for the LLM.

        Args:
            content: Content text to evaluate
            title: Title of the content
            url: URL of the content
            context: User's personal context
            learned_preferences: Optional learned preferences from user feedback

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

Examples:
- Security tool that matches high priority → priority: "high", matched_interests: ["LLM-driven security tools"]
- BJJ training video → priority: "low", matched_interests: ["Brazilian Jiu-Jitsu training approaches"]
- Basic password management article → priority: null, matched_interests: [] (matches Not Interested)"""

        # Inject learned preferences if available (from user feedback history)
        if learned_preferences:
            system_prompt += f"""

LEARNED USER PREFERENCES (from recent feedback):
{learned_preferences}

Use these learned preferences to SUPPLEMENT (not override) the user's context above.
If content matches topics the user has upvoted, consider boosting priority slightly.
If content matches topics the user has downvoted, consider lowering priority."""
        bounded = bound_content(content)

        user_prompt = f"""User's Personal Context:
{context}

Content to Evaluate:
Title: {title}
URL: {url}

Content Text:
{bounded.text}

Evaluate this content and respond with the JSON format specified."""

        return build_messages(system_prompt, user_prompt)

    def _call_llm(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Call the LLM with the evaluation prompt.

        Args:
            messages: Messages to send to the LLM

        Returns:
            Parsed JSON response from the LLM

        Raises:
            RuntimeError: If the service's circuit breaker is open
            Exception: If LLM call fails
        """
        try:
            logger.debug(
                f"Calling LLM service {self.service_name} for content evaluation"
            )

            # Extract system and user prompts from messages
            system_prompt = messages[0]["content"]
            user_prompt = messages[1]["content"]

            result = call_llm_with_circuit_breaker(
                self.service_name, system_prompt, user_prompt, "evaluate"
            )

            # Extract and parse response
            response_text = result.text

            # Parse JSON response (tolerates a ```json-fenced reply)
            parsed = extract_json(response_text)
            if parsed is None:
                logger.error(
                    f"Failed to parse LLM response as JSON. "
                    f"First 200 chars: {response_text[:200]!r}"
                )
                raise ValueError(
                    f"Invalid JSON response from LLM: {response_text[:200]!r}"
                )
            return parsed

        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            raise

    def _parse_evaluation_response(self, response: dict[str, Any]) -> ContentEvaluation:
        """Parse the LLM response into a ContentEvaluation.

        Args:
            response: Parsed JSON response from LLM

        Returns:
            ContentEvaluation object

        Raises:
            AttributeError, TypeError, ValueError: If the reply is valid JSON of the
                wrong shape (an array, a non-string priority, a matched_interests that
                is not a list). Not caught here: a reply that
                cannot be parsed is a failed evaluation, never "no priority" -- the
                caller (evaluate_content) re-raises to the per-item boundary.
        """
        # Parse priority - can be null now!
        priority_str = response.get("priority")

        # Parse matched interests first to validate priority
        matched_interests = response.get("matched_interests", [])

        # null is the model saying "none"; any other non-list is a malformed reply,
        # and coercing it to [] would store a matched item as unprioritized.
        if matched_interests is None:
            matched_interests = []
        elif not isinstance(matched_interests, list):
            raise ValueError(
                f"matched_interests must be a list, got {type(matched_interests).__name__}"
            )

        # Handle null priority or empty matched interests
        if priority_str is None or (not matched_interests and priority_str != "low"):
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
                    logger.debug("Invalid priority and no matches - setting to null")

        # Parse reasoning
        reasoning = response.get("reasoning")

        return ContentEvaluation(
            priority=priority,
            matched_interests=matched_interests,
            reasoning=reasoning,
        )
