"""Daemon orchestration logic, separated from entry point for testability."""

import logging
from typing import Optional, Dict, Any

from rich.console import Console

try:
    # When run as module
    from .storage import Storage
    from .summarizer import ContentSummarizer
    from .evaluator import ContentEvaluator
    from .notifier import Notifier
except ImportError:
    # When imported directly in tests
    from storage import Storage
    from summarizer import ContentSummarizer
    from evaluator import ContentEvaluator
    from notifier import Notifier

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
        summarizer: ContentSummarizer,
        evaluator: ContentEvaluator,
        notifier: Notifier,
        config: dict,
        console: Optional[Console] = None,
    ):
        """Initialize orchestrator with dependencies.

        Args:
            storage: Storage instance for database operations
            rss_fetcher: RSS fetcher instance
            reddit_fetcher: Reddit fetcher instance
            youtube_fetcher: YouTube fetcher instance
            summarizer: Summarizer instance for content analysis
            evaluator: Evaluator instance for priority evaluation
            notifier: Notifier instance for desktop notifications
            config: Configuration dictionary with context
            console: Optional Rich console for output
        """
        self.storage = storage
        self.rss_fetcher = rss_fetcher
        self.reddit_fetcher = reddit_fetcher
        self.youtube_fetcher = youtube_fetcher
        self.summarizer = summarizer
        self.evaluator = evaluator
        self.notifier = notifier
        self.config = config
        self.console = console or Console()

    def fetch_source_content(
        self, source: Dict[str, Any], force_refetch: bool = False
    ) -> Dict[str, Any]:
        """Fetch and process content from a single source with deduplication.

        Implements two-path deduplication:
        - Normal operation: Check existing external_ids, only process new items
        - Force refetch: Process all items, updating existing metadata

        Args:
            source: Source dict with id, url, name, type
            force_refetch: If True, process all items regardless of existence

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
                    # Step 3a: Summarize and extract insights
                    summary_result = self.summarizer.summarize_with_analysis(
                        content=item.content,
                        title=item.title,
                        url=item.url,
                        source_type=source.get("type", "rss"),
                    )

                    if not summary_result:
                        # Skip if summarization failed
                        continue

                    # Step 3b: Evaluate priority against user context
                    evaluation = self.evaluator.evaluate_content(
                        content=item.content,
                        title=item.title,
                        url=item.url,
                        context=self.config["context"],
                    )

                    # Step 3c: Build LLM analysis data
                    llm_analysis = {
                        "reading_summary": summary_result.reading_summary,
                        "alpha_insights": summary_result.alpha_insights,
                        "patterns": summary_result.patterns,
                        "entities": summary_result.entities,
                        "matched_interests": evaluation.matched_interests,
                        "priority_reasoning": evaluation.reasoning,
                        "metadata": summary_result.metadata,
                    }

                    # Step 3d: Merge with existing analysis (preserve fetcher metrics)
                    existing_analysis = item.analysis or {}
                    merged_analysis = self._merge_analysis(
                        existing_analysis, llm_analysis
                    )

                    # Step 3e: Convert ContentItem to dict and add merged analysis
                    item_dict = item.to_dict()
                    item_dict.update(
                        {
                            "summary": summary_result.summary,
                            "analysis": merged_analysis,
                            "priority": evaluation.priority.value
                            if evaluation.priority
                            else None,
                        }
                    )

                    # Step 3e: Store with deduplication tracking
                    content_id, is_new = self.storage.create_or_update_content(
                        item_dict
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
        stats = {
            "total_items": 0,
            "total_analyzed": 0,
            "total_new": 0,
            "total_updated": 0,
            "errors": [],
            "new_high_priority_items": [],  # Aggregate new HIGH priority items
        }

        # Get active sources
        self.console.print("📡 Getting active sources...")
        sources = self.storage.get_active_sources()

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
                source_stats = self.fetch_source_content(source, force_refetch)

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
