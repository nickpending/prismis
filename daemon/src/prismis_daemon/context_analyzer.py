"""Context analysis with LLM to suggest topics for user's context.md."""

import json
import logging
import re
from typing import Any

from llm_core import complete

from .observability import log as obs_log

logger = logging.getLogger(__name__)


class ContextAnalyzer:
    """Analyze flagged content items to suggest new topics for context.md."""

    def __init__(self, service_name: str):
        """Initialize the context analyzer.

        Args:
            service_name: Service name from ~/.config/llm-core/services.toml
        """
        self.service_name = service_name
        self.temperature = 0.3  # Fixed for consistent analysis

        logger.info(f"ContextAnalyzer initialized with service: {self.service_name}")

    def analyze_flagged_items(
        self, flagged_items: list[dict[str, Any]], context_text: str
    ) -> dict[str, Any]:
        """Analyze upvoted items to suggest new topics for context.md.

        Args:
            flagged_items: List of content items with user_feedback='up'
            context_text: Current context.md file contents

        Returns:
            Dict with "suggested_topics" array containing:
                - topic: str (suggested topic name)
                - section: str ("high", "medium", or "low")
                - rationale: str (why this topic is suggested)
        """
        # Handle empty flagged items
        if not flagged_items:
            logger.info("No flagged items to analyze")
            return {"suggested_topics": []}

        # Limit to 50 most recent items to avoid token overflow
        if len(flagged_items) > 50:
            logger.info(f"Truncating {len(flagged_items)} flagged items to 50")
            flagged_items = flagged_items[:50]

        logger.debug(f"Analyzing {len(flagged_items)} flagged items against context.md")

        try:
            # Parse existing topics from context
            existing_topics = self._parse_context_sections(context_text)

            # Build prompt with flagged items and existing topics
            messages = self._build_prompt(flagged_items, existing_topics)

            # Call LLM
            response = self._call_llm(messages)

            # Validate response structure
            if "suggested_topics" not in response:
                logger.error("LLM response missing 'suggested_topics' field")
                return {"suggested_topics": []}

            # Validate each suggestion
            valid_suggestions = []
            for suggestion in response["suggested_topics"]:
                if self._validate_suggestion(suggestion):
                    valid_suggestions.append(suggestion)
                else:
                    logger.warning(f"Invalid suggestion filtered out: {suggestion}")

            return {"suggested_topics": valid_suggestions}

        except Exception as e:
            logger.error(f"Context analysis failed: {e}", exc_info=True)
            # Re-raise to stop processing completely per requirements
            raise

    def _parse_context_sections(self, context_text: str) -> dict[str, list[str]]:
        """Parse context.md to extract existing topics by section.

        Args:
            context_text: Content of context.md file

        Returns:
            Dict with keys "high", "medium", "low", each containing list of topics
        """
        sections = {"high": [], "medium": [], "low": []}

        if not context_text:
            logger.warning("Empty context.md provided")
            return sections

        # Split by section headers
        high_match = re.search(
            r"## High Priority Topics\s*\n(.*?)(?=\n## |\Z)", context_text, re.DOTALL
        )
        medium_match = re.search(
            r"## Medium Priority Topics\s*\n(.*?)(?=\n## |\Z)", context_text, re.DOTALL
        )
        low_match = re.search(
            r"## Low Priority Topics\s*\n(.*?)(?=\n## |\Z)", context_text, re.DOTALL
        )

        # Extract bullet points from each section
        if high_match:
            sections["high"] = self._extract_topics(high_match.group(1))
        if medium_match:
            sections["medium"] = self._extract_topics(medium_match.group(1))
        if low_match:
            sections["low"] = self._extract_topics(low_match.group(1))

        logger.debug(
            f"Parsed context sections: {len(sections['high'])} high, "
            f"{len(sections['medium'])} medium, {len(sections['low'])} low"
        )

        return sections

    def _extract_topics(self, section_text: str) -> list[str]:
        """Extract topic strings from a section's text.

        Args:
            section_text: Text content of a section

        Returns:
            List of topic strings
        """
        topics = []
        for line in section_text.split("\n"):
            line = line.strip()
            # Match bullet points (- topic text)
            if line.startswith("- "):
                topic = line[2:].strip()
                if topic:
                    topics.append(topic)
        return topics

    def _build_prompt(
        self, flagged_items: list[dict[str, Any]], existing_topics: dict[str, list[str]]
    ) -> list[dict[str, str]]:
        """Build the analysis prompt for the LLM.

        Args:
            flagged_items: Content items flagged as interesting
            existing_topics: Existing topics from context.md by section

        Returns:
            List of messages for the LLM
        """
        # Format existing topics for prompt
        existing_high = (
            ", ".join(existing_topics["high"]) if existing_topics["high"] else "None"
        )
        existing_medium = (
            ", ".join(existing_topics["medium"])
            if existing_topics["medium"]
            else "None"
        )
        existing_low = (
            ", ".join(existing_topics["low"]) if existing_topics["low"] else "None"
        )

        # Format flagged items (title + summary only to save tokens)
        items_text = []
        for i, item in enumerate(flagged_items, 1):
            title = item.get("title", "Untitled")
            summary = item.get("summary", "")
            source_name = item.get("source_name", "")

            # Fallback to first 200 chars of content if no summary
            if not summary:
                content = item.get("content", "")
                summary = content[:200] + "..." if len(content) > 200 else content

            items_text.append(f"{i}. {title} ({source_name})\n   {summary}")

        items_formatted = "\n\n".join(items_text)

        system_prompt = """You are a context analysis assistant for a content intelligence system.

SYSTEM OVERVIEW:
The user runs automated content prioritization that:
- Fetches content from RSS feeds, Reddit, YouTube
- Evaluates content against their context.md topics
- Prioritizes matching items (high/medium/low)

The user UPVOTED these items - signaling they found them valuable. This feedback helps identify
gaps in context.md or topics that deserve higher priority.

TASK:
Analyze why these upvoted items might indicate context.md improvements are needed.

OUTPUT FORMAT:
Return ONLY valid JSON:

{
  "suggested_topics": [
    {
      "topic": "Topic text matching user's existing style",
      "section": "high" | "medium" | "low",
      "action": "expand" | "narrow" | "add" | "split",
      "existing_topic": "Closest existing topic (null if action=add)",
      "gap_analysis": "Why existing topic missed this",
      "rationale": "How this fix captures similar content"
    }
  ]
}

ACTIONS:
- expand: Existing topic too narrow, broaden it
- narrow: Existing topic too broad, make more specific
- add: Completely new area not covered
- split: One topic covering unrelated things, separate them

REQUIRED ANALYSIS:
1. Study user's existing topics:
   - Count typical word length of their topics
   - Note phrasing patterns (e.g., use of &, technical specificity, tone)
   - Observe their level of detail vs. brevity
2. Identify closest existing topic for each flagged item
3. Explain the gap: too narrow/broad/wrong focus/missing entirely
4. Generate topic name MATCHING user's exact style (length, phrasing, tone)

RULES:
- NEVER return empty - every flagged item proves a gap
- Match user's topic style (examine their existing topics)
- If multiple similar items: ONE recommendation covering all
- Section based on user's existing patterns"""

        user_prompt = f"""EXISTING CONTEXT:
High priority: {existing_high}
Medium priority: {existing_medium}
Low priority: {existing_low}

UPVOTED ITEMS (content user found valuable):

{items_formatted}

Analyze each item and recommend context.md improvements."""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _call_llm(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Call llm-core with the analysis prompt.

        Args:
            messages: Messages to send to the LLM

        Returns:
            Parsed JSON response from the LLM

        Raises:
            Exception: If LLM call fails
        """
        try:
            logger.debug(
                f"Calling llm-core service {self.service_name} for context analysis"
            )

            # Extract system and user prompts from messages
            system_prompt = messages[0]["content"]
            user_prompt = messages[1]["content"]

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
                    action="context_analysis",
                    model=result.model,
                    tokens=tokens,
                    cost_usd=cost_usd,
                    duration_ms=result.duration_ms,
                    status="success",
                )

            except Exception as e:
                # Log failed LLM call
                obs_log(
                    "llm.call",
                    action="context_analysis",
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
            logger.error(f"Response text (first 200 chars): {response_text[:200]}")
            raise ValueError(f"Invalid JSON response from LLM: {e}") from e
        except Exception as e:
            logger.error(f"LLM context analysis failed: {e}")
            raise

    def _validate_suggestion(self, suggestion: dict[str, Any]) -> bool:
        """Validate a single topic suggestion has required fields.

        Args:
            suggestion: Topic suggestion dict

        Returns:
            True if valid, False otherwise
        """
        required_fields = ["topic", "section", "action", "gap_analysis", "rationale"]

        # Check all required fields present
        for field in required_fields:
            if field not in suggestion:
                logger.warning(f"Suggestion missing required field: {field}")
                return False

        # Validate section value
        if suggestion["section"] not in ["high", "medium", "low"]:
            logger.warning(f"Invalid section value: {suggestion['section']}")
            return False

        # Validate action value
        if suggestion["action"] not in ["expand", "narrow", "add", "split"]:
            logger.warning(f"Invalid action value: {suggestion['action']}")
            return False

        # Check fields are non-empty strings
        if not isinstance(suggestion["topic"], str) or not suggestion["topic"].strip():
            logger.warning("Empty or invalid topic field")
            return False

        return True
