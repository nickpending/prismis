"""API client for CLI to communicate with daemon."""

import os
import tomllib
from pathlib import Path
from typing import Any

import httpx

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

    def _send(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
    ) -> httpx.Response:
        """Send an HTTP request to the daemon and return the raw response.

        The single place every method opens its `httpx.Client`, builds the
        request URL, attaches the API key header, and wraps `httpx.RequestError`
        and any other exception raised while sending into `RuntimeError`. Does
        not parse the body or inspect the status code — callers that want the
        standard status/success/JSON handling use `_send_json`; `get_entry_raw`
        reads `response.text` directly instead, since its endpoint is not JSON.

        Args:
            method: HTTP method (GET, POST, PATCH, DELETE)
            path: URL path appended to `self.base_url`
            json: Optional JSON request body
            params: Optional query parameters
            timeout: Optional per-call timeout override; defaults to `self.timeout`

        Returns:
            The raw httpx.Response

        Raises:
            RuntimeError: On a network error or any other failure to send
        """
        try:
            with httpx.Client(timeout=timeout or self.timeout) as client:
                return client.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json,
                    params=params,
                    headers={"X-API-Key": self.api_key},
                )
        except httpx.RequestError as e:
            raise RuntimeError(f"Network error: {e}") from e
        except Exception as e:
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Unexpected error: {e}") from e

    def _send_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: httpx.Timeout | None = None,
        check_success: bool = True,
    ) -> dict[str, Any]:
        """Send a request via `_send`, parse its JSON body, and raise on failure.

        Always raises `RuntimeError` on a >=400 status, using the body's
        `message` field when present. When `check_success` is True (the
        default, and every caller except `count_unprioritized`,
        `prune_unprioritized`, and `get_report`), also raises when the body's
        `success` flag is falsy — those three callers' original bodies never
        made that check, so it stays optional rather than folded into every
        caller's behavior.

        Takes the same `method`/`path`/`json`/`params`/`timeout` arguments as
        `_send`, plus:

        Args:
            check_success: Whether to also raise on a falsy `success` field

        Returns:
            The parsed JSON response body

        Raises:
            RuntimeError: On a network error, a >=400 status, a falsy `success`
                field (when `check_success`), or any other failure
        """
        response = self._send(method, path, json=json, params=params, timeout=timeout)
        try:
            data = response.json()
            if response.status_code >= 400:
                error_msg = data.get("message", f"API error: {response.status_code}")
                raise RuntimeError(error_msg)
            if check_success and not data.get("success"):
                raise RuntimeError(data.get("message", "Unknown error"))
            return data
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Unexpected error: {e}") from e

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
        data = self._send_json(
            "POST",
            "/api/sources",
            json={"url": url, "type": source_type, "name": name},
        )
        return data.get("data", {})

    def remove_source(self, source_id: str) -> bool:
        """Remove a source via API.

        Args:
            source_id: UUID of source to remove

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        self._send_json("DELETE", f"/api/sources/{source_id}")
        return True

    def pause_source(self, source_id: str) -> bool:
        """Pause a source via API.

        Args:
            source_id: UUID of source to pause

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        self._send_json("PATCH", f"/api/sources/{source_id}/pause")
        return True

    def resume_source(self, source_id: str) -> bool:
        """Resume a source via API.

        Args:
            source_id: UUID of source to resume

        Returns:
            True if successful

        Raises:
            RuntimeError: If API request fails
        """
        self._send_json("PATCH", f"/api/sources/{source_id}/resume")
        return True

    def count_unprioritized(self, days: int | None = None) -> int:
        """Count unprioritized content items.

        Args:
            days: Optional age filter - only count items older than this many days

        Returns:
            Count of unprioritized items

        Raises:
            RuntimeError: If API request fails
        """
        params = {"days": days} if days is not None else {}
        data = self._send_json(
            "GET", "/api/prune/count", params=params, check_success=False
        )
        return data.get("data", {}).get("count", 0)

    def prune_unprioritized(self, days: int | None = None) -> dict:
        """Delete unprioritized content items.

        Args:
            days: Optional age filter - only delete items older than this many days

        Returns:
            Dict with count of deleted items

        Raises:
            RuntimeError: If API request fails
        """
        params = {"days": days} if days is not None else {}
        return self._send_json("POST", "/api/prune", params=params, check_success=False)

    def get_report(self, period: str = "24h") -> str:
        """Generate a content report for the specified period.

        Args:
            period: Time period like "24h", "7d", "30d"

        Returns:
            Markdown formatted report

        Raises:
            RuntimeError: If API request fails
        """
        data = self._send_json(
            "GET", "/api/reports", params={"period": period}, check_success=False
        )
        return data.get("data", {}).get("markdown", "")

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
        self._send_json("PATCH", f"/api/sources/{source_id}", json={"name": name})
        return True

    def get_entry(self, entry_id: str) -> dict[str, Any]:
        """Get a single content entry by ID (summary without content field).

        Args:
            entry_id: UUID of the content entry

        Returns:
            Entry metadata dictionary (excludes content field)

        Raises:
            RuntimeError: If API request fails or entry not found
        """
        data = self._send_json("GET", f"/api/entries/{entry_id}")
        return data.get("data", {})

    def get_entry_raw(self, entry_id: str) -> str:
        """Get raw content of a single entry as plain text.

        Args:
            entry_id: UUID of the content entry

        Returns:
            Raw content text (suitable for piping)

        Raises:
            RuntimeError: If API request fails or entry not found
        """
        response = self._send("GET", f"/api/entries/{entry_id}/raw")

        # Raw endpoint returns plain text, not JSON - use _send only, never
        # _send_json, which would try to parse this as JSON and check `success`.
        if response.status_code >= 400:
            raise RuntimeError(f"Entry not found or API error: {response.status_code}")

        return response.text

    def get_content(
        self,
        priority: str | None = None,
        unread_only: bool = False,
        archive_filter: str = "exclude",
        limit: int = 50,
        source: str | None = None,
        compact: bool = False,
        since_hours: int | None = None,
        kind: str | None = None,
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
            kind: Filter by kind, single value or comma-separated list

        Returns:
            List of content item dictionaries

        Raises:
            RuntimeError: If API request fails
        """
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
        if kind:
            params["kind"] = kind

        # Map archive_filter to API parameters
        if archive_filter == "only":
            params["archived_only"] = True
        elif archive_filter == "include":
            params["include_archived"] = True
        # 'exclude' is the default (no parameter needed)

        data = self._send_json("GET", "/api/entries", params=params)
        return data.get("data", {}).get("items", [])

    def get_archive_status(self) -> dict:
        """Get archival status from API.

        Returns:
            Dict with archival configuration and stats

        Raises:
            RuntimeError: If API request fails
        """
        data = self._send_json("GET", "/api/archive/status")
        return data.get("data", {})

    def search(
        self,
        query: str,
        limit: int = 20,
        compact: bool = False,
        source: str | None = None,
        min_score: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search content using semantic similarity.

        Args:
            query: Search query string
            limit: Maximum number of results to return (1-50)
            compact: Return compact format (excludes content and analysis)
            source: Filter by source name (case-insensitive substring match)
            min_score: Minimum relevance score override (None uses server default)

        Returns:
            List of content items with relevance scores

        Raises:
            RuntimeError: If API request fails
        """
        params: dict[str, Any] = {"q": query, "limit": limit}
        if compact:
            params["compact"] = True
        if source:
            params["source"] = source
        if min_score is not None:
            params["min_score"] = min_score

        data = self._send_json("GET", "/api/search", params=params)
        return data.get("data", {}).get("items", [])

    def get_statistics(self) -> dict[str, Any]:
        """Get system-wide statistics from API.

        Returns:
            Dict with content and source statistics

        Raises:
            RuntimeError: If API request fails
        """
        data = self._send_json("GET", "/api/statistics")
        return data.get("data", {})

    def get_sources(self) -> list[dict[str, Any]]:
        """Get all configured sources via API.

        Returns:
            List of source dictionaries

        Raises:
            RuntimeError: If API request fails
        """
        data = self._send_json("GET", "/api/sources")
        return data.get("data", {}).get("sources", [])

    def extract_entry(self, entry_id: str) -> dict[str, Any]:
        """Trigger deep extraction for a content entry.

        Calls POST /api/entries/{id}/extract. Idempotent (server-side check):
        items with existing analysis.deep_extraction are returned without
        another LLM call.

        Uses a local 120s timeout override because LLM extraction may exceed
        the 30s class-level default for large documents on slow services.

        Args:
            entry_id: UUID of the content entry to extract

        Returns:
            Dict containing the analysis envelope (includes deep_extraction key)

        Raises:
            RuntimeError: If API request fails
        """
        data = self._send_json(
            "POST",
            f"/api/entries/{entry_id}/extract",
            timeout=httpx.Timeout(120.0),
        )
        return data.get("data", {})
