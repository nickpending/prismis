"""Daemon orchestration logic, separated from entry point for testability."""

import logging
import time
from typing import Any

from rich.console import Console

try:
    # When run as module
    from .config import Config
    from .embeddings import Embedder
    from .evaluator import ContentEvaluator
    from .notifier import Notifier
    from .observability import log as obs_log
    from .storage import Storage
    from .summarizer import ContentSummarizer
except ImportError:
    # When imported directly in tests
    from config import Config
    from embeddings import Embedder
    from evaluator import ContentEvaluator
    from notifier import Notifier
    from observability import log as obs_log
    from storage import Storage
    from summarizer import ContentSummarizer

console = Console()
logger = logging.getLogger(__name__)


class DaemonOrchestrator:
    """Orchestrates the fetch-analyze-store pipeline with injected dependencies."""

    def __init__(
        self,
        storage: Storage,
        rss_fetcher,
        reddit_fetcher,
        youtube_fetcher,
        file_fetcher,
        summarizer: ContentSummarizer,
        evaluator: ContentEvaluator,
        notifier: Notifier,
        config: Config,
        console: Console | None = None,
        embedder: Embedder | None = None,
    ):
        """Initialize orchestrator with dependencies.

        Args:
            storage: Storage instance for database operations
            rss_fetcher: RSS fetcher instance
            reddit_fetcher: Reddit fetcher instance
            file_fetcher: File fetcher instance
            youtube_fetcher: YouTube fetcher instance
            summarizer: Summarizer instance for content analysis
            evaluator: Evaluator instance for priority evaluation
            notifier: Notifier instance for desktop notifications
            config: Configuration object with all daemon settings
            console: Optional Rich console for output
            embedder: Optional Embedder instance for semantic search (created if not provided)
        """
        self.storage = storage
        self.rss_fetcher = rss_fetcher
        self.reddit_fetcher = reddit_fetcher
        self.youtube_fetcher = youtube_fetcher
        self.file_fetcher = file_fetcher
        self.summarizer = summarizer
        self.evaluator = evaluator
        self.notifier = notifier
        self.config = config
        self.console = console or Console()
        self.embedder = embedder or Embedder()

    def _is_transient_error(self, error: Exception) -> bool:
        """Check if an error is transient and should be retried.

        Args:
            error: The exception that occurred

        Returns:
            True if the error is transient (timeout, rate limit, connection)
        """
        error_str = str(error).lower()
        transient_patterns = [
            "timeout",
            "rate limit",
            "rate_limit",
            "ratelimit",
            "429",
            "connection",
            "temporarily unavailable",
            "service unavailable",
            "503",
            "502",
            "500",
        ]
        return any(pattern in error_str for pattern in transient_patterns)

    def _call_with_retry(self, func, *args, **kwargs):
        """Call a function with exponential backoff retry for transient errors.

        Args:
            func: Function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result of func(*args, **kwargs)

        Raises:
            Exception: If all retries exhausted or non-transient error
        """
        max_retries = self.config.llm_max_retries
        backoff_base = self.config.llm_retry_backoff_base
        last_error = None

        for attempt in range(max_retries + 1):  # +1 for initial attempt
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e

                # Don't retry non-transient errors
                if not self._is_transient_error(e):
                    raise

                # Don't retry if we've exhausted attempts
                if attempt >= max_retries:
                    obs_log(
                        "llm.retry",
                        action="exhausted",
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        error=str(e),
                    )
                    raise

                # Calculate backoff delay
                delay = backoff_base**attempt  # 2^0=1, 2^1=2, 2^2=4 seconds
                obs_log(
                    "llm.retry",
                    action="retrying",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    delay_seconds=delay,
                    error=str(e),
                )
                self.console.print(
                    f"       ⚠️  LLM error (attempt {attempt + 1}/{max_retries + 1}), "
                    f"retrying in {delay}s: {e}",
                    style="yellow",
                )
                time.sleep(delay)

        # Should never reach here, but just in case
        raise last_error  # type: ignore

    def fetch_source_content(
        self,
        source: dict[str, Any],
        force_refetch: bool = False,
        learned_preferences: str | None = None,
    ) -> dict[str, Any]:
        """Fetch and process content from a single source with deduplication.

        Implements two-path deduplication:
        - Normal operation: Check existing external_ids, only process new items
        - Force refetch: Process all items, updating existing metadata

        Args:
            source: Source dict with id, url, name, type
            force_refetch: If True, process all items regardless of existence
            learned_preferences: Optional learned preferences from user feedback for LLM

        Returns:
            Dict with processing stats: items_fetched, items_processed, items_new, items_updated, new_high_priority_items
        """
        stats = {
            "items_fetched": 0,
            "items_processed": 0,
            "items_new": 0,
            "items_updated": 0,
            "errors": [],
            "new_high_priority_items": [],  # Track new HIGH priority items for notifications
        }

        try:
            # Step 1: Select the appropriate fetcher based on source type
            source_type = source.get("type", "rss")
            if source_type == "reddit":
                fetcher = self.reddit_fetcher
            elif source_type == "youtube":
                fetcher = self.youtube_fetcher
            elif source_type == "file":
                fetcher = self.file_fetcher
            else:
                fetcher = self.rss_fetcher

            # Step 2: Fetch all items from source using the appropriate fetcher
            # All fetchers now use the same interface - pass the source dict
            all_items = fetcher.fetch_content(source)
            stats["items_fetched"] = len(all_items)

            if not all_items:
                self.console.print(
                    f"  📭 No items found for {source['name'] or source['url']}"
                )
                return stats

            self.console.print(
                f"  📰 Fetched {len(all_items)} items from {source['name'] or source['url']}"
            )

            # Step 2: Apply deduplication filtering
            if force_refetch:
                # Process all items when forcing refetch
                items_to_process = all_items
                self.console.print(
                    f"  🔄 Force refetch: processing all {len(items_to_process)} items"
                )
            else:
                # Get existing external_ids for efficient filtering
                existing_ids = self.storage.get_existing_external_ids(source["id"])
                items_to_process = [
                    item for item in all_items if item.external_id not in existing_ids
                ]

                filtered_count = len(all_items) - len(items_to_process)
                if filtered_count > 0:
                    self.console.print(
                        f"  ⏭️  Skipping {filtered_count} existing items, processing {len(items_to_process)} new"
                    )
                else:
                    self.console.print(
                        f"  🆕 All {len(items_to_process)} items are new"
                    )

            stats["items_processed"] = len(items_to_process)

            # Step 3: Analyze and store items that need processing
            for i, item in enumerate(items_to_process, 1):
                self.console.print(
                    f"    🔍 [{i}/{len(items_to_process)}] Analyzing: {item.title[:60]}..."
                )
                try:
                    # Step 3a: Check if we should skip LLM analysis for file sources
                    skip_llm_analysis = False
                    if source.get("type") == "file":
                        # For file sources, skip analysis if content is too large (baseline fetch)
                        # Analyze diffs (which start with "---" or are smaller)
                        content_size = len(item.content) if item.content else 0
                        is_diff = item.content and item.content.startswith("---")

                        if content_size > 50000 and not is_diff:
                            skip_llm_analysis = True
                            self.console.print(
                                f"       ⏭️  Skipping LLM analysis (baseline file: {content_size:,} bytes)"
                            )

                    # Step 3b: Summarize and extract insights (unless skipped)
                    if skip_llm_analysis:
                        # Store without LLM analysis - just baseline content
                        # Default file sources to HIGH priority (user explicitly added, wants updates)
                        existing_analysis = item.analysis or {}
                        item_dict = item.to_dict()
                        item_dict.update(
                            {
                                "summary": None,
                                "analysis": existing_analysis,
                                "priority": "high",
                            }
                        )

                        # Store baseline and skip to next item
                        content_id, is_new = self.storage.create_or_update_content(
                            item_dict
                        )

                        # Generate embedding even for baseline (for future search)
                        try:
                            if self.embedder and item.content:
                                embedding = self.embedder.generate_embedding(
                                    item.content
                                )
                                self.storage.store_embedding(content_id, embedding)
                                self.console.print(
                                    f"       🔗 Indexed for semantic search ({self.embedder.get_dimension()} dims)"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Failed to generate embedding for {item.title}: {e}"
                            )

                        if is_new:
                            stats["new_items"] += 1
                        continue

                    # Pass source name and metadata for context
                    metadata = {}
                    if hasattr(item, "analysis") and item.analysis:
                        metadata = item.analysis.get("metrics", {})

                    summary_result = self._call_with_retry(
                        self.summarizer.summarize_with_analysis,
                        content=item.content,
                        title=item.title,
                        url=item.url,
                        source_type=source.get("type", "rss"),
                        source_name=source.get("name", ""),
                        metadata=metadata,
                    )

                    if not summary_result:
                        # Skip if summarization failed
                        continue

                    # Show summarization mode
                    mode = summary_result.metadata.get("summarization_mode", "standard")
                    word_count = summary_result.metadata.get("word_count", 0)
                    self.console.print(
                        f"       📝 Summarized with [cyan]{mode}[/cyan] mode ({word_count:,} words)"
                    )

                    # Step 3b: Evaluate priority against user context
                    evaluation = self._call_with_retry(
                        self.evaluator.evaluate_content,
                        content=item.content,
                        title=item.title,
                        url=item.url,
                        context=self.config.context,
                        learned_preferences=learned_preferences,
                    )

                    # Step 3c: Build LLM analysis data
                    llm_analysis = {
                        "reading_summary": summary_result.reading_summary,
                        "alpha_insights": summary_result.alpha_insights,
                        "patterns": summary_result.patterns,
                        "entities": summary_result.entities,
                        "quotes": summary_result.quotes,
                        "tools": summary_result.tools,
                        "urls": summary_result.urls,
                        "matched_interests": evaluation.matched_interests,
                        "priority_reasoning": evaluation.reasoning,
                        "preference_influenced": evaluation.preference_influenced,
                        "metadata": summary_result.metadata,
                    }

                    # Step 3d: Merge with existing analysis (preserve fetcher metrics)
                    existing_analysis = item.analysis or {}
                    merged_analysis = self._merge_analysis(
                        existing_analysis, llm_analysis
                    )

                    # Step 3e: Convert ContentItem to dict and add merged analysis
                    item_dict = item.to_dict()
                    # File sources always HIGH priority (user explicitly added)
                    priority = (
                        item.priority
                        if source.get("type") == "file" and item.priority
                        else (
                            evaluation.priority.value if evaluation.priority else None
                        )
                    )
                    item_dict.update(
                        {
                            "summary": summary_result.summary,
                            "analysis": merged_analysis,
                            "priority": priority,
                        }
                    )

                    # Step 3e: Store with deduplication tracking
                    content_id, is_new = self.storage.create_or_update_content(
                        item_dict
                    )

                    # Step 3f: Generate and store embedding for semantic search
                    try:
                        # Use summary for embedding (more concise than full content)
                        text_for_embedding = summary_result.summary or item.content
                        embedding = self.embedder.generate_embedding(
                            text=text_for_embedding,
                            title=item.title,
                        )
                        self.storage.add_embedding(content_id, embedding)
                        self.console.print(
                            f"       🔗 Indexed for semantic search ({len(embedding)} dims)"
                        )
                    except Exception as embed_error:
                        # Log embedding failure but don't block content storage
                        logger.warning(
                            f"Failed to generate embedding for {content_id}: {embed_error}"
                        )
                        self.console.print(
                            "       ⚠️  Embedding generation failed", style="yellow"
                        )

                    if is_new:
                        stats["items_new"] += 1
                        # Track new HIGH priority items for notifications
                        if evaluation.priority and evaluation.priority.value == "high":
                            stats["new_high_priority_items"].append(item_dict)
                    else:
                        stats["items_updated"] += 1

                    # Show priority result
                    priority_emoji = {
                        "high": "🔴",
                        "medium": "🟡",
                        "low": "⚪",
                        None: "⚫",  # Black dot for unprioritized
                    }
                    action = "NEW" if is_new else "UPDATED"
                    priority_val = (
                        evaluation.priority.value if evaluation.priority else None
                    )
                    priority_display = (
                        priority_val.upper() if priority_val else "UNPRIORITIZED"
                    )
                    self.console.print(
                        f"       {priority_emoji.get(priority_val, '❓')} {action}: {priority_display}"
                    )

                except Exception as e:
                    error_msg = f"Failed to analyze item '{item.title}': {e}"
                    self.console.print(f"    [red]{error_msg}[/red]")
                    stats["errors"].append(error_msg)

            return stats

        except Exception as e:
            error_msg = f"Failed to fetch from {source['url']}: {e}"
            self.console.print(f"  [red]{error_msg}[/red]")
            stats["errors"].append(error_msg)
            return stats

    def run_once(self, force_refetch: bool = False) -> dict:
        """Run one fetch-analyze-store cycle with deduplication.

        Args:
            force_refetch: If True, process all items regardless of existence

        Returns:
            Dict with stats: total_items, total_analyzed, total_new, total_updated, errors
        """
        start_time = time.time()

        stats = {
            "total_items": 0,
            "total_analyzed": 0,
            "total_new": 0,
            "total_updated": 0,
            "errors": [],
            "new_high_priority_items": [],  # Aggregate new HIGH priority items
        }

        # Fetch learned preferences for LLM evaluation (003-light-preference-learning)
        # Only activates if user has provided at least 5 votes in the last 30 days
        learned_preferences = None
        try:
            feedback_stats = self.storage.get_feedback_statistics(since_days=30)
            total_votes = feedback_stats.get("totals", {}).get("total_votes", 0)
            if total_votes >= 5:
                learned_preferences = feedback_stats.get("for_llm_context")
                if learned_preferences:
                    self.console.print(
                        f"🧠 Using learned preferences from {total_votes} votes (last 30 days)"
                    )
        except Exception as e:
            logger.warning(f"Failed to fetch feedback statistics: {e}")
            # Continue without learned preferences - not critical

        # Get active sources
        self.console.print("📡 Getting active sources...")
        sources = self.storage.get_active_sources()

        # Log cycle start
        obs_log("daemon.cycle.start", sources=len(sources), force_refetch=force_refetch)

        if not sources:
            self.console.print(
                "[yellow]No active sources found. Add sources with prismis-cli.[/yellow]"
            )
            return stats

        self.console.print(f"Found {len(sources)} active source(s)")

        # Process each source with deduplication
        for source_num, source in enumerate(sources, 1):
            self.console.print(
                f"\n[bold cyan]Processing source {source_num}/{len(sources)}: {source['name'] or source['url']}[/bold cyan]"
            )

            try:
                # Use the new fetch_source_content method with deduplication
                source_stats = self.fetch_source_content(
                    source, force_refetch, learned_preferences
                )

                # Aggregate stats
                stats["total_items"] += source_stats["items_fetched"]
                stats["total_analyzed"] += source_stats["items_processed"]
                stats["total_new"] += source_stats["items_new"]
                stats["total_updated"] += source_stats["items_updated"]
                stats["errors"].extend(source_stats["errors"])
                stats["new_high_priority_items"].extend(
                    source_stats["new_high_priority_items"]
                )

                # Update source fetch status (success)
                self.storage.update_source_fetch_status(source["id"], True)

            except Exception as e:
                error_msg = f"Failed to process source {source['url']}: {e}"
                self.console.print(f"  [red]{error_msg}[/red]")
                stats["errors"].append(error_msg)
                # Update source fetch status (failure)
                self.storage.update_source_fetch_status(source["id"], False, str(e))

        # Send notifications for NEW HIGH priority content only
        if stats["new_high_priority_items"]:
            self.console.print(
                f"🔔 Sending notification for {len(stats['new_high_priority_items'])} new HIGH priority items..."
            )
            self.notifier.notify_new_content(stats["new_high_priority_items"])

        # Summary with enhanced stats
        self.console.print("\n[bold green]✅ Processing complete[/bold green]")
        self.console.print(f"📊 Total items fetched: {stats['total_items']}")
        self.console.print(f"🧠 Total items analyzed: {stats['total_analyzed']}")
        self.console.print(f"🆕 New items: {stats['total_new']}")
        self.console.print(f"🔄 Updated items: {stats['total_updated']}")

        if force_refetch:
            self.console.print("🔄 Force refetch was enabled")

        # Log cycle complete
        duration_ms = int((time.time() - start_time) * 1000)
        obs_log(
            "daemon.cycle.complete",
            duration_ms=duration_ms,
            items_fetched=stats["total_items"],
            items_new=stats["total_new"],
            items_updated=stats["total_updated"],
            errors=len(stats["errors"]),
        )

        return stats

    def _merge_analysis(self, existing_analysis: dict, llm_analysis: dict) -> dict:
        """Merge existing fetcher analysis with new LLM analysis.

        Args:
            existing_analysis: Original analysis from fetcher (may contain metrics)
            llm_analysis: New analysis data from LLM processing

        Returns:
            Merged analysis dict preserving fetcher metrics while adding LLM data
        """
        try:
            # Start with LLM analysis as base
            merged = llm_analysis.copy()

            # Preserve important fetcher data if present
            if existing_analysis:
                # Preserve metrics from Reddit/YouTube fetchers
                if "metrics" in existing_analysis:
                    merged["metrics"] = existing_analysis["metrics"]
                    logger.debug("Preserved fetcher metrics in analysis")

                # Preserve any other fetcher-specific data
                for key, value in existing_analysis.items():
                    if key not in merged and key != "metrics":
                        merged[key] = value
                        logger.debug(f"Preserved existing analysis field: {key}")

            return merged

        except Exception as e:
            # Fallback: If merge fails, preserve metrics over LLM analysis
            logger.warning(f"Analysis merge failed: {e}, preserving existing analysis")
            if existing_analysis and "metrics" in existing_analysis:
                # Metrics are more important than LLM analysis for user decision-making
                fallback = {"metrics": existing_analysis["metrics"]}
                fallback.update(llm_analysis)
                return fallback
            else:
                # No metrics to preserve, just return LLM analysis
                return llm_analysis

    def run_archival_policy(self) -> dict:
        """Run archival policy based on config.

        Returns:
            Dict with stats: archived_count
        """
        if not self.config.archival_enabled:
            return {"archived_count": 0}

        # Build archival config from settings
        archival_config = {
            "high_read": self.config.archival_high_read,
            "medium_unread": self.config.archival_medium_unread,
            "medium_read": self.config.archival_medium_read,
            "low_unread": self.config.archival_low_unread,
            "low_read": self.config.archival_low_read,
        }

        try:
            count = self.storage.archive_old_content(archival_config)

            if count > 0:
                self.console.print(
                    f"[cyan]📦 Auto-archival: {count} items archived[/cyan]"
                )

            return {"archived_count": count}

        except Exception as e:
            self.console.print(f"[red]❌ Archival failed: {e}[/red]")
            return {"archived_count": 0}

    def backfill_embeddings(self, limit: int = 50) -> dict:
        """Generate embeddings for items without them (stragglers from failures).

        Args:
            limit: Maximum items to process per run (default 50)

        Returns:
            Dict with stats: processed_count, failed_count
        """
        try:
            # Get items without embeddings
            batch = self.storage.get_content_without_embeddings(limit=limit)

            if not batch:
                return {"processed": 0, "failed": 0}

            processed = 0
            failed = 0

            for item in batch:
                try:
                    # Generate embedding from summary or content
                    text = item["summary"] or item["content"] or item["title"]
                    embedding = self.embedder.generate_embedding(
                        text=text, title=item["title"]
                    )

                    if embedding:
                        self.storage.add_embedding(
                            content_id=item["id"], embedding=embedding
                        )
                        processed += 1
                    else:
                        failed += 1

                except Exception as e:
                    self.console.print(
                        f"[yellow]⚠ Failed embedding for '{item['title']}': {e}[/yellow]"
                    )
                    failed += 1

            if processed > 0:
                self.console.print(
                    f"[cyan]🔗 Auto-indexed {processed} stragglers ({failed} failed)[/cyan]"
                )

            return {"processed": processed, "failed": failed}

        except Exception as e:
            self.console.print(f"[red]❌ Embedding backfill failed: {e}[/red]")
            return {"processed": 0, "failed": 0}
