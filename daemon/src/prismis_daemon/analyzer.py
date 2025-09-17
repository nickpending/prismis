"""LLM content analyzer for priority and topic extraction."""

import json
import logging
from typing import Dict, Any, Optional

import litellm

logger = logging.getLogger(__name__)


class Analyzer:
    """Analyzes content using LLM to determine priority and extract summaries.

    Uses LiteLLM for provider abstraction, supporting OpenAI, Anthropic, Ollama, etc.
    Analyzes content against user context to determine HIGH/MEDIUM/LOW priority.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the analyzer with LLM configuration.

        Args:
            config: Optional configuration dict with:
                - model: LLM model name (e.g., gpt-4o-mini, ollama/llama2, claude-3-haiku)
                - api_key: API key for the provider
                - provider: Provider name (openai, ollama, anthropic, groq)
                - api_base: Optional API base URL (for Ollama)
                - temperature: Optional temperature setting
        """
        self.config = config or {}

        # Model must be provided in config
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

        # Optional: Set other LiteLLM settings
        if "temperature" in self.config:
            self.temperature = self.config["temperature"]
        else:
            self.temperature = 0.3  # Lower temperature for consistent analysis

        # Optional: Configure LiteLLM settings
        litellm.drop_params = True  # Drop unsupported params instead of erroring

        logger.info(
            f"Analyzer initialized with {provider} provider, model: {self.model}"
        )

    def analyze(self, content: str, context: str) -> Dict[str, Any]:
        """Analyze content against user context to determine priority and topics.

        Args:
            content: The article/content text to analyze
            context: User's personal context (high/medium/low priority topics)

        Returns:
            Dictionary with:
                - summary: 2-3 sentence summary
                - priority: 'high', 'medium', or 'low'
                - topics: List of identified topics
                - relevance_score: Float 0-1 indicating relevance

        Raises:
            Exception: If LLM call fails (wrapped with context)
        """
        try:
            # Build the analysis prompt
            prompt = self._build_prompt(content, context)

            # Call LLM with structured output request
            logger.debug(f"Calling {self.model} for content analysis")

            # Build kwargs for litellm call
            kwargs = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a content analysis assistant. Analyze content and return structured JSON.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "temperature": self.temperature,
                "response_format": {"type": "json_object"},  # Request JSON response
            }

            # Add credentials if provided
            if self.api_key:
                kwargs["api_key"] = self.api_key
            if self.api_base:
                kwargs["api_base"] = self.api_base

            response = litellm.completion(**kwargs)

            # Extract and parse response
            response_text = response.choices[0].message.content
            logger.debug(f"LLM response: {response_text[:200]}...")

            # Parse JSON response
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM JSON response: {e}")
                # Fallback to basic parsing
                result = self._fallback_parse(response_text)

            # Validate and normalize result
            result = self._validate_result(result)

            logger.info(
                f"Analysis complete - Priority: {result['priority']}, Score: {result['relevance_score']}"
            )

            return result

        except Exception as e:
            raise Exception(f"Failed to analyze content: {e}") from e

    def _build_prompt(self, content: str, context: str) -> str:
        """Build the analysis prompt for the LLM.

        Args:
            content: Article text to analyze
            context: User's priority context

        Returns:
            Formatted prompt string
        """
        # Truncate content if too long (to manage token usage)
        max_content_length = 3000
        if len(content) > max_content_length:
            content = content[:max_content_length] + "..."

        prompt = f"""Analyze the following content based on the user's interests and priorities.

CONTENT TO ANALYZE:
{content}

USER'S INTERESTS AND PRIORITIES:
{context}

Please analyze this content and return a JSON object with the following structure:
{{
    "summary": "A 2-3 sentence summary with SPECIFIC insights, numbers, and technical details",
    "priority": "high" or "medium" or "low",
    "topics": ["topic1", "topic2", "topic3"],
    "relevance_score": 0.0 to 1.0,
    "tools": ["tool1", "tool2"],
    "urls": ["https://example.com", "https://github.com/repo"]
}}

CRITICAL SUMMARY REQUIREMENTS:
- Extract SPECIFIC metrics, numbers, percentages, and measurable claims
- Include CONCRETE technical details, tool names, version numbers, methodologies
- State ACTUAL findings, results, or conclusions - not generic descriptions
- Avoid phrases like "discusses", "explores", "covers" - say what it CLAIMS or REVEALS
- Example GOOD: "GPT-4 achieves 86.4% on MMLU benchmark, 25% improvement over GPT-3.5. Uses 1.76 trillion parameters with mixture-of-experts architecture."
- Example BAD: "Discusses improvements in GPT-4 performance. Explores new architectures."

TOOLS EXTRACTION:
- Extract names of software, libraries, frameworks, or tools mentioned
- Include version numbers if specified (e.g., "React 18.2", "Python 3.11")
- Focus on technical tools, not generic terms
- Examples: "PostgreSQL", "Docker", "FastAPI", "pytest", "Rust", "TensorFlow"

URLs EXTRACTION:
- Extract actual URLs mentioned or linked in the content
- Include GitHub repos, documentation sites, project homepages
- Clean up tracking parameters if present
- Maximum 5 most relevant URLs
- Do NOT include the source article's own URL

Priority Guidelines:
- HIGH: Directly matches high priority topics, score > 0.8
- MEDIUM: Matches medium priority topics, score > 0.5
- LOW: Somewhat relevant but not priority, score > 0.3
- Skip if score < 0.3 or matches "Not Interested" topics

Focus on extracting actionable, specific information the user can use or reference."""

        return prompt

    def _validate_result(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and normalize the analysis result.

        Args:
            result: Raw result from LLM

        Returns:
            Validated and normalized result dict
        """
        # Ensure all required fields exist
        validated = {
            "summary": result.get("summary", "No summary available"),
            "priority": result.get("priority", "low").lower(),
            "topics": result.get("topics", []),
            "relevance_score": float(result.get("relevance_score", 0.5)),
            "tools": result.get("tools", []),
            "urls": result.get("urls", []),
        }

        # Validate priority value
        if validated["priority"] not in ["high", "medium", "low"]:
            logger.warning(
                f"Invalid priority '{validated['priority']}', defaulting to 'low'"
            )
            validated["priority"] = "low"

        # Ensure relevance_score is in range
        validated["relevance_score"] = max(0.0, min(1.0, validated["relevance_score"]))

        # Ensure topics is a list
        if not isinstance(validated["topics"], list):
            validated["topics"] = []

        # Ensure tools is a list
        if not isinstance(validated["tools"], list):
            validated["tools"] = []

        # Ensure urls is a list
        if not isinstance(validated["urls"], list):
            validated["urls"] = []

        return validated

    def _fallback_parse(self, response_text: str) -> Dict[str, Any]:
        """Fallback parsing if JSON parsing fails.

        Args:
            response_text: Raw text response from LLM

        Returns:
            Basic result dict with defaults
        """
        logger.warning("Using fallback parsing for LLM response")

        # Try to extract priority
        priority = "low"
        if "high" in response_text.lower():
            priority = "high"
        elif "medium" in response_text.lower():
            priority = "medium"

        return {
            "summary": response_text[:200]
            if len(response_text) > 200
            else response_text,
            "priority": priority,
            "topics": [],
            "relevance_score": 0.5,
            "tools": [],
            "urls": [],
        }
