"""Source validation module for verifying sources before adding to database."""

import re
import time
from collections.abc import Mapping
from urllib.parse import urlparse

import feedparser
import httpx
import praw
import requests
from prawcore import exceptions as prawcore_exceptions
from prawcore.sessions import FiniteRetryStrategy
from praw.models import Subreddit
from requests.adapters import HTTPAdapter

from .config import Config

# Absent credentials and refused credentials are two different answers that send the
# operator to two different places. This message covers only the first.
REDDIT_NOT_CONFIGURED = (
    "Reddit credentials not configured - set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET"
)


class _SingleAttemptRetry(FiniteRetryStrategy):
    """prawcore's retry strategy fixed at one attempt.

    prawcore sleeps for up to several seconds between its three default attempts, and
    that sleep sits outside the session where this validator's deadline is enforced. The
    only way the stated budget holds is for there to be nothing to sleep between.
    """

    def __init__(self, retries: int = 1) -> None:
        super().__init__(retries=min(retries, 1))


class _DeadlineAdapter(HTTPAdapter):
    """A transport adapter holding every request inside one wall-clock deadline.

    prawcore computes its 16-second default at import time and binds it as a default
    argument value, so neither setting its environment variable at runtime nor replacing
    the constant reaches a call made afterwards. The transport is the only remaining
    place the validator's own budget can be imposed on the request itself. The deadline
    spans the whole probe rather than each request, because one probe costs two calls:
    the access token, then the subreddit.
    """

    def __init__(self, budget: float) -> None:
        super().__init__()
        self._deadline = time.monotonic() + budget

    def send(
        self,
        request: requests.PreparedRequest,
        stream: bool = False,
        timeout: float | tuple[float, float] | tuple[float, None] | None = None,
        verify: bool | str = True,
        cert: str | tuple[str, str] | None = None,
        proxies: Mapping[str, str] | None = None,
    ) -> requests.Response:
        """Send a request bounded by whatever is left of the deadline."""
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise requests.exceptions.ConnectTimeout(
                "Reddit validation budget exhausted", request=request
            )
        if not isinstance(timeout, float | int) or timeout > remaining:
            timeout = remaining
        return super().send(
            request,
            stream=stream,
            timeout=timeout,
            verify=verify,
            cert=cert,
            proxies=proxies,
        )


def _deadline_session(budget: float) -> requests.Session:
    """Build a requests session whose every call shares one wall-clock budget.

    Args:
        budget: Seconds the whole probe is allowed, across all of its requests

    Returns:
        A session mounted on the deadline-bounded transport
    """
    session = requests.Session()
    adapter = _DeadlineAdapter(budget)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


class SourceValidator:
    """Validates content sources before adding them to the database.

    All validation methods return (is_valid, error_message) tuples.
    Network requests have a 5-second timeout to prevent hanging.
    """

    def __init__(self, config: Config | None = None) -> None:
        """Initialize the validator with default timeout settings.

        Args:
            config: Config carrying the Reddit credentials. Optional because rss,
                youtube and file validation need none; without it the reddit path
                reports itself unconfigured instead of the whole validator failing.
        """
        self.timeout = 5.0  # 5 second timeout for all network requests
        self.user_agent = "Prismis/1.0 (Content Aggregator)"
        self.config = config

    def validate_source(
        self, url: str, source_type: str
    ) -> tuple[bool, str | None, dict | None]:
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

    def _validate_rss(self, url: str) -> tuple[bool, str | None, dict | None]:
        """Validate an RSS/Atom feed URL.

        Fetches the feed and checks if it's valid XML with entries.

        Args:
            url: The RSS/Atom feed URL

        Returns:
            Tuple of (is_valid, error_message, metadata)
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

    def _validate_reddit(self, url: str) -> tuple[bool, str | None, dict | None]:
        """Validate a Reddit subreddit URL against Reddit's authenticated API.

        Composed of three steps so that the two holding decisions can be exercised
        without a network: parse the URL, probe Reddit, interpret what came back.

        Args:
            url: The Reddit URL (supports reddit.com/r/NAME and reddit://NAME formats)

        Returns:
            Tuple of (is_valid, error_message, metadata)
        """
        subreddit_name = self._parse_subreddit(url)
        if not subreddit_name:
            return False, "Could not extract subreddit name from URL", None

        config, credential_error = self._reddit_credentials()
        if config is None:
            return False, credential_error, None

        outcome: object
        try:
            outcome = self._probe_subreddit(subreddit_name, config)
        except Exception as e:
            outcome = e

        return self._interpret_reddit_outcome(subreddit_name, outcome)

    def _parse_subreddit(self, url: str) -> str | None:
        """Extract a subreddit name from any URL form the daemon accepts.

        Args:
            url: reddit://NAME, a reddit.com or old.reddit.com /r/ URL, or a bare name

        Returns:
            The subreddit name, or None if the input names no subreddit
        """
        # Handle reddit:// protocol
        if url.startswith("reddit://"):
            return url.replace("reddit://", "").strip("/") or None

        # Handle standard and old reddit.com URLs
        if "reddit.com/r/" in url or "old.reddit.com/r/" in url:
            match = re.search(r"/r/([^/\?]+)", url)
            return match.group(1) if match else None

        # Handle just subreddit name
        if url and "/" not in url and "." not in url:
            return url

        return None

    def _reddit_credentials(self) -> tuple[Config | None, str | None]:
        """Return the config to probe with, or the message explaining its absence.

        An unset environment variable leaves the literal env: placeholder in place,
        which is truthy. Handing that to Reddit earns a 401 and reports credentials as
        invalid on a machine that simply has none — collapsing the two answers the
        caller most needs to tell apart.

        Returns:
            Tuple of (config, error_message); exactly one of the two is None
        """
        config = self.config
        if config is None:
            return None, REDDIT_NOT_CONFIGURED
        for value in (config.reddit_client_id, config.reddit_client_secret):
            if not value or value.startswith("env:"):
                return None, REDDIT_NOT_CONFIGURED
        return config, None

    def _build_reddit_client(self, config: Config) -> praw.Reddit:
        """Build a read-only PRAW client bounded by this validator's timeout.

        Passing `check_for_updates=False` matters before any subreddit is touched:
        PRAW's constructor otherwise reaches pypi.org. The retry strategy is cut to a
        single attempt because prawcore's default sleeps between its three, and that
        sleep happens outside the session where the deadline is enforced.

        Args:
            config: Config carrying usable Reddit credentials

        Returns:
            A PRAW client whose every request is held inside the timeout budget
        """
        reddit = praw.Reddit(
            client_id=config.reddit_client_id,
            client_secret=config.reddit_client_secret,
            user_agent=config.reddit_user_agent,
            check_for_updates=False,
            requestor_kwargs={"session": _deadline_session(self.timeout)},
        )
        reddit.read_only = True

        # PRAW exposes no retry knob; the strategy lives on the prawcore session it
        # builds in its constructor, which is why this reaches past the public surface.
        core = reddit._core
        if core is None:
            raise RuntimeError("PRAW built no core session")
        core._retry_strategy_class = _SingleAttemptRetry

        return reddit

    def _probe_subreddit(self, name: str, config: Config) -> Subreddit:
        """Fetch a subreddit, forcing the request PRAW would otherwise defer.

        PRAW hands back a lazy handle and reaches Reddit only on attribute access, so
        the identifier read here is the probe itself rather than a spare field.

        Args:
            name: The subreddit name
            config: Config carrying usable Reddit credentials

        Returns:
            The fetched subreddit

        Raises:
            prawcore.exceptions.PrawcoreException: On any refusal or network failure
        """
        reddit = self._build_reddit_client(config)
        subreddit = reddit.subreddit(name)
        _ = subreddit.id
        return subreddit

    def _interpret_reddit_outcome(
        self, name: str, outcome: object
    ) -> tuple[bool, str | None, dict | None]:
        """Map what the probe returned or raised onto a validation result.

        Every prawcore failure is routed to a message naming its own cause. Anything
        left unrouted falls through to a blind except that this repo suppresses the lint
        for, so four different answers would arrive as one identical string.

        Args:
            name: The subreddit name that was probed
            outcome: The fetched subreddit, or the exception the probe raised

        Returns:
            Tuple of (is_valid, error_message, metadata)
        """
        if not isinstance(outcome, Exception):
            metadata = {}
            display_name = getattr(outcome, "display_name_prefixed", None)
            if display_name:
                metadata["display_name"] = display_name
            return True, None, metadata

        if isinstance(
            outcome, prawcore_exceptions.NotFound | prawcore_exceptions.Redirect
        ):
            return False, f"Subreddit r/{name} does not exist", None
        if isinstance(outcome, prawcore_exceptions.Forbidden):
            return False, f"Subreddit r/{name} is private or quarantined", None
        if isinstance(outcome, prawcore_exceptions.UnavailableForLegalReasons):
            return False, f"Subreddit r/{name} is unavailable for legal reasons", None
        if isinstance(outcome, prawcore_exceptions.TooManyRequests):
            return False, "Reddit rate limit exceeded - try again later", None
        if isinstance(outcome, prawcore_exceptions.ResponseException):
            status = outcome.response.status_code
            if status == 401:
                return False, "Reddit credentials are invalid or expired", None
            return False, f"Reddit API returned HTTP {status}", None
        if isinstance(outcome, prawcore_exceptions.RequestException):
            return (
                False,
                f"Network error contacting Reddit: {outcome.original_exception}",
                None,
            )
        if isinstance(outcome, prawcore_exceptions.PrawcoreException):
            return False, f"Reddit API error: {type(outcome).__name__}", None

        return False, f"Reddit validation error: {str(outcome)}", None

    def _validate_youtube(self, url: str) -> tuple[bool, str | None, dict | None]:
        """Validate a YouTube channel/user URL.

        Only validates URL format to avoid YouTube API quota consumption.
        Does not verify if the channel actually exists.

        Args:
            url: The YouTube URL

        Returns:
            Tuple of (is_valid, error_message, metadata)
        """
        try:
            # Handle youtube:// protocol
            if url.startswith("youtube://"):
                # Format: youtube://channel_id or youtube://@handle
                channel_part = url.replace("youtube://", "").strip("/")
                if channel_part.startswith("@") or channel_part.startswith("UC"):
                    return True, None, None
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

    def _validate_file(self, url: str) -> tuple[bool, str | None, dict | None]:
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
