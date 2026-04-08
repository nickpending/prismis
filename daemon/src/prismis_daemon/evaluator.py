"""Content interest evaluation against user context using LLM."""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from llm_core import complete

from .circuit_breaker import get_circuit_breaker
from .observability import log as obs_log

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
            service_name: Service name from ~/.config/llm-core/services.toml
        """
        self.service_name = service_name
        self.temperature = 0.3  # Fixed for evaluation

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

IMPORTANT: Most content should be null. Be selective - only assign priorities to content that clearly matches the user's stated interests.

Examples:
- Random AI discussion with no security relevance → priority: null, matched_interests: []
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

    def _call_llm(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Call llm-core with the evaluation prompt.

        Args:
            messages: Messages to send to the LLM

        Returns:
            Parsed JSON response from the LLM

        Raises:
            Exception: If LLM call fails
        """
        try:
            logger.debug(
                f"Calling llm-core service {self.service_name} for content evaluation"
            )

            # Extract system and user prompts from messages
            system_prompt = messages[0]["content"]
            user_prompt = messages[1]["content"]

            # Check circuit breaker before LLM call
            circuit = get_circuit_breaker(self.service_name)
            if not circuit.check_can_proceed():
                status = circuit.get_status()
                raise RuntimeError(
                    f"LLM circuit breaker is open (quota exhausted). "
                    f"Recovery in {status.get('recovery_in_seconds', 'unknown')}s"
                )

            try:
                result = complete(
                    prompt=user_prompt,
                    system_prompt=system_prompt,
                    service=self.service_name,
                    temperature=self.temperature,
                    json=True,
                )

                # Extract token usage
                tokens = {
                    "prompt": result.tokens.input,
                    "completion": result.tokens.output,
                    "total": result.tokens.input + result.tokens.output,
                }

                # Cost already estimated by llm_core
                cost_usd = result.cost

                # Log successful LLM call
                obs_log(
                    "llm.call",
                    action="evaluate",
                    model=result.model,
                    tokens=tokens,
                    cost_usd=cost_usd,
                    duration_ms=result.duration_ms,
                    status="success",
                )

                # Record success for circuit breaker (closes if half-open)
                circuit.record_success()

            except Exception as e:
                # Record failure for circuit breaker (may open circuit)
                circuit.record_failure(e)

                # Log failed LLM call
                obs_log(
                    "llm.call",
                    action="evaluate",
                    model=self.service_name,
                    status="error",
                    error=str(e),
                )
                raise  # Re-raise to preserve existing error handling

            # Extract and parse response
            response_text = result.text

            # Parse JSON response
            return json.loads(response_text)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM response as JSON: {e}")
            raise ValueError(f"Invalid JSON response from LLM: {e}") from e
        except Exception as e:
            logger.error(f"LLM evaluation failed: {e}")
            raise

    def _parse_evaluation_response(self, response: dict[str, Any]) -> ContentEvaluation:
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
