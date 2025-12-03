"""Content summarization with rich analysis extraction using LLM."""

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import litellm
from litellm import completion_cost

try:
    from .observability import log as obs_log
except ImportError:
    from observability import log as obs_log

logger = logging.getLogger(__name__)


@dataclass
class ContentSummary:
    """Result of content summarization with universal structured analysis."""

    # Brief summary for display (400 chars max)
    summary: str

    # Extended reading summary for in-app reading (2000+ chars, markdown)
    reading_summary: str

    # Universal structured analysis fields (extracted once during ingest)
    alpha_insights: list[str]
    patterns: list[str]
    entities: list[str]  # These are the "topics" - key concepts/technologies
    quotes: list[str]  # Key memorable quotes from the content
    tools: list[str]  # Novel/interesting tools and libraries mentioned
    urls: list[str]  # URLs referenced in the content
    metadata: dict[str, Any]


class ContentSummarizer:
    """Generate summaries and extract structured insights from content."""

    def __init__(self, config: dict[str, Any] | None = None):
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
        metadata: dict[str, Any] | None = None,
    ) -> ContentSummary | None:
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

            # Determine summarization mode based on content characteristics
            word_count = self._calculate_word_count(content)
            mode = self._get_mode_name(word_count, source_type)
            system_prompt = self._select_system_prompt(word_count, source_type)

            logger.debug(
                f"Content-aware summarization: {word_count} words, "
                f"source_type={source_type}, mode={mode}"
            )

            # Call LLM
            logger.debug(f"Calling {self.model} for content analysis")

            # Build kwargs for litellm call
            kwargs = {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": system_prompt,
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
                    action="summarize",
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
                    action="summarize",
                    model=self.model,
                    status="error",
                    error=str(e),
                    duration_ms=duration_ms,
                )
                raise  # Re-raise to preserve existing error handling

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
                    "word_count": word_count,
                    "summarization_mode": mode,
                },
            )

        except Exception as e:
            logger.error(f"LLM summarization failed: {e}", exc_info=True)
            # Re-raise to stop processing completely per requirements
            raise

    def _calculate_word_count(self, content: str) -> int:
        """Calculate word count from content.

        Args:
            content: Text content to count words in

        Returns:
            Number of words in content
        """
        if not content or not content.strip():
            return 0
        return len(content.split())

    def _get_mode_name(self, word_count: int, source_type: str) -> str:
        """Get the summarization mode name for logging.

        Args:
            word_count: Number of words in content
            source_type: Source type (reddit, youtube, rss, file, etc.)

        Returns:
            Mode name: 'brief', 'detailed', 'diff', or 'standard'
        """
        if source_type == "file":
            return "diff"
        elif source_type == "reddit" and word_count < 300:
            return "brief"
        elif source_type == "youtube" and word_count > 5000:
            return "detailed"
        else:
            return "standard"

    def _select_system_prompt(self, word_count: int, source_type: str) -> str:
        """Select appropriate system prompt based on content characteristics.

        Args:
            word_count: Number of words in content
            source_type: Source type (reddit, youtube, rss, file, etc.)

        Returns:
            System prompt string for the selected mode
        """
        # Diff mode: File sources (content is unified diff)
        if source_type == "file":
            return self._get_diff_system_prompt()

        # Brief mode: Short Reddit posts (< 300 words)
        elif source_type == "reddit" and word_count < 300:
            return self._get_brief_system_prompt()

        # Detailed mode: Long YouTube videos (> 5000 words)
        elif source_type == "youtube" and word_count > 5000:
            return self._get_detailed_system_prompt()

        # Standard mode: Everything else (default)
        else:
            return self._get_system_prompt()

    def _get_system_prompt(self) -> str:
        """Get the standard system prompt for content analysis (current behavior)."""
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

STEP 3: EXTRACT HASHTAG-STYLE TAGS (entities)
Think Twitter hashtags or Instagram tags - simple, searchable, one concept each.
Extract 3-5 essential tags. IMPORTANT: 3 great tags is BETTER than 5 mediocre ones.
Do NOT force 5 tags - only include tags that truly matter.

HASHTAG MINDSET:
- Think breadth, not depth - what's this REALLY about?
- Single words preferred, hyphens OK for compound concepts
- Choose the essence, not the full description
- Less is more - 3 great tags better than 5 mediocre ones

SIMPLIFICATION RULES:
- "ai language models" → "ai" (not ai-language-models)
- "supply chain attack" → "security" or "supply-chain"
- "national institutes of health" → "health" or "nih"
- "16-digit numerical password" → "security"
- "adaptive security appliance" → "security"
- "biomedical research funding" → "research" or "biomedical"
- "artificial intelligence ethics" → "ai" and "ethics" (separate tags)

GOOD hashtag examples:
- AI article: ["ai", "chatgpt", "ethics"]
- Security breach: ["security", "ransomware", "cisco"]
- Health research: ["health", "research", "nih"]
- Dev tutorial: ["python", "tutorial", "web"]

BAD examples (too complex):
- ["ai language models", "ethical considerations", "societal impact"]
- ["supply chain attack", "open source security", "npm ecosystem"]
- ["national institutes of health", "biomedical research", "federal funding"]

VALIDATION RULES (MUST FOLLOW):
- NO SPACES EVER. If multi-word: either hyphenate OR simplify to one word
  • "cloud code" → "claude-code" OR just "claude"
  • "software development" → "software-development" OR just "software"
  • "agentic tool use" → "agentic-tools" OR just "agentic"
- NO DUPLICATES. If you include "gemini", don't add "llm-gemini"
- Every tag: lowercase letters and hyphens only
- If ANY tag contains spaces, the extraction has FAILED

CRITICAL: Pick the ESSENCE, not the description.
If unsure, go broader and simpler. All lowercase, no spaces.

STEP 4: EXTRACT MEMORABLE QUOTES (quotes)
Find 0-3 quotes that are GENUINELY INSIGHTFUL. Many articles have NO quotable insights - that's OK.

QUALITY CRITERIA:
- ONLY extract quotes that would be worth sharing or remembering
- Look for: counterintuitive insights, profound observations, surprising facts, expert wisdom
- SKIP: basic questions, obvious statements, routine facts, setup sentences
- If there's nothing profound or memorable, return empty array []

VERBATIM REQUIREMENT:
- MUST be exact text from the content (copy-paste, not paraphrased)
- Include enough context to make sense standalone (1-3 sentences max)
- Never write "The author states..." or summarize - use their exact words

EXAMPLES of QUOTE-WORTHY insights:
✅ "The best code is no code, because code is a liability that requires maintenance"
✅ "Context is that which is scarce. Compute is abundant, but knowing what to compute is hard"
✅ "Performance improvements of 10x happen at the architecture level, not the code level"

EXAMPLES of NON-QUOTES (never extract these):
❌ "I want to use Claude in Cursor" (basic question)
❌ "Has anyone found a way to turn it off?" (mundane question)
❌ "This process takes about 5 minutes" (routine fact)
❌ "Let me explain how this works" (setup sentence)

REMEMBER: Better to have zero quotes than to extract mundane sentences. Only the gems.

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

    def _get_brief_system_prompt(self) -> str:
        """Get brief system prompt for short content (Reddit <300 words).

        Returns standard prompt with modified reading_summary instruction.
        """
        standard = self._get_system_prompt()
        # Replace reading_summary instruction for brief mode
        return standard.replace(
            "- Reading summary: approximately 10-15% of original content length (minimum 2000 chars), comprehensive MARKDOWN:",
            "- Reading summary: Minimal - approximately 500-800 chars. Focus on core points only since original is already short:",
        )

    def _get_detailed_system_prompt(self) -> str:
        """Get detailed system prompt for long content (YouTube >5000 words).

        Returns standard prompt with modified reading_summary instruction.
        """
        standard = self._get_system_prompt()
        # Replace reading_summary instruction for detailed mode
        return standard.replace(
            "- Reading summary: approximately 10-15% of original content length (minimum 2000 chars), comprehensive MARKDOWN:",
            "- Reading summary: Comprehensive - approximately 20-25% of original content length. Provide richer detail with deeper analysis since source is extensive:",
        )

    def _get_diff_system_prompt(self) -> str:
        """Get diff-aware system prompt for file sources (content is unified diff).

        Focuses analysis on what actually changed, not surrounding context.
        """
        return """You are an expert at analyzing unified diffs. The content is a UNIFIED DIFF showing changes to a file.

CRITICAL: You MUST respond with ONLY valid JSON. Start with { and end with }. No preamble.

UNDERSTANDING UNIFIED DIFF FORMAT:
- Lines starting with "---" and "+++" are file headers (ignore these)
- Lines starting with "@@" show line numbers where changes occur
- Lines starting with "-" are REMOVED content (old version)
- Lines starting with "+" are ADDED content (new version)
- Lines without +/- prefix are CONTEXT (unchanged lines shown for reference)

YOUR TASK: Analyze ONLY what actually changed (+ and - lines), NOT the context lines.
Context lines are just there to show where changes occurred - do NOT summarize them as if they were new content.

STEP 1: CREATE SUMMARIES
- Summary: 400 chars max. Describe what CHANGED (e.g., "Updated documentation URLs from docs.claude.com to code.claude.com across 6 sections")
- Reading summary: MARKDOWN format describing:
  * # What Changed - brief overview of the change type
  * ## Changes Made - specific changes with before/after when useful
  * ## Impact - what this means for users/developers
  * IMPORTANT: Focus on the ACTUAL changes, not the surrounding context

STEP 2: EXTRACT INSIGHTS & PATTERNS
- Alpha insights: What do these changes reveal? (e.g., "Documentation migration indicates platform consolidation")
- Patterns: What patterns appear in the changes? (e.g., "Consistent URL scheme migration")

STEP 3: EXTRACT TAGS (entities)
Extract 3-5 tags about what changed. Examples:
- URL migration: ["documentation", "url-migration"]
- Bug fix: ["bugfix", "error-handling"]
- Feature addition: ["feature", "api"]

STEP 4: EXTRACT QUOTES
Usually empty for diffs. Only include if changes contain genuinely insightful text.

STEP 5: EXTRACT TOOLS
Only tools that were ADDED or REMOVED in the changes, not tools mentioned in context.

STEP 6: EXTRACT URLs
Only URLs that were ADDED in the changes (lines starting with "+").

Return JSON with: summary, reading_summary, alpha_insights, patterns, entities, quotes, tools, urls, metadata"""

    def _build_prompt(
        self,
        content: str,
        title: str,
        url: str,
        source_type: str,
        source_name: str,
        metadata: dict[str, Any],
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
