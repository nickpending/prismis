"""Daemon orchestration logic, separated from entry point for testability."""

import logging
import time
from typing import Any

from rich.console import Console

from .analysis import build_llm_analysis, get_learned_preferences
from .config import Config
from .deep_extractor import ContentDeepExtractor
from .embeddings import Embedder
from .evaluator import ContentEvaluator
from .kind_classifier import KindClassifier
from .models import ContentItem
from .notifier import Notifier
from .observability import log as obs_log
from .readability import is_readable
from .storage import Storage
from .summarizer import ContentSummarizer

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
        deep_extractor: ContentDeepExtractor | None = None,
        kind_classifier: KindClassifier | None = None,
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
            deep_extractor: Optional ContentDeepExtractor for deep synthesis on
                HIGH-priority items. None disables deep extraction.
            kind_classifier: Optional KindClassifier for tagging each item's primary
                kind (gh #77). None disables kind classification.
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
        self.deep_extractor = deep_extractor
        self.kind_classifier = kind_classifier

    @staticmethod
    def _should_deep_extract(
        priority: str | None,
        auto_extract: str,
        source_type: str | None = None,
        exclude: list[str] | None = None,
    ) -> bool:
        """Decide whether to run deep extraction based on priority + config threshold.

        Args:
            priority: The item's evaluated priority ("high"|"medium"|"low"|None)
            auto_extract: Threshold from config ("none"|"high"|"all")
            source_type: The item's source type ("rss"|"reddit"|"youtube"|"file")
            exclude: Source types to skip regardless of priority (config-driven)

        Returns:
            True if deep extraction should run for this item.
        """
        # Excluded sources skip deep extraction regardless of priority (low signal-to-noise)
        if exclude and source_type in exclude:
            return False
        if not auto_extract or auto_extract == "none":
            return False
        if auto_extract == "all":
            return priority in ("high", "medium", "low")
        if auto_extract == "high":
            return priority == "high"
        return False

    def analyze_and_store_item(
        self,
        item: ContentItem,
        source: dict[str, Any],
        learned_preferences: str | None = None,
    ) -> dict[str, Any] | None:
        """Summarize, evaluate and store one item; return what was stored.

        The per-item pipeline shared by the fetch loop and (job 2) refetch:
        summarize, evaluate, build_llm_analysis, merge with fetcher metrics,
        kind classification, the deep-extraction gate (skipped for a
        title_only item), create_or_update_content, embedding.

        Kind classification and deep extraction failures never raise
        (INV-002): each is caught here and reported back in the return dict
        for the caller to fold into its own stats, rather than failing the
        item.

        Args:
            item: ContentItem with content, title, url and any fetcher
                analysis (e.g. metrics) already set.
            source: Source dict with at least "type" and "name".
            learned_preferences: Optional learned preferences for the evaluator.

        Returns:
            None if summarization returned nothing (nothing was stored,
            mirroring the fetch loop's own skip). Otherwise a dict with
            content_id, is_new, item_dict, priority, evaluation,
            merged_analysis, kind_classify_failure and deep_extract_failure
            (the last two None when nothing failed).
        """
        source_type = source.get("type", "rss")

        metadata = item.analysis.get("metrics", {}) if item.analysis else {}

        summary_result = self.summarizer.summarize_with_analysis(
            content=item.content or "",
            title=item.title,
            url=item.url,
            source_type=source_type,
            source_name=source.get("name", ""),
            metadata=metadata,
        )

        if not summary_result:
            return None

        mode = summary_result.metadata.get("summarization_mode", "standard")
        word_count = summary_result.metadata.get("word_count", 0)
        self.console.print(
            f"       📝 Summarized with [cyan]{mode}[/cyan] mode ({word_count:,} words)"
        )

        evaluation = self.evaluator.evaluate_content(
            content=item.content or "",
            title=item.title,
            url=item.url,
            context=self.config.context,
            learned_preferences=learned_preferences,
        )

        llm_analysis = build_llm_analysis(
            summary_result,
            evaluation,
            item.content,
            (item.analysis or {}).get("fetch_outcome"),
        )

        existing_analysis = item.analysis or {}
        merged_analysis = self._merge_analysis(existing_analysis, llm_analysis)

        # Kind classification (gh #77), after the light pass. Failure must NEVER
        # raise into the pipeline (INV-002): caught below and returned for the
        # caller to record, while the item still stores with its light summary
        # and priority, just with no kind key in its analysis. No classifier
        # configured means no call at all (SC-4).
        kind_classify_failure: str | None = None
        if self.kind_classifier:
            try:
                kind_result = self.kind_classifier.classify(
                    title=item.title,
                    source_type=source_type,
                    source_name=source.get("name", ""),
                    summary=summary_result.summary or "",
                    reading_summary=summary_result.reading_summary or "",
                    raw_content=item.content or "",
                )
                merged_analysis["kind"] = kind_result.kind
                merged_analysis["kind_confidence"] = kind_result.confidence
            except Exception as e:
                kind_classify_failure = (
                    f"Kind classification failed for '{item.title}': {e}"
                )
                logger.warning(kind_classify_failure, exc_info=True)
                self.console.print(
                    f"       ⚠️  Kind classification failed: {e}",
                    style="yellow",
                )
                # Do NOT re-raise — pipeline continues without a kind (INV-002)

        item_dict = item.to_dict()
        # File sources always HIGH priority (user explicitly added)
        priority = (
            item.priority
            if source_type == "file" and item.priority
            else (evaluation.priority.value if evaluation.priority else None)
        )
        item_dict.update(
            {
                "summary": summary_result.summary,
                "analysis": merged_analysis,
                "priority": priority,
            }
        )

        # Deep extraction gate. Failure must NEVER raise into the pipeline
        # (INV-002): caught below and returned, logging and continuing with
        # the light summary only. A title-only item never deep-extracts
        # (SC-3) even when its priority passes the gate below -- there is no
        # real content underneath the light summary to synthesize further.
        deep_extract_failure: str | None = None
        if (
            self.deep_extractor
            and not merged_analysis.get("title_only")
            and self._should_deep_extract(
                priority,
                self.config.auto_extract,
                source_type,
                self.config.deep_extract_exclude,
            )
        ):
            try:
                extraction = self.deep_extractor.extract(
                    content=item.content or "",
                    title=item.title,
                    url=item.url,
                )
                if extraction:
                    merged_analysis["deep_extraction"] = extraction
                    item_dict["analysis"] = merged_analysis
                    self.console.print("       🧠 Deep extraction added")
            except Exception as e:
                # Not raised (INV-002), but returned: without this the
                # light-only item is indistinguishable from one that was
                # never meant to be deep-extracted (#72).
                deep_extract_failure = f"Deep extraction failed for '{item.title}': {e}"
                logger.warning(deep_extract_failure, exc_info=True)
                self.console.print(
                    f"       ⚠️  Deep extraction failed: {e}",
                    style="yellow",
                )
                # Do NOT re-raise — pipeline continues with light summary only (INV-002)

        content_id, is_new = self.storage.create_or_update_content(item_dict)

        # Generate and store embedding for semantic search. Uses summary +
        # synthesis (when present) so search reflects the richer
        # deep-extraction text.
        try:
            text_for_embedding = summary_result.summary or item.content or ""
            synth = merged_analysis.get("deep_extraction", {}).get("synthesis")
            if synth:
                text_for_embedding = f"{text_for_embedding}\n\n{synth}"
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
                f"Failed to generate embedding for {content_id}: {embed_error}",
                exc_info=True,
            )
            self.console.print(
                "       ⚠️  Embedding generation failed", style="yellow"
            )

        return {
            "content_id": content_id,
            "is_new": is_new,
            "item_dict": item_dict,
            "priority": priority,
            "evaluation": evaluation,
            "merged_analysis": merged_analysis,
            "kind_classify_failure": kind_classify_failure,
            "deep_extract_failure": deep_extract_failure,
        }

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
        stats: dict[str, Any] = {
            "items_fetched": 0,
            "items_processed": 0,
            "items_new": 0,
            "items_updated": 0,
            "errors": [],
            "fetch_error": None,  # Set when the source itself failed, not one item
            # Items kept at the light summary because deep extraction failed; kept out
            # of "errors", which counts pipeline failures (INV-002).
            "deep_extract_failures": [],
            # Items stored without a kind because the classifier call itself failed;
            # kept out of "errors" for the same reason (INV-002).
            "kind_classify_failures": [],
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

            # Step 2: Fetch all items from source using the appropriate fetcher.
            # existing_ids is every stored external_id; known_readable_ids is the
            # subset stored readably (title_only false or absent, SC-4) -- file
            # sources keep the two equal, since their external_id embeds the
            # content hash and can't distinguish a title-only retry from a new
            # item the way the other three fetchers' stable external_ids can.
            #
            # force_refetch means every item is re-extracted AND re-analysed
            # (its own long-standing meaning here, "then" clause of SC-4): an
            # empty known_readable_ids is passed to the fetcher itself so none
            # of the three skip paths (RSS's trafilatura fetch, Reddit's
            # article-fetch-and-comment-read, YouTube's transcript download)
            # substitute a placeholder for an item already stored readably.
            # Without this, forcing a refetch of a healthy source silently
            # degrades every already-readable item back to title_only=true.
            existing_ids = self.storage.get_existing_external_ids(source["id"])
            if source_type == "file":
                all_items = fetcher.fetch_content(source)
                known_readable_ids = existing_ids
            elif force_refetch:
                known_readable_ids = set()
                all_items = fetcher.fetch_content(
                    source, known_readable_ids=known_readable_ids
                )
            else:
                known_readable_ids = self.storage.get_readable_external_ids(
                    source["id"]
                )
                all_items = fetcher.fetch_content(
                    source, known_readable_ids=known_readable_ids
                )
            stats["items_fetched"] = len(all_items)

            if not all_items:
                self.console.print(
                    f"  📭 No items found for {source['name'] or source['url']}"
                )
                return stats

            self.console.print(
                f"  📰 Fetched {len(all_items)} items from {source['name'] or source['url']}"
            )

            # Step 2: Apply deduplication filtering. Only items already stored
            # readably are dropped here -- a stored title-only item stays in
            # items_to_process so its fresh content gets a chance to become
            # readable (SC-4); force_refetch still processes every item.
            if force_refetch:
                items_to_process = all_items
                self.console.print(
                    f"  🔄 Force refetch: processing all {len(items_to_process)} items"
                )
            else:
                items_to_process = [
                    item
                    for item in all_items
                    if item.external_id not in known_readable_ids
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
                # A title-only item retried this cycle whose fresh content is
                # still not readable is left alone rather than re-analysed
                # (SC-4) -- nothing changed, so re-running the LLM pass would
                # only spend money to store the same title_only=true result.
                # force_refetch bypasses this too: it processes every item.
                if (
                    not force_refetch
                    and item.external_id in existing_ids
                    and item.external_id not in known_readable_ids
                    and not is_readable(item.content)
                ):
                    self.console.print(
                        f"    ⏭️  [{i}/{len(items_to_process)}] Still not readable, skipping: {item.title[:60]}"
                    )
                    continue

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
                                self.storage.add_embedding(content_id, embedding)
                                self.console.print(
                                    f"       🔗 Indexed for semantic search ({self.embedder.get_dimension()} dims)"
                                )
                        except Exception as e:
                            logger.warning(
                                f"Failed to generate embedding for {item.title}: {e}",
                                exc_info=True,
                            )

                        if is_new:
                            stats["items_new"] += 1
                        continue

                    # Steps 3b-3f: summarize, evaluate, build_llm_analysis, merge
                    # with fetcher metrics, kind classification, the
                    # deep-extraction gate, storage and embedding all live in
                    # one method shared with refetch (SC-1).
                    result = self.analyze_and_store_item(
                        item, source, learned_preferences
                    )
                    if result is None:
                        # Skip if summarization failed
                        continue

                    if result["kind_classify_failure"]:
                        stats["kind_classify_failures"].append(
                            result["kind_classify_failure"]
                        )
                    if result["deep_extract_failure"]:
                        stats["deep_extract_failures"].append(
                            result["deep_extract_failure"]
                        )

                    is_new = result["is_new"]
                    item_dict = result["item_dict"]
                    evaluation = result["evaluation"]

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
                    # Per-item boundary: one item's failure never stops the source.
                    error_msg = f"Failed to analyze item '{item.title}': {e}"
                    logger.exception(error_msg)
                    self.console.print(f"    [red]{error_msg}[/red]")
                    stats["errors"].append(error_msg)

            return stats

        except Exception as e:
            # Per-source fetch boundary: one source's failure never stops the cycle.
            error_msg = f"Failed to fetch from {source['url']}: {e}"
            logger.exception(error_msg)
            self.console.print(f"  [red]{error_msg}[/red]")
            stats["errors"].append(error_msg)
            stats["fetch_error"] = str(e)
            return stats

    def run_once(self, force_refetch: bool = False) -> dict:
        """Run one fetch-analyze-store cycle with deduplication.

        Args:
            force_refetch: If True, process all items regardless of existence

        Returns:
            Dict with stats: total_items, total_analyzed, total_new, total_updated, errors
        """
        start_time = time.time()

        stats: dict[str, Any] = {
            "total_items": 0,
            "total_analyzed": 0,
            "total_new": 0,
            "total_updated": 0,
            "errors": [],
            "deep_extract_failures": [],
            "kind_classify_failures": [],
            "new_high_priority_items": [],  # Aggregate new HIGH priority items
        }

        # Fetch learned preferences for LLM evaluation (003-light-preference-learning)
        # Only activates if user has provided at least 5 votes in the last 30 days
        learned_preferences = None
        try:
            learned_preferences, total_votes = get_learned_preferences(self.storage)
            if learned_preferences:
                self.console.print(
                    f"🧠 Using learned preferences from {total_votes} votes (last 30 days)"
                )
        except Exception as e:
            logger.warning(f"Failed to fetch feedback statistics: {e}", exc_info=True)
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
                stats["deep_extract_failures"].extend(
                    source_stats["deep_extract_failures"]
                )
                stats["kind_classify_failures"].extend(
                    source_stats["kind_classify_failures"]
                )
                stats["new_high_priority_items"].extend(
                    source_stats["new_high_priority_items"]
                )

                # fetch_source_content reports a failed fetch in its stats rather than
                # raising, so the except below never sees it (#75).
                if source_stats["fetch_error"] is not None:
                    self.storage.update_source_fetch_status(
                        source["id"], False, source_stats["fetch_error"]
                    )
                else:
                    self.storage.update_source_fetch_status(source["id"], True)

            except Exception as e:
                # Per-source boundary: one source's failure never stops the cycle.
                error_msg = f"Failed to process source {source['url']}: {e}"
                logger.exception(error_msg)
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
            deep_extract_failures=len(stats["deep_extract_failures"]),
            kind_classify_failures=len(stats["kind_classify_failures"]),
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

        # A storage failure raises: the scheduler logs the job exception with its
        # traceback, where a zero count would read as "nothing to archive".
        count = self.storage.archive_old_content(archival_config)

        if count > 0:
            self.console.print(f"[cyan]📦 Auto-archival: {count} items archived[/cyan]")

        return {"archived_count": count}

    def backfill_embeddings(self, limit: int = 50) -> dict:
        """Generate embeddings for items without them (stragglers from failures).

        Args:
            limit: Maximum items to process per run (default 50)

        Returns:
            Dict with stats: processed_count, failed_count
        """
        # A storage failure raises: the scheduler logs the job exception with its
        # traceback, where a zero count would read as "nothing to backfill".
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
                # Per-item boundary: one item's embedding failure counts as failed
                # and the loop continues.
                logger.warning(
                    f"Failed embedding for '{item['title']}': {e}", exc_info=True
                )
                self.console.print(
                    f"[yellow]⚠ Failed embedding for '{item['title']}': {e}[/yellow]"
                )
                failed += 1

        if processed > 0:
            self.console.print(
                f"[cyan]🔗 Auto-indexed {processed} stragglers ({failed} failed)[/cyan]"
            )

        return {"processed": processed, "failed": failed}
