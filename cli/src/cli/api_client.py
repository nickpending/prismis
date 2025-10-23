"""API client for CLI to communicate with daemon."""

import os
from typing import Optional, Dict, Any
from pathlib import Path

import httpx
import tomllib


class APIClient:
    """Client for communicating with Prismis daemon API."""

    def __init__(self):
        """Initialize API client with config."""
        self.base_url = "http://localhost:8989"
        self.api_key = self._load_api_key()
        self.timeout = httpx.Timeout(30.0)  # 30 second timeout for validation

    def _load_api_key(self) -> str:
        """Load API key from config file.

        Returns:
            API key from config

        Raises:
            RuntimeError: If config not found or API key missing
        """
        # XDG Base Directory support
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
            raise RuntimeError(f"Failed to parse config: {e}")

        api_key = config.get("api", {}).get("key")
        if not api_key:
            raise RuntimeError(
                "API key not found in config.toml\n"
                "Add [api] section with key = 'your-api-key'"
            )

        return api_key

    def add_source(
        self, url: str, source_type: str, name: Optional[str] = None
    ) -> Dict[str, Any]:
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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

    def count_unprioritized(self, days: Optional[int] = None) -> int:
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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

    def prune_unprioritized(self, days: Optional[int] = None) -> dict:
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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

    def get_entry(self, entry_id: str) -> Dict[str, Any]:
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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")

    def get_content(
        self,
        priority: Optional[str] = None,
        unread_only: bool = False,
        limit: int = 50,
    ) -> list[Dict[str, Any]]:
        """Get content items with optional filtering.

        Args:
            priority: Filter by priority level ('high', 'medium', 'low')
            unread_only: Only return unread items
            limit: Maximum number of items to return (1-100)

        Returns:
            List of content item dictionaries

        Raises:
            RuntimeError: If API request fails
        """
        with httpx.Client(timeout=self.timeout) as client:
            try:
                # Build query parameters
                params: Dict[str, Any] = {"limit": limit}
                if priority:
                    params["priority"] = priority
                if unread_only:
                    params["unread_only"] = True

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
                raise RuntimeError(f"Network error: {e}")
            except Exception as e:
                if isinstance(e, RuntimeError):
                    raise
                raise RuntimeError(f"Unexpected error: {e}")
