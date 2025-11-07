"""RSS content fetcher with full article extraction."""

import hashlib
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import feedparser
import httpx
from trafilatura import extract, fetch_url

from ..models import ContentItem
from ..config import Config
from ..observability import log as obs_log

logger = logging.getLogger(__name__)


class RSSFetcher:
    """Fetches and processes RSS feed content with full article extraction.

    Implements the plugin pattern for content sources. Fetches RSS feeds,
    extracts full article content, and returns standardized ContentItem objects.
    """

    def __init__(self, max_items: int = None, config: Config = None, timeout: int = 30):
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

    def fetch_content(self, source: dict) -> List[ContentItem]:
        """Fetch RSS feed and extract full content for each item.

        Args:
            source: Source dict with 'url' and 'id' keys

        Returns:
            List of ContentItem objects with full content extracted

        Raises:
            Exception: If feed parsing fails (wrapped with context)
        """
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
            cutoff_date = datetime.now(timezone.utc) - timedelta(
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
                    title = entry.get("title", "Untitled")
                    url = entry.get("link", "")

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

                    # Extract full article content with trafilatura
                    content = self._extract_full_content(url, entry)

                    # Create ContentItem (use fetched_at if no published_at)
                    fetched_at = datetime.utcnow()
                    item = ContentItem(
                        source_id=source_id,
                        external_id=external_id,
                        title=title,
                        url=url,
                        content=content,
                        published_at=published_at or fetched_at,
                        fetched_at=fetched_at,
                    )

                    items.append(item)
                    logger.debug(f"Processed: {title} ({len(content)} chars)")

                    # Stop if we have enough items
                    if len(items) >= self.max_items:
                        break

                except Exception as e:
                    logger.error(
                        f"Error processing entry '{entry.get('title', 'Unknown')}': {e}"
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
        title = entry.get("title", str(datetime.utcnow()))
        return hashlib.sha256(title.encode()).hexdigest()[:16]

    def _parse_published_date(self, entry: dict) -> Optional[datetime]:
        """Parse published date from feed entry.

        Args:
            entry: Feed entry dict from feedparser

        Returns:
            Parsed datetime or None if not available
        """
        # feedparser provides parsed time tuple - convert to timezone-aware datetime
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                # Convert time tuple to timezone-aware datetime
                return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse published date: {e}")

        # Try updated date as fallback
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            try:
                # Convert time tuple to timezone-aware datetime
                return datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
            except Exception as e:
                logger.debug(f"Could not parse updated date: {e}")

        return None

    def _extract_full_content(self, url: str, entry: dict) -> str:
        """Extract full article content using trafilatura.

        Args:
            url: Article URL to fetch
            entry: Original feed entry (fallback for content)

        Returns:
            Full article text or summary/description as fallback
        """
        try:
            # Attempt to fetch and extract full content
            logger.debug(f"Extracting full content from: {url}")

            # Fetch the webpage (trafilatura doesn't support timeout param)
            downloaded = fetch_url(url)

            if downloaded:
                # Extract text content
                content = extract(
                    downloaded,
                    include_comments=False,
                    include_tables=True,
                    no_fallback=False,
                )

                if content:
                    logger.debug(f"Extracted {len(content)} chars from {url}")
                    return content

            logger.debug(f"Trafilatura extraction failed for {url}, using fallback")

        except Exception as e:
            logger.warning(f"Error extracting content from {url}: {e}")

        # Fallback to RSS content
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

        return fallback_content or "No content available"

    def __del__(self):
        """Cleanup HTTP client on deletion."""
        if hasattr(self, "client"):
            self.client.close()
