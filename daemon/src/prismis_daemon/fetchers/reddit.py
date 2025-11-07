"""Reddit content fetcher using PRAW."""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any

import praw

from ..models import ContentItem
from ..config import Config
from ..observability import log as obs_log

logger = logging.getLogger(__name__)


class RedditFetcher:
    """Fetches and processes Reddit content.

    Implements the plugin pattern for Reddit sources. Uses PRAW in read-only mode
    to fetch posts from subreddits and returns standardized ContentItem objects.
    """

    def __init__(self, max_items: int = None, config: Config = None):
        """Initialize the Reddit fetcher with PRAW client.

        Args:
            max_items: Maximum number of posts to fetch per subreddit (uses config if None)
            config: Config instance with Reddit credentials (loads from file if None)
        """
        # Load config if not provided
        if config is None:
            config = Config.from_file()

        self.max_items = max_items or config.get_max_items("reddit")
        self.config = config

        # Initialize PRAW with credentials from config
        try:
            self.reddit = praw.Reddit(
                client_id=config.reddit_client_id,
                client_secret=config.reddit_client_secret,
                user_agent=config.reddit_user_agent,
            )
            self.reddit.read_only = True
            logger.info("Reddit fetcher initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Reddit client: {e}")
            self.reddit = None

    def fetch_content(self, source: Dict[str, Any]) -> List[ContentItem]:
        """Fetch hot posts from a subreddit.

        Args:
            source: Source dict with 'url' (reddit URL) and 'id' (source UUID)

        Returns:
            List of ContentItem objects from Reddit posts

        Raises:
            Exception: If subreddit access fails
        """
        # Check if Reddit client is available
        if not self.reddit:
            raise Exception("Reddit client not initialized - check credentials")

        source_url = source.get("url", "")
        source_id = source.get("id", "")

        # Parse subreddit name from URL
        # Supports: https://reddit.com/r/python, reddit.com/r/python, /r/python, python
        subreddit_name = self._parse_subreddit_name(source_url)

        if not subreddit_name:
            raise ValueError(f"Could not parse subreddit from URL: {source_url}")

        items = []

        start_time = time.time()

        try:
            logger.info(f"Fetching posts from r/{subreddit_name}")
            subreddit = self.reddit.subreddit(subreddit_name)

            # Calculate cutoff date from config
            cutoff_date = datetime.now(timezone.utc) - timedelta(
                days=self.config.max_days_lookback
            )
            logger.debug(
                f"Filtering posts older than {cutoff_date} ({self.config.max_days_lookback} days)"
            )

            # Fetch hot posts with filtering
            post_count = 0
            filtered_count = 0
            for submission in subreddit.hot(
                limit=self.max_items + 50  # Extra for filtering old posts
            ):
                # Skip stickied posts
                if submission.stickied:
                    logger.debug(f"Skipping stickied post: {submission.title}")
                    continue

                # Skip image/video posts
                if self._is_image_post(submission):
                    logger.debug(f"Skipping image/video post: {submission.title}")
                    continue

                # Apply date filter
                post_date = None
                if hasattr(submission, "created_utc"):
                    try:
                        post_date = datetime.fromtimestamp(
                            submission.created_utc, tz=timezone.utc
                        )
                    except Exception as e:
                        logger.debug(f"Could not parse post date: {e}")

                # Skip posts older than cutoff
                if post_date and post_date < cutoff_date:
                    filtered_count += 1
                    logger.debug(
                        f"Skipping old post: {submission.title} (posted {post_date})"
                    )
                    continue

                # Convert to ContentItem
                item = self._to_content_item(submission, source_id)
                items.append(item)

                post_count += 1
                if post_count >= self.max_items:
                    break

            if filtered_count > 0:
                logger.info(
                    f"Filtered {filtered_count} old posts (older than {self.config.max_days_lookback} days)"
                )
            logger.info(
                f"Successfully fetched {len(items)} posts from r/{subreddit_name}"
            )

            # Log successful fetch
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.complete",
                fetcher_type="reddit",
                source_id=source_id,
                source_url=source_url,
                subreddit=subreddit_name,
                items_count=len(items),
                duration_ms=duration_ms,
                status="success",
            )

        except Exception as e:
            # Log fetch error
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "fetcher.error",
                fetcher_type="reddit",
                source_id=source_id,
                source_url=source_url,
                subreddit=subreddit_name,
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise Exception(
                f"Failed to fetch Reddit content from r/{subreddit_name}: {e}"
            ) from e

        return items

    def _parse_subreddit_name(self, url: str) -> str:
        """Parse subreddit name from various URL formats.

        Args:
            url: Reddit URL in various formats

        Returns:
            Subreddit name or empty string if parsing fails
        """
        # Remove protocol and www
        url = url.replace("https://", "").replace("http://", "").replace("www.", "")

        # Pattern to match subreddit names
        patterns = [
            r"reddit\.com/r/([a-zA-Z0-9_]+)",
            r"^r/([a-zA-Z0-9_]+)",
            r"^([a-zA-Z0-9_]+)$",  # Just the subreddit name
        ]

        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        return ""

    def _is_image_post(self, submission) -> bool:
        """Check if a Reddit post is primarily an image/video post.

        Args:
            submission: PRAW submission object

        Returns:
            True if post is image/video, False if text content
        """
        # Check if it's a text post (self post)
        if submission.is_self:
            return False

        # Check common image/video domains
        image_domains = [
            "i.redd.it",
            "i.imgur.com",
            "imgur.com",
            "gfycat.com",
            "v.redd.it",
            "youtube.com",
            "youtu.be",
            "streamable.com",
        ]

        url = submission.url.lower()
        for domain in image_domains:
            if domain in url:
                return True

        # Check file extensions
        image_extensions = [".jpg", ".jpeg", ".png", ".gif", ".webp", ".mp4", ".webm"]
        for ext in image_extensions:
            if url.endswith(ext):
                return True

        return False

    def _fetch_comments(self, submission) -> List[Dict[str, str]]:
        """Fetch top comments from a Reddit submission.

        Args:
            submission: PRAW submission object

        Returns:
            List of dicts with 'author' and 'body' (top-level only, filtered for deleted)
        """
        comments = []

        try:
            # Get max comments limit from config (0 = unlimited)
            max_comments = self.config.reddit_max_comments
            limit = None if max_comments == 0 else max_comments

            # Get flattened comment list (replace_more=0 to avoid fetching "load more" comments)
            submission.comments.replace_more(limit=0)
            comment_list = submission.comments.list()

            # Filter top-level comments only and skip deleted/removed
            for comment in comment_list:
                # Skip if not top-level (has parent that's not the submission)
                if not hasattr(
                    comment, "parent_id"
                ) or not comment.parent_id.startswith("t3_"):
                    continue

                # Skip deleted/removed comments
                if not hasattr(comment, "body") or comment.body in [
                    "[deleted]",
                    "[removed]",
                ]:
                    continue

                # Get author name (handle deleted authors)
                author = str(comment.author) if comment.author else "[deleted]"

                comments.append({"author": author, "body": comment.body})

                # Check limit
                if limit and len(comments) >= limit:
                    break

            logger.debug(f"Fetched {len(comments)} comments from {submission.id}")

        except Exception as e:
            logger.warning(f"Failed to fetch comments for {submission.id}: {e}")
            # Return empty list on error - don't fail the whole fetch
            return []

        return comments

    def _extract_metrics(self, submission) -> Dict[str, Any]:
        """Extract Reddit-specific metrics from a post.

        Args:
            submission: PRAW submission object

        Returns:
            Dictionary with score, upvote_ratio, num_comments
        """
        return {
            "score": getattr(submission, "score", 0),
            "upvote_ratio": getattr(submission, "upvote_ratio", 0.0),
            "num_comments": getattr(submission, "num_comments", 0),
            "subreddit": str(submission.subreddit)
            if hasattr(submission, "subreddit")
            else None,
            "author": str(submission.author) if submission.author else "[deleted]",
        }

    def _to_content_item(self, submission, source_id: str) -> ContentItem:
        """Convert Reddit submission to ContentItem.

        Args:
            submission: PRAW submission object
            source_id: UUID of the source in database

        Returns:
            Standardized ContentItem object
        """
        # Generate external_id from permalink (unique across Reddit)
        external_id = f"https://reddit.com{submission.permalink}"

        # Get title
        title = submission.title

        # URL is the Reddit post URL
        url = f"https://reddit.com{submission.permalink}"

        # For text posts, use selftext; for link posts, include URL in content
        content = ""
        if submission.is_self and submission.selftext:
            content = submission.selftext
        else:
            content = f"Link: {submission.url}\n\n"
            # If there's a text description with the link
            if hasattr(submission, "selftext") and submission.selftext:
                content += submission.selftext

        # Handle deleted/missing content
        if (
            not content
            or content.strip() == "[deleted]"
            or content.strip() == "[removed]"
        ):
            content = f"Link post to: {submission.url}"

        # Fetch and append comments to content for LLM enrichment
        comments = self._fetch_comments(submission)
        if comments:
            # Format comments as markdown discussion section with author attribution
            discussion = "\n\n## Discussion\n\n"
            formatted_comments = []
            for comment in comments:
                # Format as: **u/author:**\n> comment body (blockquote for clarity)
                formatted = f"**u/{comment['author']}:**\n> {comment['body']}"
                formatted_comments.append(formatted)
            discussion += "\n\n".join(formatted_comments)
            content += discussion
            logger.debug(f"Enriched content with {len(comments)} comments")

        # Parse published date (Reddit uses Unix timestamp) - make timezone-aware
        published_at = None
        if hasattr(submission, "created_utc"):
            try:
                published_at = datetime.fromtimestamp(
                    submission.created_utc, tz=timezone.utc
                )
            except Exception as e:
                logger.debug(f"Could not parse date for {external_id}: {e}")

        # Extract metrics
        metrics = self._extract_metrics(submission)

        # Create ContentItem with metrics in analysis field
        item = ContentItem(
            source_id=source_id,
            external_id=external_id,
            title=title,
            url=url,
            content=content,
            published_at=published_at,
            fetched_at=datetime.utcnow(),
            analysis={"metrics": metrics},  # Store Reddit metrics here
        )

        return item
