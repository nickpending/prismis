"""Content summarization with rich analysis extraction using LLM."""

import logging
import re
from dataclasses import dataclass
from typing import Any

from .llm_call import bound_content, call_llm_with_circuit_breaker
from .llm_client import extract_json

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
    quotes: list[str]  # Key memorable quotes from the content
    tools: list[str]  # Novel/interesting tools and libraries mentioned
    urls: list[str]  # URLs referenced in the content
    metadata: dict[str, Any]
    # The model's verdict that the content is the piece itself rather than a notice,
    # navigation, error page, bare link or body-less teaser. None when the reply
    # carried no boolean for it: unknown, never false.
    substantive: bool | None = None


# The system prompt's verdict on whether the text is the piece itself, in every mode but
# diff. For a discussion-basis item the article is absent by definition, so the verdict
# is about the discussion instead.
_SUBSTANTIVE_PARAGRAPH = (
    '"substantive" says whether the text contains the piece itself (the article, '
    "post, announcement or release note the title refers to) rather than only "
    "material around it. Set it to true when the piece's own content is present, "
    "however short: a two-sentence release note or a one-paragraph announcement is "
    "substantive, and navigation around it does not change that. Set it to false "
    "when the piece itself is missing: only a link (with or without reader "
    "comments), a notice (JavaScript, cookies, bot check, login, error, paywall), "
    "navigation, interface labels or a site tagline, or a citation or listing "
    "without the work's content."
)

_DISCUSSION_SUBSTANTIVE_PARAGRAPH = (
    '"substantive" says whether the reader discussion in the text says something '
    "substantive about the story the title refers to. The article itself is "
    "unavailable, so its absence does not make the text not substantive. Set it to "
    "true when commenters engage with the story's subject in their own words, "
    "however briefly: an argument, an experience, a correction, an explanation, a "
    "counterpoint or a pointer to relevant work. Set it to false when the discussion "
    "holds nothing about the story: only jokes, one-line reactions, pleasantries, "
    "off-topic chatter, bare links, or a notice."
)

# Placed above the content in the request when the article could not be fetched and the text is the
# item's reader discussion only.
_DISCUSSION_NOTE = (
    "ARTICLE UNAVAILABLE: the article itself could not be fetched. The text below is "
    "reader discussion about the story, not the article. Summarize it as discussion "
    "(what commenters said, argued and pointed to), and never present it as the "
    "article's own content."
)


def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _tools_named_in(tools: list[Any], content: str) -> list[str]:
    """Keep only the tools the content actually names (gh #78: extracted tools the
    content never mentions are invented). Compared with case and punctuation removed,
    so "kotlin-compose" still matches "Kotlin Compose"."""
    haystack = _squash(content)
    kept = [
        t for t in tools if isinstance(t, str) and _squash(t) and _squash(t) in haystack
    ]
    if len(kept) < len(tools):
        logger.debug(f"Dropped {len(tools) - len(kept)} tool(s) not named in content")
    return kept


class ContentSummarizer:
    """Generate summaries and extract structured insights from content."""

    def __init__(self, service_name: str):
        """Initialize the summarizer with the LLM service.

        Args:
            service_name: Name of a [services.<name>] table in config.toml
        """
        self.service_name = service_name

        logger.info(f"ContentSummarizer initialized with service: {self.service_name}")

    def summarize_with_analysis(
        self,
        content: str,
        title: str = "",
        url: str = "",
        source_type: str = "",
        source_name: str = "",
        metadata: dict[str, Any] | None = None,
        content_basis: str | None = None,
    ) -> ContentSummary | None:
        """Generate summary with universal structured analysis.

        Args:
            content: The content to summarize
            title: Optional title of the content
            url: Optional URL of the content
            source_type: Optional source type/category
            content_basis: "discussion" when the article is unavailable and the
                content is the item's reader discussion only; the request then
                says so and asks the substantive verdict about the discussion

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
                content,
                title,
                url,
                source_type,
                source_name,
                metadata or {},
                content_basis,
            )

            bounded_record = bound_content(content).record

            # Determine summarization mode based on content characteristics
            word_count = self._calculate_word_count(content)
            mode = self._get_mode_name(word_count, source_type)
            system_prompt = self._select_system_prompt(
                word_count, source_type, content_basis
            )

            logger.debug(
                f"Content-aware summarization: {word_count} words, "
                f"source_type={source_type}, mode={mode}"
            )

            # Call LLM
            logger.debug(
                f"Calling LLM service {self.service_name} for content analysis"
            )

            result = call_llm_with_circuit_breaker(
                self.service_name, system_prompt, prompt, "summarize"
            )

            # Extract and parse response
            response_text = result.text
            logger.debug("Received structured response from LLM")

            # Parse JSON response (tolerates a ```json-fenced reply)
            parsed = extract_json(response_text)
            if parsed is None:
                logger.error(
                    f"Failed to parse LLM JSON response. "
                    f"First 200 chars: {response_text[:200]!r}"
                )
                return None

            # Validate required fields
            required_fields = [
                "summary",
                "reading_summary",
                "alpha_insights",
                "patterns",
                "quotes",  # Required - core extraction feature
            ]
            for field in required_fields:
                if field not in parsed:
                    logger.error(f"Missing required field '{field}' in LLM response")
                    return None

            # Ensure optional fields exist with defaults
            parsed["tools"] = _tools_named_in(parsed.get("tools") or [], content)
            parsed.setdefault("urls", [])

            # Create ContentSummary object
            return ContentSummary(
                summary=parsed["summary"],
                reading_summary=parsed["reading_summary"],
                alpha_insights=parsed.get("alpha_insights", []),
                patterns=parsed.get("patterns", []),
                quotes=parsed.get("quotes", []),
                tools=parsed.get("tools", []),
                urls=parsed.get("urls", []),
                substantive=(
                    parsed["substantive"]
                    if isinstance(parsed.get("substantive"), bool)
                    else None
                ),
                metadata={
                    "model": result.model,
                    "content_length": len(content),
                    "word_count": word_count,
                    "summarization_mode": mode,
                    **({"content_bounded": bounded_record} if bounded_record else {}),
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

    def _select_system_prompt(
        self, word_count: int, source_type: str, content_basis: str | None = None
    ) -> str:
        """Select appropriate system prompt based on content characteristics.

        Args:
            word_count: Number of words in content
            source_type: Source type (reddit, youtube, rss, file, etc.)
            content_basis: "discussion" swaps the substantive verdict's wording for
                the discussion-based one

        Returns:
            System prompt string for the selected mode
        """
        prompt = self._select_mode_prompt(word_count, source_type)
        if content_basis == "discussion":
            return prompt.replace(
                _SUBSTANTIVE_PARAGRAPH, _DISCUSSION_SUBSTANTIVE_PARAGRAPH
            )
        return prompt

    def _select_mode_prompt(self, word_count: int, source_type: str) -> str:
        """The system prompt for the summarization mode these characteristics select."""
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
  * ## Takeaways - what this means and why it matters. This is the last section; each heading appears exactly once
  * Write clean, readable markdown for web display
  * NO HTML, NO broken formatting, ONLY clean markdown
  * IMPORTANT: Use \\n for newlines (not actual line breaks), escape quotes with \\"

STEP 2: EXTRACT INSIGHTS & PATTERNS
- Alpha insights: Universal truths that exist outside the article but are grounded in it (10-24 items)
- Patterns: Specific methods, frameworks, or approaches described (3-10 items)

STEP 3: EXTRACT MEMORABLE QUOTES (quotes)
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

STEP 4: EXTRACT SUBSTANTIVE TOOLS
A tool is named software a reader could go and get: an application, CLI, library, framework, service or platform. List one when the content is substantively about it: building, using, reviewing, demoing, announcing, promoting, comparing or recommending it, or explaining what it does. Hype counts; a bare mention does not.

If the content's main subject is a tool, list that tool first. Name the tool itself, not its parts or features.

Not tools: AI models themselves (gpt-5, opus, llama-3; apps built on them, like ChatGPT or Claude Code, are tools), a product's features or settings, and names from code (functions, variables, flags).

Copy each name as the content writes it. Maximum 5. Empty when nothing qualifies.

STEP 5: FIND REFERENCED URLS
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
  "reading_summary": "# <title>\\n\\n## Overview\\n<2-3 sentences of context>\\n\\n## Key Points\\n- <takeaway>\\n- <takeaway>\\n\\n## Summary\\n<the longest section: the comprehensive narrative>\\n\\n## Takeaways\\n<what this means and why it matters>",
  "alpha_insights": [
    "Universal principle or truth grounded in the content",
    "Another universal principle from the content"
  ],
  "patterns": [
    "Specific method or approach described",
    "Framework or technique mentioned"
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
  ],
  "substantive": true
}

SUBSTANTIVE:
""" + _SUBSTANTIVE_PARAGRAPH

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

STEP 3: EXTRACT QUOTES
Usually empty for diffs. Only include if changes contain genuinely insightful text.

STEP 4: EXTRACT TOOLS
Only tools that were ADDED or REMOVED in the changes, not tools mentioned in context.

STEP 5: EXTRACT URLs
Only URLs that were ADDED in the changes (lines starting with "+").

STEP 6: JUDGE SUBSTANCE
"substantive" is a boolean. Set it to false when the content is not the piece itself: a JavaScript, cookie or bot-check notice, site navigation or footer text, an error or login page, a bare link, or a teaser with no body. Set it to true otherwise, including a short genuine change.

Return JSON with: summary, reading_summary, alpha_insights, patterns, quotes, tools, urls, substantive, metadata"""

    def _build_prompt(
        self,
        content: str,
        title: str,
        url: str,
        source_type: str,
        source_name: str,
        metadata: dict[str, Any],
        content_basis: str | None = None,
    ) -> str:
        """Build the analysis prompt for the LLM.

        Args:
            content: Article text to analyze
            title: Title of the content
            url: URL of the content
            source_type: Source type/category
            source_name: Name of the source (e.g., @unsupervised-learning, r/rust)
            metadata: Additional metadata (author, subreddit, view count, etc.)
            content_basis: "discussion" adds the note that the article is unavailable
                and the content is reader discussion

        Returns:
            Formatted prompt string
        """
        bounded = bound_content(content)
        logger.debug(
            f"Sending content to LLM for analysis: {bounded.sent_bytes:,} of "
            f"{bounded.total_bytes:,} bytes"
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

        basis_note = (
            f"{_DISCUSSION_NOTE}\n\n" if content_basis == "discussion" else ""
        )

        return f"""Analyze this content and extract structured insights:

Title: {title}
Source Type: {source_type}
{metadata_str}URL: {url}

IMPORTANT: Use the provided metadata above. Do NOT infer or guess author names, channel names, or other metadata not explicitly provided.

CRITICAL FOR URL EXTRACTION: The source URL above ({url}) is where this content came from. DO NOT include it in your extracted URLs - only extract URLs that are referenced WITHIN the content itself.

{basis_note}CONTENT:
{bounded.text}"""
