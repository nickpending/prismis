"""Content summarization with rich analysis extraction using LLM."""

import json
import logging
from dataclasses import dataclass
from typing import Dict, Any, List, Optional

import litellm

logger = logging.getLogger(__name__)


@dataclass
class ContentSummary:
    """Result of content summarization with universal structured analysis."""

    # Brief summary for display (400 chars max)
    summary: str

    # Extended reading summary for in-app reading (2000+ chars, markdown)
    reading_summary: str

    # Universal structured analysis fields (extracted once during ingest)
    alpha_insights: List[str]
    patterns: List[str]
    entities: List[str]  # These are the "topics" - key concepts/technologies
    quotes: List[str]  # Key memorable quotes from the content
    tools: List[str]  # Novel/interesting tools and libraries mentioned
    urls: List[str]  # URLs referenced in the content
    metadata: Dict[str, Any]


class ContentSummarizer:
    """Generate summaries and extract structured insights from content."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """Initialize the summarizer with LLM configuration.

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
        litellm.drop_params = True  # Drop unsupported params instead of erroring
        self.temperature = 0.3  # Lower temperature for consistent analysis

        logger.info(f"ContentSummarizer initialized with model: {self.model}")

    def summarize_with_analysis(
        self,
        content: str,
        title: str = "",
        url: str = "",
        source_type: str = "",
        source_name: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[ContentSummary]:
        """Generate summary with universal structured analysis.

        Args:
            content: The content to summarize
            title: Optional title of the content
            url: Optional URL of the content
            source_type: Optional source type/category

        Returns:
            ContentSummary with summary and structured analysis, or None if fails
        """
        if not content or not content.strip():
            logger.debug("Empty content provided for summarization")
            return None

        logger.debug(
            f"Summarizing content with analysis (length: {len(content):,} chars, "
            f"title: {title[:50] if title else 'No title'})"
        )

        try:
            # Build the analysis prompt (from legacy system)
            prompt = self._build_prompt(
                content, title, url, source_type, source_name, metadata or {}
            )

            # Call LLM
            logger.debug(f"Calling {self.model} for content analysis")

            # Build kwargs for litellm call
            kwargs = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": self._get_system_prompt(),
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
            logger.debug("Received structured response from LLM")

            # Parse JSON response
            try:
                result = json.loads(response_text)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse LLM JSON response: {e}")
                return None

            # Validate required fields
            required_fields = [
                "summary",
                "reading_summary",
                "alpha_insights",
                "patterns",
                "entities",
                "quotes",  # Required - core extraction feature
            ]
            for field in required_fields:
                if field not in result:
                    logger.error(f"Missing required field '{field}' in LLM response")
                    return None

            # Ensure optional fields exist with defaults
            result.setdefault("tools", [])
            result.setdefault("urls", [])

            # Create ContentSummary object
            return ContentSummary(
                summary=result["summary"],
                reading_summary=result["reading_summary"],
                alpha_insights=result.get("alpha_insights", []),
                patterns=result.get("patterns", []),
                entities=result.get("entities", []),
                quotes=result.get("quotes", []),
                tools=result.get("tools", []),
                urls=result.get("urls", []),
                metadata={
                    "model": self.model,
                    "content_length": len(content),
                },
            )

        except Exception as e:
            logger.error(f"LLM summarization failed: {e}", exc_info=True)
            # Re-raise to stop processing completely per requirements
            raise

    def _get_system_prompt(self) -> str:
        """Get the system prompt for content analysis."""
        return """You are an expert content analyst. Follow these steps SEQUENTIALLY.

CRITICAL: You MUST respond with ONLY valid JSON. DO NOT include any text, explanation, or preamble before or after the JSON. Start directly with { and end directly with }. No "Here is the analysis:" or similar phrases. ONLY JSON.

STEP 1: CREATE SUMMARIES
- Summary: 400 chars max, capture key information for card display
- Reading summary: approximately 10-15% of original content length (minimum 2000 chars), comprehensive MARKDOWN:
  * MUST use proper markdown formatting with # headers and ## subheaders
  * Start with # Title matching the content
  * ## Overview section - brief context/background (2-3 sentences)
  * ## Key Points - bullet list of main takeaways
  * ## Summary - THE MAIN SECTION! Comprehensive narrative covering what was discussed, arguments made, flow of ideas. This should be substantive enough that someone could skip the original unless they want full nuance.
  * ## Takeaways - what this means and why it matters
  * Write clean, readable markdown for web display
  * NO HTML, NO broken formatting, ONLY clean markdown
  * IMPORTANT: Use \\n for newlines (not actual line breaks), escape quotes with \\"

STEP 2: EXTRACT INSIGHTS & PATTERNS
- Alpha insights: Universal truths that exist outside the article but are grounded in it (10-24 items)
- Patterns: Specific methods, frameworks, or approaches described (3-10 items)

STEP 3: IDENTIFY TOP 5 ENTITIES
Extract EXACTLY the TOP 5 MOST SIGNIFICANT entities for content discovery.
Think: "What are the 5 most important things someone would search for to find similar content?"

INCLUDE THESE TYPES (pick the 5 most relevant):
* Major technologies/frameworks (e.g., "React", "Kubernetes", "Python")
* Companies/organizations (e.g., "Google", "OpenAI", "Microsoft")
* Key concepts/methodologies (e.g., "ML", "AI", "Agile", "DevOps")
* Well-known tools/platforms (e.g., "GitHub", "AWS", "Docker")
* Important people mentioned by name (e.g., "Elon Musk", "Sam Altman")

NEVER INCLUDE:
* File names (README.md, config.json, CLAUDE.md, package.json)
* Commands (/init, --help, npm install, git commit)
* Code snippets or function names
* Generic words (file, user, system, document)
* Minor features or UI elements
* Anything with file extensions (.md, .json, .py, .js)

Be ruthlessly selective - only the 5 MOST searchable, significant entities.
Format all entities in lowercase except for common abbreviations (AI, ML, AWS, etc.).
Use common abbreviations: "AI" instead of "artificial intelligence", "ML" instead of "machine learning".

STEP 4: FIND ACTUAL QUOTES
Extract 1-3 MEANINGFUL quotes that capture profound insights or ideas.

MUST be actual verbatim quotes from the content (not paraphrased)
Look for unique perspectives, counterintuitive observations, or key arguments
Each quote should be max 3 sentences
Select quotes that capture the essence of what makes this content valuable

Examples of GOOD quotes:
- "The best code is no code, because code is a liability that requires maintenance and understanding"
- "Context is that which is scarce. Compute is abundant, but knowing what to compute is the hard part"
- "The fundamental problem of communication is not transmitting information but establishing shared meaning"

DO NOT select mundane facts or obvious statements
Prefer wisdom, insights, and thought-provoking observations
COPY EXACT TEXT - do NOT paraphrase or create summaries like "The post encourages..."

STEP 5: EXTRACT SUBSTANTIVE TOOLS
Extract tools that are discussed SUBSTANTIVELY in the content.

Only include tools that meet these criteria:
- The article explains what problem they solve or why they're useful
- The author has actually used them or provides meaningful insight about them
- They are central to the article's discussion (not just mentioned in passing)
- The content provides enough context for a reader to understand WHY they'd want to investigate this tool

Examples of substantive discussion:
- "We switched to X because Y wasn't handling Z use case, and here's what we learned..."
- "Tool X solves the problem of Y by doing Z differently than existing approaches..."
- "I've been experimenting with X and found it reduces Y by 50%..."

DO NOT include tools that are:
- Just mentioned in a list without context
- Part of standard tech stacks unless specifically discussed
- Referenced without explanation of their purpose or benefits
- Obvious or well-known unless the article provides new insights about them

Maximum 5 tools to keep focused on the most valuable ones
Format: lowercase unless it's a proper name

STEP 6: FIND REFERENCED URLS
Extract actual URLs referenced or linked WITHIN the content.

Include GitHub repos, documentation sites, project homepages that are referenced
Clean up tracking parameters if present
Maximum 5 most relevant URLs
CRITICAL: Do NOT include the source article's own URL (the URL where this content came from)
Only extract URLs that are mentioned, linked to, or referenced within the article text
Do NOT make up URLs - only extract ones actually mentioned in the content

OUTPUT FORMAT:
{
  "summary": "Brief summary of the article's main points",
  "reading_summary": "# Title Here\\n\\n## Overview\\nBrief context and background (2-3 sentences)\\n\\n## Key Points\\n- Main takeaway 1\\n- Main takeaway 2\\n- Main takeaway 3\\n\\n## Summary\\nThis is the MEAT of the content. Write a comprehensive narrative that covers what was actually discussed, the arguments made, the flow of ideas, and important details. Someone should be able to read this and understand the content without needing the original (unless they want full nuance). This should be the longest section.\\n\\n## Takeaways\\nWhat this means and why it matters...",
  "alpha_insights": [
    "Universal principle or truth grounded in the content",
    "Another universal principle from the content"
  ],
  "patterns": [
    "Specific method or approach described",
    "Framework or technique mentioned"
  ],
  "entities": [
    "Most significant entity #1",
    "Most significant entity #2",
    "Most significant entity #3",
    "Most significant entity #4",
    "Most significant entity #5"
  ],
  "quotes": [
    "First memorable quote that captures key insight",
    "Second impactful quote with specific data"
  ],
  "tools": [
    "tool1",
    "tool2"
  ],
  "urls": [
    "https://example.com/referenced-link",
    "https://github.com/project"
  ]
}"""

    def _build_prompt(
        self,
        content: str,
        title: str,
        url: str,
        source_type: str,
        source_name: str,
        metadata: Dict[str, Any],
    ) -> str:
        """Build the analysis prompt for the LLM.

        Args:
            content: Article text to analyze
            title: Title of the content
            url: URL of the content
            source_type: Source type/category
            source_name: Name of the source (e.g., @unsupervised-learning, r/rust)
            metadata: Additional metadata (author, subreddit, view count, etc.)

        Returns:
            Formatted prompt string
        """
        # Use full content - no truncation for comprehensive analysis
        logger.debug(
            f"Sending full content to LLM for analysis: {len(content):,} characters"
        )

        # Build metadata string
        metadata_str = ""
        if source_name:
            metadata_str += f"Source Name: {source_name}\n"
        if metadata:
            if metadata.get("author"):
                metadata_str += f"Author: {metadata['author']}\n"
            if metadata.get("subreddit"):
                metadata_str += f"Subreddit: r/{metadata['subreddit']}\n"
            if metadata.get("view_count"):
                metadata_str += f"View Count: {metadata['view_count']:,}\n"

        return f"""Analyze this content and extract structured insights:

Title: {title}
Source Type: {source_type}
{metadata_str}URL: {url}

IMPORTANT: Use the provided metadata above. Do NOT infer or guess author names, channel names, or other metadata not explicitly provided.

CRITICAL FOR URL EXTRACTION: The source URL above ({url}) is where this content came from. DO NOT include it in your extracted URLs - only extract URLs that are referenced WITHIN the content itself.

CONTENT:
{content}"""
