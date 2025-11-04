"""File/URL content monitoring fetcher for static text files."""

import difflib
import hashlib
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

import httpx

try:
    from ..config import Config
    from ..models import ContentItem
    from ..storage import Storage
except ImportError:
    from config import Config
    from models import ContentItem
    from storage import Storage

logger = logging.getLogger(__name__)


class FileFetcher:
    """Fetcher for monitoring static text files (CHANGELOG.md, docs, etc).

    Detects content changes via SHA256 hash comparison.
    Creates new entry per change with unified diff showing what changed.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        max_items: Optional[int] = None,
        timeout: int = 10,
        storage: Optional[Storage] = None,
    ):
        """Initialize file fetcher.

        Args:
            config: Configuration object (loaded from file if not provided)
            max_items: Maximum items to return per fetch (defaults to config)
            timeout: HTTP request timeout in seconds
            storage: Storage instance for querying previous content
        """
        if config is None:
            config = Config.from_file()
        self.config = config
        self.max_items = max_items or config.get_max_items("file")
        self.timeout = timeout
        self.storage = storage if storage else Storage()
        self.client = httpx.Client(timeout=timeout)

    def fetch_content(self, source: Dict[str, Any]) -> List[ContentItem]:
        """Fetch file content and detect changes.

        Args:
            source: Source dict with 'url' (file URL) and 'id' (source UUID)

        Returns:
            List with 0 or 1 ContentItem (only if content changed)
        """
        url = source.get("url")
        source_id = source.get("id")
        source_name = source.get("display_name", source.get("name", "File"))

        if not url or not source_id:
            logger.warning(f"Missing url or source_id for file source: {source}")
            return []

        # Fetch current content
        try:
            response = self.client.get(url)
            response.raise_for_status()

            # Validate content type (text/markdown only)
            content_type = response.headers.get("Content-Type", "")
            if "text" not in content_type and "markdown" not in content_type:
                logger.warning(
                    f"Skipping non-text file: {url} (Content-Type: {content_type})"
                )
                return []

            current_content = response.text

        except httpx.HTTPError as e:
            logger.warning(f"Failed to fetch {url}: {e}")
            return []

        # Calculate content hash
        current_hash = hashlib.sha256(current_content.encode()).hexdigest()

        # Check for previous version
        previous_entry = self.storage.get_latest_content_for_source(source_id)

        # If previous entry exists, check if content changed
        if previous_entry:
            previous_hash = (
                previous_entry.get("analysis", {}).get("content_hash")
                if previous_entry.get("analysis")
                else None
            )

            if previous_hash == current_hash:
                # Content unchanged - no new entry
                logger.debug(f"No changes detected for {url}")
                return []

            # Content changed - generate diff
            previous_content = (
                previous_entry.get("analysis", {}).get("full_text")
                if previous_entry.get("analysis")
                else previous_entry.get("content", "")
            )

            diff_text = self._generate_diff(previous_content, current_content, url)
            diff_stats = self._calculate_diff_stats(previous_content, current_content)

            # Create ContentItem with diff
            item = ContentItem(
                source_id=source_id,
                external_id=self._generate_external_id(url, current_hash),
                title=f"{source_name} Updated",
                url=url,
                content=diff_text,
                published_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                analysis={
                    "content_hash": current_hash,
                    "full_text": current_content,
                    "diff_stats": diff_stats,
                    "first_fetch": False,
                },
            )

            logger.info(
                f"Change detected in {url}: {diff_stats['added_lines']}+ {diff_stats['removed_lines']}- lines"
            )
            return [item]

        else:
            # First fetch - no previous version to diff against
            item = ContentItem(
                source_id=source_id,
                external_id=self._generate_external_id(url, current_hash),
                title=f"{source_name} Updated",
                url=url,
                content=current_content,
                published_at=datetime.now(timezone.utc),
                fetched_at=datetime.now(timezone.utc),
                analysis={
                    "content_hash": current_hash,
                    "full_text": current_content,
                    "first_fetch": True,
                },
            )

            logger.info(f"First fetch of {url} ({len(current_content)} chars)")
            return [item]

    def _generate_external_id(self, url: str, content_hash: str) -> str:
        """Generate unique external ID from URL and content hash.

        Args:
            url: File URL
            content_hash: SHA256 hash of content

        Returns:
            16-character hex string for deduplication
        """
        combined = f"{url}{content_hash}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]

    def _generate_diff(
        self, previous_content: str, current_content: str, url: str
    ) -> str:
        """Generate unified diff between two versions.

        Args:
            previous_content: Previous file content
            current_content: Current file content
            url: File URL (for diff header)

        Returns:
            Unified diff text
        """
        try:
            previous_lines = previous_content.splitlines(keepends=False)
            current_lines = current_content.splitlines(keepends=False)

            diff = difflib.unified_diff(
                previous_lines,
                current_lines,
                fromfile=f"{url} (previous)",
                tofile=f"{url} (current)",
                lineterm="",
            )

            return "\n".join(diff)

        except Exception as e:
            logger.warning(f"Diff generation failed for {url}: {e}")
            # Fallback to full current content
            return current_content

    def _calculate_diff_stats(
        self, previous_content: str, current_content: str
    ) -> Dict[str, int]:
        """Calculate diff statistics (lines added/removed).

        Args:
            previous_content: Previous file content
            current_content: Current file content

        Returns:
            Dict with added_lines, removed_lines, changed_lines counts
        """
        try:
            previous_lines = previous_content.splitlines(keepends=False)
            current_lines = current_content.splitlines(keepends=False)

            # Use difflib to calculate actual diff
            diff = list(
                difflib.unified_diff(previous_lines, current_lines, lineterm="", n=0)
            )

            added = sum(
                1
                for line in diff
                if line.startswith("+") and not line.startswith("+++")
            )
            removed = sum(
                1
                for line in diff
                if line.startswith("-") and not line.startswith("---")
            )

            return {
                "added_lines": added,
                "removed_lines": removed,
                "changed_lines": added + removed,
            }

        except Exception as e:
            logger.warning(f"Diff stats calculation failed: {e}")
            return {"added_lines": 0, "removed_lines": 0, "changed_lines": 0}

    def __del__(self):
        """Cleanup HTTP client on deletion."""
        if hasattr(self, "client"):
            self.client.close()
