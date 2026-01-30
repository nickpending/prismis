"""Auto-update context.md based on user feedback votes."""

import json
import logging
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import litellm
from litellm import completion_cost

try:
    from .config import Config
    from .storage import Storage
    from .observability import log as obs_log
except ImportError:
    from config import Config
    from storage import Storage
    from observability import log as obs_log

logger = logging.getLogger(__name__)


class ContextAutoUpdater:
    """Auto-updates context.md based on user feedback votes."""

    def __init__(self, config: Config, storage: Storage):
        """Initialize the auto-updater.

        Args:
            config: Application configuration
            storage: Storage instance for database access
        """
        self.config = config
        self.storage = storage
        
        # Get paths
        self.context_path = self._get_context_path()
        self.backup_dir = self.context_path.parent / "context_backups"
        
        # LLM settings from config
        self.model = config.llm_model
        self.api_key = config.llm_api_key
        self.api_base = config.llm_api_base
        
        # Configure LiteLLM
        litellm.drop_params = True
        self.temperature = 0.3
        
        logger.info(f"ContextAutoUpdater initialized with model: {self.model}")

    def _get_context_path(self) -> Path:
        """Get the path to context.md from XDG config."""
        import os
        xdg_config_home = os.environ.get(
            "XDG_CONFIG_HOME", str(Path.home() / ".config")
        )
        return Path(xdg_config_home) / "prismis" / "context.md"

    def _get_last_update_path(self) -> Path:
        """Get the path to the last update timestamp file."""
        return self.context_path.parent / ".context_last_update"

    def should_update(self) -> tuple[bool, str]:
        """Check if an update should run.

        Returns:
            Tuple of (should_update, reason)
        """
        # Check if enabled
        if not self.config.context_auto_update_enabled:
            return False, "Auto-update disabled in config"

        # Check if enough time has passed
        last_update_file = self._get_last_update_path()
        if last_update_file.exists():
            try:
                last_update_ts = float(last_update_file.read_text().strip())
                days_since = (time.time() - last_update_ts) / (24 * 60 * 60)
                if days_since < self.config.context_auto_update_interval_days:
                    return False, f"Only {days_since:.1f} days since last update (need {self.config.context_auto_update_interval_days})"
            except (ValueError, OSError) as e:
                logger.warning(f"Could not read last update timestamp: {e}")

        # Check if enough votes exist
        stats = self.storage.get_feedback_statistics(
            since_days=self.config.context_auto_update_interval_days
        )
        total_votes = stats.get("totals", {}).get("total_votes", 0)
        
        if total_votes < self.config.context_auto_update_min_votes:
            return False, f"Only {total_votes} votes (need {self.config.context_auto_update_min_votes})"

        return True, f"Ready: {total_votes} votes, interval passed"

    def backup_context(self) -> Path | None:
        """Create a timestamped backup of current context.md.

        Returns:
            Path to backup file, or None if context.md doesn't exist
        """
        if not self.context_path.exists():
            logger.warning("No context.md to backup")
            return None

        # Create backup directory
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        # Create timestamped backup
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"context_{timestamp}.md"
        
        shutil.copy2(self.context_path, backup_path)
        logger.info(f"Backed up context.md to {backup_path}")

        # Prune old backups
        self._prune_old_backups()

        return backup_path

    def _prune_old_backups(self) -> None:
        """Remove old backups, keeping only the configured number."""
        if not self.backup_dir.exists():
            return

        backups = sorted(self.backup_dir.glob("context_*.md"), reverse=True)
        
        for old_backup in backups[self.config.context_backup_count:]:
            old_backup.unlink()
            logger.debug(f"Removed old backup: {old_backup}")

    def _get_voted_articles(self) -> list[dict[str, Any]]:
        """Get all voted articles within the update interval.

        Returns:
            List of article metadata with votes
        """
        # Use storage to get content with user_feedback
        # We need to query voted items directly
        try:
            # Get all content with votes in the time window
            voted_items = []
            
            # Query upvoted items
            up_items = self.storage.get_content_by_feedback(
                feedback="up",
                since_days=self.config.context_auto_update_interval_days
            )
            for item in up_items:
                voted_items.append(self._format_article(item, "up"))

            # Query downvoted items
            down_items = self.storage.get_content_by_feedback(
                feedback="down", 
                since_days=self.config.context_auto_update_interval_days
            )
            for item in down_items:
                voted_items.append(self._format_article(item, "down"))

            return voted_items

        except Exception as e:
            logger.error(f"Failed to get voted articles: {e}")
            return []

    def _format_article(self, item: dict[str, Any], vote: str) -> dict[str, Any]:
        """Format an article for the LLM prompt.

        Args:
            item: Raw article data from storage
            vote: "up" or "down"

        Returns:
            Formatted article metadata
        """
        # Extract matched interests from analysis JSON
        matched_interests = []
        if item.get("analysis"):
            try:
                analysis = json.loads(item["analysis"]) if isinstance(item["analysis"], str) else item["analysis"]
                matched_interests = analysis.get("matched_interests", [])
            except (json.JSONDecodeError, TypeError):
                pass

        return {
            "title": item.get("title", "Untitled"),
            "summary": item.get("summary", ""),
            "source_name": item.get("source_name", "Unknown"),
            "source_type": item.get("source_type", ""),
            "original_priority": item.get("priority", "none"),
            "matched_interests": matched_interests,
            "vote": vote,
            "published_at": str(item.get("published_at", "")),
        }

    def _build_prompt(
        self,
        current_context: str,
        voted_articles: list[dict[str, Any]],
        stats: dict[str, Any],
    ) -> list[dict[str, str]]:
        """Build the LLM prompt for context update.

        Args:
            current_context: Current context.md content
            voted_articles: List of voted article metadata
            stats: Feedback statistics

        Returns:
            List of messages for the LLM
        """
        # Format voted articles
        articles_json = json.dumps(voted_articles, indent=2)

        # Extract key stats
        topics_upvoted = stats.get("topics_upvoted", [])[:10]  # Top 10
        topics_downvoted = stats.get("topics_downvoted", [])[:10]
        
        # Format topics for readability
        upvoted_str = ", ".join([f"{t['topic']} ({t['count']})" for t in topics_upvoted]) if topics_upvoted else "None"
        downvoted_str = ", ".join([f"{t['topic']} ({t['count']})" for t in topics_downvoted]) if topics_downvoted else "None"

        # Get trusted/distrusted sources
        by_source = stats.get("by_source", [])
        trusted = [s["source_name"] for s in by_source if s.get("upvote_ratio", 0) >= 0.7][:5]
        distrusted = [s["source_name"] for s in by_source if s.get("upvote_ratio", 0) <= 0.3 and s.get("total", 0) >= 3][:5]
        
        trusted_str = ", ".join(trusted) if trusted else "None"
        distrusted_str = ", ".join(distrusted) if distrusted else "None"

        system_prompt = """You are a context.md updater for a content intelligence system.

STRICT OUTPUT RULES:
1. Output ONLY valid Markdown — no explanations, no code blocks, no preamble
2. Preserve EXACT section headers:
   - "## High Priority Topics"
   - "## Medium Priority Topics"
   - "## Low Priority Topics"
   - "## Not Interested"
3. Each topic is a bullet point starting with "- "
4. NEVER remove topics entirely — only move between sections
5. PRESERVE the user's writing style (examine their phrasing, specificity, tone)
6. If the current context.md has custom sections, PRESERVE them unchanged
7. Keep topics concise — match the length of existing topics

TOPIC MOVEMENT RULES:
- Upvoted topics: Promote (LOW→MED, MED→HIGH) or add if new
- Downvoted topics: Demote (HIGH→MED, MED→LOW→Not Interested)
- Multiple upvotes on same topic = stronger signal for promotion
- Topics with BOTH up and down votes = leave unchanged (conflicting signal)

ADDING NEW TOPICS:
- Only add if 2+ upvoted articles share a clear theme not in current context
- Match the user's existing topic style exactly
- Place in appropriate section based on vote strength"""

        user_prompt = f"""Current context.md:
---
{current_context}
---

Voted articles (last {self.config.context_auto_update_interval_days} days):
{articles_json}

Vote summary:
- Topics frequently upvoted: {upvoted_str}
- Topics frequently downvoted: {downvoted_str}
- Trusted sources: {trusted_str}
- Distrusted sources: {distrusted_str}

Generate the complete updated context.md."""

        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

    def _validate_context_md(self, content: str) -> tuple[bool, str]:
        """Validate the generated context.md is well-formed.

        Args:
            content: Generated content to validate

        Returns:
            Tuple of (is_valid, error_message)
        """
        required_sections = [
            "## High Priority Topics",
            "## Medium Priority Topics",
            "## Low Priority Topics",
        ]

        for section in required_sections:
            if section not in content:
                return False, f"Missing required section: {section}"

        # Check it's not empty/too short
        if len(content) < 100:
            return False, f"Content too short ({len(content)} chars)"

        # Check bullet points exist
        if "- " not in content:
            return False, "No bullet points found"

        # Check it doesn't contain code blocks or explanations
        if "```" in content:
            return False, "Contains code blocks (should be pure markdown)"

        return True, "Valid"

    def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Call LiteLLM to generate updated context.

        Args:
            messages: Messages to send to the LLM

        Returns:
            Generated context.md content

        Raises:
            Exception: If LLM call fails
        """
        logger.debug(f"Calling {self.model} for context update")

        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
        }

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        start_time = time.time()

        try:
            response = litellm.completion(**kwargs)
            duration_ms = int((time.time() - start_time) * 1000)

            # Extract content
            content = response.choices[0].message.content.strip()

            # Log for observability
            tokens = {
                "prompt": response.usage.prompt_tokens if response.usage else 0,
                "completion": response.usage.completion_tokens if response.usage else 0,
            }

            try:
                cost = completion_cost(completion_response=response)
            except Exception:
                cost = 0.0

            obs_log(
                operation="context_auto_update",
                duration_ms=duration_ms,
                tokens=tokens,
                cost=cost,
                model=self.model,
            )

            logger.info(f"Context update LLM call completed in {duration_ms}ms")
            return content

        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise

    def update(self) -> tuple[bool, str]:
        """Run the context auto-update.

        Returns:
            Tuple of (success, message)
        """
        logger.info("Starting context auto-update")

        # Check if we should update
        should, reason = self.should_update()
        if not should:
            logger.info(f"Skipping update: {reason}")
            return False, reason

        try:
            # Load current context
            if not self.context_path.exists():
                return False, "No context.md found"
            
            current_context = self.context_path.read_text()

            # Get voted articles
            voted_articles = self._get_voted_articles()
            if not voted_articles:
                return False, "No voted articles found"

            logger.info(f"Found {len(voted_articles)} voted articles")

            # Get feedback statistics
            stats = self.storage.get_feedback_statistics(
                since_days=self.config.context_auto_update_interval_days
            )

            # Build prompt
            messages = self._build_prompt(current_context, voted_articles, stats)

            # Call LLM
            new_context = self._call_llm(messages)

            # Validate output
            is_valid, error = self._validate_context_md(new_context)
            if not is_valid:
                logger.error(f"Generated context.md invalid: {error}")
                return False, f"Validation failed: {error}"

            # Backup current context
            backup_path = self.backup_context()

            # Write new context
            self.context_path.write_text(new_context)
            logger.info(f"Updated context.md (backup: {backup_path})")

            # Record update timestamp
            self._get_last_update_path().write_text(str(time.time()))

            return True, f"Updated successfully ({len(voted_articles)} articles processed)"

        except Exception as e:
            logger.error(f"Context auto-update failed: {e}", exc_info=True)
            return False, f"Error: {e}"


def run_context_update(config: Config, storage: Storage) -> None:
    """Run context auto-update (called by scheduler).

    Args:
        config: Application configuration
        storage: Storage instance
    """
    updater = ContextAutoUpdater(config, storage)
    success, message = updater.update()
    
    if success:
        logger.info(f"Context auto-update: {message}")
    else:
        logger.debug(f"Context auto-update skipped: {message}")
