"""RSS content fetcher with full article extraction."""

import hashlib
import logging
import time
from datetime import UTC, datetime, timedelta

import feedparser
import httpx

from ..article_extractor import extract_article
from ..config import Config
from ..hackernews import fetch_discussion, is_item_link, story_id
from ..models import ContentItem
from ..observability import log as obs_log
from ..readability import (
    RSS_NO_CONTENT_FALLBACK,
    format_discussion,
    format_reddit_link_only,
    is_readable,
)

logger = logging.getLogger(__name__)


class RSSFetcher:
    """Fetches and processes RSS feed content with full article extraction.

    Implements the plugin pattern for content sources. Fetches RSS feeds,
    extracts full article content, and returns standardized ContentItem objects.
    """

    def __init__(self, max_items: int | None = None, config: Config | None = None, timeout: int = 30):
        """Initialize the RSS fetcher.

        Args:
            max_items: Maximum number of items to fetch per feed (uses config if None)
            config: Config instance (loads from file if None)
            timeout: Timeout in seconds for HTTP requests (default: 30)
        """
        # Load config if not provided
        if config is None:
            config = Config.from_file()

        self.max_items = max_items or config.get_max_items("rss")
        self.config = config
        self.timeout = timeout
        self.client = httpx.Client(timeout=timeout, follow_redirects=True)

    def fetch_content(
        self, source: dict, known_readable_ids: set[str] | None = None
    ) -> list[ContentItem]:
        """Fetch RSS feed and extract full content for each item.

        Args:
            source: Source dict with 'url' and 'id' keys
            known_readable_ids: external_ids the orchestrator already has stored
                readably (SC-4). An entry matching one of these skips the
                trafilatura fetch entirely -- the orchestrator's own dedup filter
                drops the result anyway, so the fetch would only be discarded work.

        Returns:
            List of ContentItem objects with full content extracted

        Raises:
            Exception: If feed parsing fails (wrapped with context)
        """
        known_readable_ids = known_readable_ids or set()
        source_url = source.get("url", "")
        source_id = source.get("id", "")
        items = []

        start_time = time.time()

        try:
            # Parse RSS feed with timeout via httpx
            logger.info(f"Fetching RSS feed: {source_url}")
            response = self.client.get(source_url)
            feed = feedparser.parse(response.text)

            # Check for feed errors
            if feed.bozo:
                logger.warning(
                    f"Feed parsing issues for {source_url}: {feed.bozo_exception}"
                )

            # Calculate cutoff date from config
            cutoff_date = datetime.now(UTC) - timedelta(
                days=self.config.max_days_lookback
            )
            logger.debug(
                f"Filtering content older than {cutoff_date} ({self.config.max_days_lookback} days)"
            )

            # Process feed entries with date filtering and max items limit
            entries = feed.entries if hasattr(feed, "entries") else []

            # Apply max items limit for RSS feeds
            max_items = self.config.get_max_items("rss")
            entries = entries[:max_items]  # Limit before processing

            logger.info(
                f"Processing {len(entries)} entries from {source_url} (max {max_items})"
            )

            filtered_count = 0
            for entry in entries:
                try:
                    # Extract basic metadata
                    external_id = self._get_external_id(entry)
                    title = str(entry.get("title", "Untitled"))
                    url = str(entry.get("link", ""))

                    if not url:
                        logger.warning(f"Skipping entry without URL: {title}")
                        continue

                    # Get published date and apply filter
                    published_at = self._parse_published_date(entry)

                    # Skip entries older than cutoff date
                    if published_at and published_at < cutoff_date:
                        filtered_count += 1
                        logger.debug(
                            f"Skipping old entry: {title} (published {published_at})"
                        )
                        continue

                    # Extract full article content with trafilatura -- skipped for
                    # an entry already stored readably (SC-4): the orchestrator's
                    # dedup filter discards this item either way, so extracting
                    # again would only be wasted network and CPU.
                    fetch_outcome: dict[str, str] | None = None
                    comments_outcome: dict[str, str] | None = None
                    hn_id = story_id(entry.get("comments"))
                    if external_id in known_readable_ids:
                        content = self._fallback_content(entry)
                    elif hn_id and is_item_link(url, hn_id):
                        # An Ask/Show HN self post links to its own HN page: there
                        # is no article to extract, its body comes from the API.
                        content = self._fallback_content(entry)
                    else:
                        content, fetch_outcome = self._extract_full_content(url, entry)

                    # A Hacker News story also gets its top comments, read from HN's
                    # API -- skipped, like the article, for an entry already stored
                    # readably.
                    if hn_id and external_id not in known_readable_ids:
                        content, comments_outcome = self.add_hn_discussion(
                            hn_id, url, content
                        )

                    # Create ContentItem (use fetched_at if no published_at)
                    fetched_at = datetime.now(UTC)
                    item = ContentItem(
                        source_id=source_id,
                        external_id=external_id,
                        title=title,
                        url=url,
                        content=content,
                        published_at=published_at or fetched_at,
                        fetched_at=fetched_at,
                        analysis=(
                            {
                                **(
                                    {"fetch_outcome": fetch_outcome}
                                    if fetch_outcome
                                    else {}
                                ),
                                **(
                                    {"comments_outcome": comments_outcome}
                                    if comments_outcome
                                    else {}
                                ),
                            }
                            or None
                        ),
                    )

                    items.append(item)
                    logger.debug(f"Processed: {title} ({len(content)} chars)")

                    # Stop if we have enough items
                    if len(items) >= self.max_items:
                        break

                except Exception as e:
                    # Per-entry boundary: one bad entry never stops the feed.
                    logger.error(
                        f"Error processing entry '{entry.get('title', 'Unknown')}': {e}",
                        exc_info=True,
                    )
                    continue

            if filtered_count > 0:
                logger.info(
                    f"Filtered {filtered_count} old entries (older than {self.config.max_days_lookback} days)"
                )
            logger.info(f"Successfully fetched {len(items)} items from {source_url}")

            # Log successful fetch
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.complete",
                fetcher_type="rss",
                source_id=source_id,
                source_url=source_url,
                items_count=len(items),
                duration_ms=duration_ms,
                status="success",
            )

        except Exception as e:
            # Log fetch error
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.error",
                fetcher_type="rss",
                source_id=source_id,
                source_url=source_url,
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise Exception(f"Failed to fetch RSS feed {source_url}: {e}") from e

        finally:
            # Cleanup client if needed
            pass

        return items

    def _get_external_id(self, entry: dict) -> str:
        """Generate a unique external ID for deduplication.

        Args:
            entry: Feed entry dict from feedparser

        Returns:
            Unique identifier for this entry
        """
        # Try to use entry ID if available
        if entry.get("id"):
            return entry["id"]

        # Fall back to URL hash
        if entry.get("link"):
            return hashlib.sha256(entry["link"].encode()).hexdigest()[:16]

        # Last resort: hash the title
        title = entry.get("title", str(datetime.now(UTC)))
        return hashlib.sha256(title.encode()).hexdigest()[:16]

    def _parse_published_date(self, entry: object) -> datetime | None:
        """Parse published date from feed entry.

        Args:
            entry: Feed entry from feedparser, read by attribute (FeedParserDict
                exposes both attribute and mapping access; only attributes are used
                here so plain objects work too)

        Returns:
            Parsed datetime or None if not available
        """
        # feedparser provides parsed time tuple - convert to timezone-aware datetime
        published_parsed = getattr(entry, "published_parsed", None)
        if published_parsed:
            try:
                # Convert time tuple to timezone-aware datetime
                year, month, day, hour, minute, second = published_parsed[:6]
                return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
            except (TypeError, ValueError, OverflowError) as e:
                logger.debug(f"Could not parse published date: {e}")

        # Try updated date as fallback
        updated_parsed = getattr(entry, "updated_parsed", None)
        if updated_parsed:
            try:
                # Convert time tuple to timezone-aware datetime
                year, month, day, hour, minute, second = updated_parsed[:6]
                return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
            except (TypeError, ValueError, OverflowError) as e:
                logger.debug(f"Could not parse updated date: {e}")

        return None

    def _extract_full_content(
        self, url: str, entry: dict
    ) -> tuple[str, dict[str, str]]:
        """Extract full article content using the shared article extractor.

        Args:
            url: Article URL to fetch
            entry: Original feed entry (fallback for content)

        Returns:
            (content, fetch_outcome): the full article text or the summary/
            description fallback, and the extraction's `{"outcome", "detail"}`
        """
        logger.debug(f"Extracting full content from: {url}")
        result = extract_article(
            url, allowed_private_hosts=self.config.fetch_allow_private_hosts
        )
        content = result.text
        if content and is_readable(content):
            logger.debug(f"Extracted {len(content)} chars from {url}")
            return content, result.as_fetch_outcome()

        logger.debug(f"No readable extraction for {url}, using fallback")
        return self._fallback_content(entry), result.as_fetch_outcome()

    def add_hn_discussion(
        self, hn_id: str, url: str, content: str
    ) -> tuple[str, dict[str, str] | None]:
        """Add Hacker News story `hn_id`'s API discussion to `content`.

        An Ask/Show HN self post (its `url` is the HN item itself) takes the item's
        own text as its body. The top comments follow under the shared discussion
        header. When the article is absent -- `content` is not readable -- a link line
        to `url` leads instead, the shape the readability check reads as "no article,
        discussion only".

        Returns:
            (content, comments_outcome). A failed read returns `content` as it was
            with `{"outcome": "fetch_failed", "detail": <exception type>}`.
        """
        discussion, outcome = fetch_discussion(
            hn_id, self.config.hackernews_max_comments
        )
        if outcome:
            return content, outcome

        if is_item_link(url, hn_id) and discussion.text:
            content = discussion.text
        elif discussion.comments and not is_readable(content):
            content = format_reddit_link_only(url)
        return content + format_discussion(discussion.comments), None

    def _fallback_content(self, entry: dict) -> str:
        """The feed entry's own content/summary/description, or the shared
        no-content placeholder when none of those carry anything either."""
        fallback_content = ""

        # Try content field first
        if entry.get("content"):
            # content can be a list of dicts
            if isinstance(entry["content"], list) and entry["content"]:
                fallback_content = entry["content"][0].get("value", "")
            else:
                fallback_content = str(entry["content"])

        # Try summary field
        if not fallback_content and entry.get("summary"):
            fallback_content = entry["summary"]

        # Try description field
        if not fallback_content and entry.get("description"):
            fallback_content = entry["description"]

        return fallback_content or RSS_NO_CONTENT_FALLBACK

    def __del__(self):
        """Cleanup HTTP client on deletion."""
        if hasattr(self, "client"):
            self.client.close()
