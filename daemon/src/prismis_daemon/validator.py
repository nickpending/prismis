"""Source validation module for verifying sources before adding to database."""

import re
from typing import Tuple, Optional
from urllib.parse import urlparse

import httpx
import feedparser


class SourceValidator:
    """Validates content sources before adding them to the database.

    All validation methods return (is_valid, error_message) tuples.
    Network requests have a 5-second timeout to prevent hanging.
    """

    def __init__(self) -> None:
        """Initialize the validator with default timeout settings."""
        self.timeout = 5.0  # 5 second timeout for all network requests
        self.user_agent = "Prismis/1.0 (Content Aggregator)"

    def validate_source(
        self, url: str, source_type: str
    ) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate a source URL based on its type.

        Args:
            url: The source URL to validate
            source_type: Type of source ('rss', 'reddit', 'youtube')

        Returns:
            Tuple of (is_valid, error_message, metadata)
            - is_valid: True if source is valid
            - error_message: None if valid, error description if invalid
            - metadata: Optional dict with source-specific metadata (e.g., display_name)
        """
        try:
            if source_type == "rss":
                return self._validate_rss(url)
            elif source_type == "reddit":
                return self._validate_reddit(url)
            elif source_type == "youtube":
                return self._validate_youtube(url)
            elif source_type == "file":
                return self._validate_file(url)
            else:
                return False, f"Unknown source type: {source_type}", None
        except Exception as e:
            return False, f"Validation failed: {str(e)}", None

    def _validate_rss(self, url: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate an RSS/Atom feed URL.

        Fetches the feed and checks if it's valid XML with entries.

        Args:
            url: The RSS/Atom feed URL

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Fetch the feed with timeout
            response = httpx.get(
                url,
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": self.user_agent},
            )

            # Check HTTP status
            if response.status_code != 200:
                return (
                    False,
                    f"HTTP {response.status_code}: {response.reason_phrase}",
                    None,
                )

            # Parse the feed
            feed = feedparser.parse(response.text)

            # Check if feed is malformed
            if feed.bozo:
                # Some feeds have minor issues but are still usable
                # Only fail if there are no entries at all
                if not hasattr(feed, "entries") or len(feed.entries) == 0:
                    error = getattr(feed, "bozo_exception", "Invalid RSS/Atom feed")
                    return False, f"Invalid feed format: {error}", None

            # Check if feed has entries
            if not hasattr(feed, "entries"):
                return False, "Feed has no entries attribute", None

            if len(feed.entries) == 0:
                # Empty feed is technically valid but warn user
                return True, None, None  # Allow empty feeds, they might populate later

            # Feed is valid
            return True, None, None

        except httpx.TimeoutException:
            return False, "Request timed out after 5 seconds", None
        except httpx.RequestError as e:
            return False, f"Network error: {str(e)}", None
        except Exception as e:
            return False, f"RSS validation error: {str(e)}", None

    def _validate_reddit(self, url: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate a Reddit subreddit URL.

        Checks if the subreddit exists via Reddit's JSON API.

        Args:
            url: The Reddit URL (supports reddit.com/r/NAME and reddit://NAME formats)

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Extract subreddit name from various URL formats
            subreddit = None

            # Handle reddit:// protocol
            if url.startswith("reddit://"):
                subreddit = url.replace("reddit://", "").strip("/")
            # Handle standard reddit.com URLs
            elif "reddit.com/r/" in url:
                # Extract subreddit name from URL
                match = re.search(r"/r/([^/\?]+)", url)
                if match:
                    subreddit = match.group(1)
            # Handle old.reddit.com URLs
            elif "old.reddit.com/r/" in url:
                match = re.search(r"/r/([^/\?]+)", url)
                if match:
                    subreddit = match.group(1)
            # Handle just subreddit name
            elif "/" not in url and "." not in url:
                subreddit = url

            if not subreddit:
                return False, "Could not extract subreddit name from URL", None

            # Check if subreddit exists via about.json endpoint
            check_url = f"https://www.reddit.com/r/{subreddit}/about.json"

            response = httpx.get(
                check_url,
                timeout=self.timeout,
                headers={"User-Agent": self.user_agent},
                follow_redirects=True,
            )

            # Check response status
            if response.status_code == 404:
                return False, f"Subreddit r/{subreddit} does not exist", None
            elif response.status_code == 403:
                # Private subreddit - exists but not accessible
                return False, f"Subreddit r/{subreddit} is private", None
            elif response.status_code == 429:
                return False, "Reddit rate limit exceeded - try again later", None
            elif response.status_code != 200:
                return (
                    False,
                    f"HTTP {response.status_code}: Could not verify subreddit",
                    None,
                )

            # Parse JSON response
            try:
                data = response.json()
                # Check if it's actually a subreddit response
                if data.get("kind") == "t5" or (
                    data.get("data", {}).get("subreddit_type")
                ):
                    # Extract the display name with proper capitalization
                    metadata = {}
                    if "data" in data:
                        # Get display_name_prefixed (e.g., "r/ChatGPT")
                        display_name = data["data"].get("display_name_prefixed")
                        if display_name:
                            metadata["display_name"] = display_name
                    return True, None, metadata
                else:
                    return False, "Invalid subreddit response format", None
            except Exception:
                return False, "Could not parse Reddit API response", None

        except httpx.TimeoutException:
            return False, "Request timed out after 5 seconds", None
        except httpx.RequestError as e:
            return False, f"Network error: {str(e)}", None
        except Exception as e:
            return False, f"Reddit validation error: {str(e)}", None

    def _validate_youtube(self, url: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate a YouTube channel/user URL.

        Only validates URL format to avoid YouTube API quota consumption.
        Does not verify if the channel actually exists.

        Args:
            url: The YouTube URL

        Returns:
            Tuple of (is_valid, error_message)
        """
        try:
            # Handle youtube:// protocol
            if url.startswith("youtube://"):
                # Format: youtube://channel_id or youtube://@handle
                channel_part = url.replace("youtube://", "").strip("/")
                if channel_part.startswith("@") or channel_part.startswith("UC"):
                    return True, None, None, None
                else:
                    return False, "Invalid YouTube channel ID or handle", None

            # Parse standard YouTube URLs
            parsed = urlparse(url)

            # Check if it's a YouTube domain
            valid_domains = ["youtube.com", "www.youtube.com", "m.youtube.com"]
            if parsed.netloc not in valid_domains:
                return False, f"Not a YouTube URL: {parsed.netloc}", None

            # Check path patterns for channels
            path = parsed.path.lower()

            # Valid YouTube channel URL patterns
            valid_patterns = [
                r"^/c/[^/]+",  # youtube.com/c/ChannelName
                r"^/channel/[^/]+",  # youtube.com/channel/UCxxxxxx (channel ID)
                r"^/@[^/]+",  # youtube.com/@handle
                r"^/user/[^/]+",  # youtube.com/user/Username (legacy)
            ]

            # Check if URL matches any valid pattern
            for pattern in valid_patterns:
                if re.match(pattern, path):
                    return True, None, None

            # Check if it's a video or playlist URL (not supported)
            if "/watch" in path or "/playlist" in path:
                return (
                    False,
                    "Video and playlist URLs not supported - please provide channel URL",
                    None,
                )

            return False, "Invalid YouTube channel URL format", None

        except Exception as e:
            return False, f"YouTube validation error: {str(e)}", None

    def _validate_file(self, url: str) -> Tuple[bool, Optional[str], Optional[dict]]:
        """Validate a file URL for text/markdown content.

        Checks if URL ends with supported file extensions (.md, .txt).
        Does not fetch the file, only validates the URL format.

        Args:
            url: The file URL

        Returns:
            Tuple of (is_valid, error_message, metadata)
        """
        try:
            # Check for supported file extensions
            supported_extensions = (".md", ".txt")
            if not url.endswith(supported_extensions):
                return (
                    False,
                    f"File URL must end with {' or '.join(supported_extensions)}",
                    None,
                )

            # Basic URL validation
            if not url.startswith(("http://", "https://")):
                return False, "File URL must start with http:// or https://", None

            return True, None, None

        except Exception as e:
            return False, f"File validation error: {str(e)}", None
