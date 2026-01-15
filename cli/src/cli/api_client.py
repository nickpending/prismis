"""API client for CLI to communicate with daemon."""

import os
from pathlib import Path
from typing import Any

import httpx
import tomllib

from cli.remote import get_remote_key, get_remote_url, is_remote_mode


class APIClient:
    """Client for communicating with Prismis daemon API."""

    def __init__(self):
        """Initialize API client with config."""
        self.base_url = get_remote_url()
        self.api_key = self._load_api_key()
        self.timeout = httpx.Timeout(30.0)  # 30 second timeout for validation

    def _load_api_key(self) -> str:
        """Load API key from config file.

        Uses [remote].key if in remote mode, otherwise [api].key.

        Returns:
            API key from config

        Raises:
            RuntimeError: If config not found or API key missing
        """
        # Check for remote mode first
        if is_remote_mode():
            remote_key = get_remote_key()
            if remote_key:
                return remote_key
            raise RuntimeError(
                "Remote mode configured but [remote].key not set in config.toml"
            )

        # Local mode: load from [api].key
        xdg_config_home = os.environ.get(
            "XDG_CONFIG_HOME", str(Path.home() / ".config")
        )
        config_path = Path(xdg_config_home) / "prismis" / "config.toml"

        if not config_path.exists():
            raise RuntimeError(
                f"Config file not found at {config_path}\n"
                "Run 'make install-config' to create default configuration, or create config.toml manually."
            )

        try:
            with open(config_path, "rb") as f:
                config = tomllib.load(f)
        except Exception as e:
            raise RuntimeError(f"Failed to parse config: {e}") from e

        api_key = config.get("api", {}).get("key")
        if not api_key:
            raise RuntimeError(
                "API key not found in config.toml\n"
                "Add [api] section with key = 'your-api-key'"
            )

        return api_key

    def add_source(
        self, url: str, source_type: str, name: str | None = None
    ) -> dict[str, Any]:
        """Add a new source via API.

        Args:
            url: Source URL
            source_type: Type of source (rss, reddit, youtube)
            name: Optional custom name

        Returns:
            API response data

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.post(
                    f"{self.base_url}/api/sources",
                    json={"url": url, "type": source_type, "name": name},
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {})

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def remove_source(self, source_id: str) -> bool:
        """Remove a source via API.

        Args:
            source_id: UUID of source to remove

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.delete(
                    f"{self.base_url}/api/sources/{source_id}",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return True

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def pause_source(self, source_id: str) -> bool:
        """Pause a source via API.

        Args:
            source_id: UUID of source to pause

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.patch(
                    f"{self.base_url}/api/sources/{source_id}/pause",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return True

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def resume_source(self, source_id: str) -> bool:
        """Resume a source via API.

        Args:
            source_id: UUID of source to resume

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.patch(
                    f"{self.base_url}/api/sources/{source_id}/resume",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return True

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def count_unprioritized(self, days: int | None = None) -> int:
        """Count unprioritized content items.

        Args:
            days: Optional age filter - only count items older than this many days

        Returns:
            Count of unprioritized items

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                params = {"days": days} if days is not None else {}
                response = client.get(
                    f"{self.base_url}/api/prune/count",
                    headers={"X-API-Key": self.api_key},
                    params=params,
                )

                data = response.json()

                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                return data.get("data", {}).get("count", 0)

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def prune_unprioritized(self, days: int | None = None) -> dict:
        """Delete unprioritized content items.

        Args:
            days: Optional age filter - only delete items older than this many days

        Returns:
            Dict with count of deleted items

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                params = {"days": days} if days is not None else {}
                response = client.post(
                    f"{self.base_url}/api/prune",
                    headers={"X-API-Key": self.api_key},
                    params=params,
                )

                data = response.json()

                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                return data

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_report(self, period: str = "24h") -> str:
        """Generate a content report for the specified period.

        Args:
            period: Time period like "24h", "7d", "30d"

        Returns:
            Markdown formatted report

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.get(
                    f"{self.base_url}/api/reports",
                    headers={"X-API-Key": self.api_key},
                    params={"period": period},
                )

                data = response.json()

                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                return data.get("data", {}).get("markdown", "")

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def edit_source(self, source_id: str, name: str) -> bool:
        """Edit a source's name via API.

        Args:
            source_id: UUID of source to edit
            name: New name for the source

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.patch(
                    f"{self.base_url}/api/sources/{source_id}",
                    json={"name": name},
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return True

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_entry(self, entry_id: str) -> dict[str, Any]:
        """Get a single content entry by ID (summary without content field).

        Args:
            entry_id: UUID of the content entry

        Returns:
            Entry metadata dictionary (excludes content field)

        Raises:
            RuntimeError: If API request fails or entry not found
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.get(
                    f"{self.base_url}/api/entries/{entry_id}",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {})

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_entry_raw(self, entry_id: str) -> str:
        """Get raw content of a single entry as plain text.

        Args:
            entry_id: UUID of the content entry

        Returns:
            Raw content text (suitable for piping)

        Raises:
            RuntimeError: If API request fails or entry not found
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                response = client.get(
                    f"{self.base_url}/api/entries/{entry_id}/raw",
                    headers={"X-API-Key": self.api_key},
                )

                # Raw endpoint returns plain text, not JSON
                if response.status_code >= 400:
                    raise RuntimeError(
                        f"Entry not found or API error: {response.status_code}"
                    )

                return response.text

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_content(
        self,
        priority: str | None = None,
        unread_only: bool = False,
        archive_filter: str = "exclude",
        limit: int = 50,
        source: str | None = None,
        compact: bool = False,
        since_hours: int | None = None,
    ) -> list[dict[str, Any]]:
        """Get content items with optional filtering.

        Args:
            priority: Filter by priority level ('high', 'medium', 'low')
            unread_only: Only return unread items
            archive_filter: Archive filtering ('exclude', 'only', 'include')
            limit: Maximum number of items to return (1-100)
            source: Filter by source name (case-insensitive substring match)
            compact: Return compact format (excludes content and analysis)
            since_hours: Only return items from last N hours

        Returns:
            List of content item dictionaries

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                # Build query parameters
                params: dict[str, Any] = {"limit": limit}
                if priority:
                    params["priority"] = priority
                if unread_only:
                    params["unread_only"] = True
                if source:
                    params["source"] = source
                if compact:
                    params["compact"] = True
                if since_hours:
                    params["since_hours"] = since_hours

                # Map archive_filter to API parameters
                if archive_filter == "only":
                    params["archived_only"] = True
                elif archive_filter == "include":
                    params["include_archived"] = True
                # 'exclude' is the default (no parameter needed)

                response = client.get(
                    f"{self.base_url}/api/entries",
                    headers={"X-API-Key": self.api_key},
                    params=params,
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {}).get("items", [])

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_archive_status(self) -> dict:
        """Get archival status from API.

        Returns:
            Dict with archival configuration and stats

        Raises:
            RuntimeError: If API request fails
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/archive/status",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {})

        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Unexpected error: {e}") from e

    def search(
        self,
        query: str,
        limit: int = 20,
        compact: bool = False,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search content using semantic similarity.

        Args:
            query: Search query string
            limit: Maximum number of results to return (1-50)
            compact: Return compact format (excludes content and analysis)
            source: Filter by source name (case-insensitive substring match)

        Returns:
            List of content items with relevance scores

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                params: dict[str, Any] = {"q": query, "limit": limit}
                if compact:
                    params["compact"] = True
                if source:
                    params["source"] = source

                response = client.get(
                    f"{self.base_url}/api/search",
                    headers={"X-API-Key": self.api_key},
                    params=params,
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {}).get("items", [])

            except httpx.RequestError as e:
                raise RuntimeError(f"Network error: {e}") from e
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}") from e

    def get_statistics(self) -> dict[str, Any]:
        """Get system-wide statistics from API.

        Returns:
            Dict with content and source statistics

        Raises:
            RuntimeError: If API request fails
        """
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.get(
                    f"{self.base_url}/api/statistics",
                    headers={"X-API-Key": self.api_key},
                )

                data = response.json()

                # Check for API errors
                if response.status_code >= 400:
                    error_msg = data.get(
                        "message", f"API error: {response.status_code}"
                    )
                    raise RuntimeError(error_msg)

                if not data.get("success"):
                    raise RuntimeError(data.get("message", "Unknown error"))

                return data.get("data", {})

        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Unexpected error: {e}") from e
