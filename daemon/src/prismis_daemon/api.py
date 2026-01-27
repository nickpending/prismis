"""REST API server for Prismis daemon."""

import os
import re
import time
from collections import defaultdict
from difflib import SequenceMatcher
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from rich.console import Console

from .api_errors import (
    APIError,
    NotFoundError,
    ServerError,
    ValidationError,
)
from .api_models import (
    APIResponse,
    ContentUpdateRequest,
    SourceRequest,
    SourceResponse,
)
from .audio import AudioScriptGenerator, LspeakTTSEngine
from .auth import verify_api_key
from .config import Config
from .context_analyzer import ContextAnalyzer
from .embeddings import Embedder
from .observability import log as obs_log
from .reports import ReportGenerator
from .storage import Storage
from .validator import SourceValidator

console = Console()

app = FastAPI(
    title="Prismis API",
    description="REST API for managing content sources",
    version="1.0.0",
)


@app.middleware("http")
async def log_requests(request: Request, call_next) -> Response:
    """Log API requests in same style as daemon output."""
    # Skip static files and health checks
    if request.url.path.startswith("/assets/") or request.url.path == "/health":
        return await call_next(request)

    # Log API requests
    if request.url.path.startswith("/api/"):
        start_time = time.time()
        response = await call_next(request)
        duration = (time.time() - start_time) * 1000

        client_ip = request.client.host if request.client else "unknown"

        # For list endpoints, try to extract item count from response
        count_info = ""
        item_count = None
        if (
            request.url.path in ["/api/entries", "/api/search"]
            and response.status_code == 200
        ):
            try:
                import json

                body = b""
                async for chunk in response.body_iterator:
                    body += chunk

                # Parse response to get count
                data = json.loads(body)
                if data.get("success") and "data" in data:
                    if "items" in data["data"]:
                        count = len(data["data"]["items"])
                        count_info = f" [{count} items]"
                        item_count = count

                # Rebuild response with same body
                from fastapi.responses import Response as FastAPIResponse

                response = FastAPIResponse(
                    content=body,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    media_type=response.media_type,
                )
            except Exception as e:
                console.print(
                    f"[dim red]Failed to parse response for item count: {e}[/dim red]"
                )

        # Include query parameters for debugging
        query_str = f"?{request.url.query}" if request.url.query else ""
        console.print(
            f"[dim]   📡 API: {request.method} {request.url.path}{query_str} from {client_ip} → {response.status_code}{count_info} ({duration:.0f}ms)[/dim]"
        )

        # Log to observability system
        obs_log(
            "api.request",
            method=request.method,
            path=request.url.path,
            query=request.url.query if request.url.query else None,
            client_ip=client_ip,
            status_code=response.status_code,
            duration_ms=int(duration),
            item_count=item_count,
        )

        return response

    return await call_next(request)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Handle our custom APIError exceptions with consistent format.

    Formats all errors as: {"success": false, "message": "...", "data": null}
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "data": None},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Handle FastAPI validation errors with consistent format.

    Converts FastAPI's default {"detail": [...]} format to our standard
    {"success": false, "message": "...", "data": null} format.
    """
    # Extract first error for simple message
    first_error = exc.errors()[0]
    field = " -> ".join(str(loc) for loc in first_error["loc"])

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "message": f"Validation error in {field}: {first_error['msg']}",
            "data": None,
        },
    )


# Dependency injection for Storage with proper cleanup
async def get_storage() -> Storage:
    """Dependency injection for Storage instances with cleanup.

    Uses FastAPI's yield dependency pattern to ensure database
    connections are properly closed after each request.
    """
    storage = Storage()
    try:
        yield storage
    finally:
        storage.close()


# Dependency injection for Config
async def get_config() -> Config:
    """Dependency injection for Config instances.

    Loads configuration from standard location.
    Config is stateless so no cleanup needed.
    """
    return Config.from_file()


# Configure CORS for local access only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files for webapp - MUST be last to avoid intercepting API routes
static_dir = Path(__file__).parent / "static"


def normalize_source_url(url: str, source_type: str) -> str:
    """Normalize special protocol URLs to real URLs.

    Args:
        url: The source URL (may include special protocols)
        source_type: The type of source

    Returns:
        Normalized real URL for storage
    """
    url = url.strip()

    if source_type == "reddit":
        if url.startswith("reddit://"):
            # Convert reddit://rust to https://www.reddit.com/r/rust
            subreddit = url[9:].strip("/")
            return f"https://www.reddit.com/r/{subreddit}"

    elif source_type == "youtube":
        if url.startswith("youtube://"):
            # Convert youtube:// URLs to real YouTube URLs
            channel = url[10:].strip("/")

            # Handle @username format
            if channel.startswith("@"):
                return f"https://www.youtube.com/{channel}"

            # Handle channel IDs (usually start with UC)
            if channel.startswith("UC"):
                return f"https://www.youtube.com/channel/{channel}"

            # Default to @handle format
            return f"https://www.youtube.com/@{channel}"

    # Handle RSS protocol
    elif source_type == "rss":
        if url.startswith("rss://"):
            # Convert rss://example.com/feed to https://example.com/feed
            feed_url = url[6:].lstrip("/")

            # Add https:// if not already present
            if not feed_url.startswith(("http://", "https://")):
                return f"https://{feed_url}"
            return feed_url

    # For already-normalized URLs, return as-is
    return url


def extract_name_from_url(url: str, source_type: str) -> str:
    """Extract a human-readable name from a URL.

    Args:
        url: The source URL (already normalized)
        source_type: The type of source

    Returns:
        A reasonable name extracted from the URL
    """
    # For Reddit
    if source_type == "reddit" and "/r/" in url:
        match = re.search(r"/r/([^/\?]+)", url)
        if match:
            return f"r/{match.group(1)}"

    # For YouTube
    if source_type == "youtube":
        if "@" in url:
            match = re.search(r"@([^/\?]+)", url)
            if match:
                return f"@{match.group(1)}"
        elif "/channel/" in url:
            # Use channel ID but truncate if too long
            match = re.search(r"/channel/([^/\?]+)", url)
            if match:
                channel_id = match.group(1)
                return channel_id[:20]

    # For RSS/generic, extract domain
    url_clean = re.sub(r"^https?://", "", url)
    url_clean = re.sub(r"^www\.", "", url_clean)
    domain = url_clean.split("/")[0].split("?")[0]

    return domain.split(".")[0].title() if "." in domain else domain


def normalize_title_for_comparison(title: str) -> str:
    """Normalize title for fuzzy comparison.

    Removes common prefixes, lowercases, and strips whitespace.
    """
    if not title:
        return ""
    # Lowercase and strip
    normalized = title.lower().strip()
    # Remove common prefixes like [Discussion], [R], etc.
    normalized = re.sub(r"^\[.*?\]\s*", "", normalized)
    # Remove leading articles
    normalized = re.sub(r"^(the|a|an)\s+", "", normalized)
    return normalized


def title_similarity(title1: str, title2: str) -> float:
    """Calculate similarity ratio between two titles.

    Uses SequenceMatcher for fuzzy matching.
    Returns a float between 0.0 (no match) and 1.0 (exact match).
    """
    norm1 = normalize_title_for_comparison(title1)
    norm2 = normalize_title_for_comparison(title2)
    if not norm1 or not norm2:
        return 0.0
    return SequenceMatcher(None, norm1, norm2).ratio()


def deduplicate_content(
    items: list[dict], similarity_threshold: float = 0.80
) -> list[dict]:
    """Deduplicate content items by fuzzy title matching.

    Groups items with similar titles and keeps the highest priority one as primary.
    Adds duplicate_count and duplicate_sources fields to grouped items.

    Args:
        items: List of content item dicts
        similarity_threshold: Minimum similarity ratio (0.0-1.0) to consider duplicate

    Returns:
        Deduplicated list with duplicate metadata added to primary items
    """
    if not items:
        return items

    priority_order = {"high": 0, "medium": 1, "low": 2, None: 3, "": 3}

    # Track which items have been grouped
    grouped_indices: set[int] = set()
    result: list[dict] = []

    for i, item in enumerate(items):
        if i in grouped_indices:
            continue

        # Find all similar items
        group = [item]
        group_sources = [item.get("source_name", "Unknown")]

        for j, other in enumerate(items[i + 1 :], start=i + 1):
            if j in grouped_indices:
                continue

            similarity = title_similarity(
                item.get("title", ""), other.get("title", "")
            )
            if similarity >= similarity_threshold:
                group.append(other)
                group_sources.append(other.get("source_name", "Unknown"))
                grouped_indices.add(j)

        # Sort group by priority (highest first) to pick the best one
        group.sort(key=lambda x: priority_order.get(x.get("priority"), 3))
        primary = group[0].copy()  # Don't mutate original

        # Add duplicate metadata if there are duplicates
        if len(group) > 1:
            primary["duplicate_count"] = len(group)
            # Unique sources for the duplicates
            unique_sources = list(dict.fromkeys(group_sources))
            primary["duplicate_sources"] = unique_sources
        else:
            primary["duplicate_count"] = 1
            primary["duplicate_sources"] = None

        result.append(primary)
        grouped_indices.add(i)

    return result


@app.post(
    "/api/sources", response_model=APIResponse, dependencies=[Depends(verify_api_key)]
)
async def add_source(
    request: SourceRequest, storage: Storage = Depends(get_storage)
) -> APIResponse:
    """Add a new content source.

    Normalizes the URL, validates it, and adds to the database.
    """
    try:
        # Normalize the URL to a real URL
        normalized_url = normalize_source_url(request.url, request.type)

        # Use provided name or auto-generate one
        name = request.name
        if not name:
            name = extract_name_from_url(normalized_url, request.type)

        # Validate the source
        validator = SourceValidator()
        is_valid, error_msg, metadata = validator.validate_source(
            normalized_url, request.type
        )

        if not is_valid:
            raise ValidationError(f"Source validation failed: {error_msg}")

        # Use display name from metadata if available (e.g., for Reddit subreddits)
        if metadata and "display_name" in metadata:
            name = metadata["display_name"]

        # Add to database
        source_id = storage.add_source(normalized_url, request.type, name)

        return APIResponse(
            success=True,
            message="Source added successfully",
            data={
                "id": source_id,
                "url": normalized_url,
                "type": request.type,
                "name": name,
            },
        )

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to add source: {str(e)}") from e


@app.get(
    "/api/sources",
    dependencies=[Depends(verify_api_key)],
)
async def get_sources(storage: Storage = Depends(get_storage)) -> dict:
    """Get all configured sources."""
    try:
        sources = storage.get_all_sources()

        # Convert to response models
        source_responses = []
        for source in sources:
            source_responses.append(
                SourceResponse(
                    id=source["id"],
                    url=source["url"],
                    type=source["type"],
                    name=source.get("name"),
                    active=source.get("active", True),
                    last_fetched=source.get("last_fetched_at"),
                    error_count=source.get("error_count", 0),
                    last_error=source.get("last_error"),
                )
            )

        return {
            "success": True,
            "message": f"Retrieved {len(source_responses)} sources",
            "data": {"sources": source_responses, "total": len(source_responses)},
        }

    except Exception as e:
        raise ServerError(f"Failed to get sources: {str(e)}") from e


@app.patch(
    "/api/sources/{source_id}",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_source(
    source_id: str,
    request: SourceRequest,
    storage: Storage = Depends(get_storage),
) -> APIResponse:
    """Update a content source (name and/or URL).

    Updates the source with new name and/or URL.
    """
    try:
        # Get the existing source first
        sources = storage.get_all_sources()
        source = next((s for s in sources if s["id"] == source_id), None)

        if not source:
            raise NotFoundError("Source", source_id)

        # Prepare update data - only update fields that are provided
        update_data = {}

        # Update URL if provided and different
        if request.url and request.url != source["url"]:
            # Normalize the new URL
            normalized_url = normalize_source_url(request.url, request.type)

            # Validate the new URL
            validator = SourceValidator()
            is_valid, error_msg, metadata = validator.validate_source(
                normalized_url, request.type
            )

            if not is_valid:
                raise ValidationError(f"Source validation failed: {error_msg}")

            update_data["url"] = normalized_url

        # Update name if provided
        if request.name is not None:  # Allow empty string to clear name
            update_data["name"] = request.name

        # Only update if there are changes
        if update_data:
            success = storage.update_source(source_id, update_data)

            if not success:
                raise ServerError("Failed to update source")

        return APIResponse(
            success=True,
            message="Source updated successfully",
            data={"id": source_id, **update_data},
        )

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to update source: {str(e)}") from e


@app.delete(
    "/api/sources/{source_id}",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def delete_source(
    source_id: str, storage: Storage = Depends(get_storage)
) -> APIResponse:
    """Delete a content source.

    This will cascade delete all content from this source.
    """
    try:
        success = storage.remove_source(source_id)

        if not success:
            raise NotFoundError("Source", source_id)

        return APIResponse(
            success=True, message="Source removed successfully", data={"id": source_id}
        )

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to remove source: {str(e)}") from e


@app.patch(
    "/api/entries/{content_id}",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_content(
    content_id: str,
    request: ContentUpdateRequest,
    storage: Storage = Depends(get_storage),
) -> APIResponse:
    """Update content properties (read status, favorited, interesting_override).

    This endpoint allows clients to update content metadata.
    At least one field must be provided in the request.
    """
    try:
        # Build kwargs for update_content_status
        # Only pass user_feedback if it was explicitly provided in the request
        update_kwargs = {
            "read": request.read,
            "favorited": request.favorited,
            "interesting_override": request.interesting_override,
        }

        # Check if user_feedback was explicitly set in the request JSON
        # We need to distinguish between "not provided" and "explicitly set to null"
        if hasattr(request, "user_feedback"):
            update_kwargs["user_feedback"] = request.user_feedback

        # Update content status
        success = storage.update_content_status(content_id, **update_kwargs)

        if not success:
            raise NotFoundError("Content", content_id)

        # Get updated content to return current state
        updated_content = storage.get_content_by_id(content_id)

        return APIResponse(
            success=True,
            message="Content updated successfully",
            data={
                "id": content_id,
                "read": updated_content["read"] if updated_content else None,
                "favorited": updated_content["favorited"] if updated_content else None,
                "interesting_override": updated_content["interesting_override"]
                if updated_content
                else None,
                "user_feedback": updated_content.get("user_feedback")
                if updated_content
                else None,
            },
        )

    except APIError:
        raise  # Re-raise our custom errors
    except ValueError as e:
        raise ValidationError(str(e)) from e
    except Exception as e:
        raise ServerError(f"Failed to update content: {str(e)}") from e


@app.patch(
    "/api/sources/{source_id}/pause",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def pause_source(
    source_id: str, storage: Storage = Depends(get_storage)
) -> APIResponse:
    """Pause a content source (set inactive).

    The source will remain in the database but won't be fetched.
    """
    try:
        success = storage.pause_source(source_id)

        if not success:
            raise NotFoundError("Source", source_id)

        return APIResponse(
            success=True,
            message="Source paused successfully",
            data={"id": source_id, "active": False},
        )

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to pause source: {str(e)}") from e


@app.patch(
    "/api/sources/{source_id}/resume",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def resume_source(
    source_id: str, storage: Storage = Depends(get_storage)
) -> APIResponse:
    """Resume a paused content source (set active).

    The source will be fetched again in the next cycle.
    Error count will be reset.
    """
    try:
        success = storage.resume_source(source_id)

        if not success:
            raise NotFoundError("Source", source_id)

        return APIResponse(
            success=True,
            message="Source resumed successfully",
            data={"id": source_id, "active": True},
        )

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to resume source: {str(e)}") from e


@app.get("/api/entries", dependencies=[Depends(verify_api_key)])
async def get_content(
    priority: str | None = Query(
        None,
        description="Filter by priority level(s). Single: 'high' or comma-separated: 'high,medium,low'",
    ),
    unread_only: bool = Query(False),
    include_archived: bool = Query(False),
    interesting_override: bool | None = Query(
        None, description="Filter by interesting_override flag"
    ),
    limit: int = Query(50, le=10000, ge=1),
    since: str | None = Query(None, description="ISO8601 timestamp to filter content"),
    since_hours: int | None = Query(
        None, ge=1, le=720, description="Hours to look back (convenience parameter)"
    ),
    sort_by: str | None = Query(
        None,
        description="Sort order: 'priority' (default), 'date', or 'unread'",
    ),
    source: str | None = Query(
        None, description="Filter by source name (case-insensitive substring match)"
    ),
    compact: bool = Query(
        False, description="Return compact format (excludes content and analysis)"
    ),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Get content items with optional filtering.

    Args:
        priority: Filter by priority level(s). Single value ('high', 'medium', 'low') or
                 comma-separated ('high,medium,low')
        unread_only: Only return unread items (default: False)
        include_archived: Include archived content (default: False)
        interesting_override: Filter by interesting_override flag (default: None)
        limit: Maximum number of items to return (1-10000, default: 50)
        since: ISO8601 timestamp to filter content (e.g., '2025-11-05T12:00:00Z')
        since_hours: Hours to look back (1-720). Convenience parameter - converted to timestamp.
                     If neither since nor since_hours provided, returns all content.
        sort_by: Sort order - 'priority' (default), 'date', or 'unread'
        source: Filter results to sources containing this substring (case-insensitive)
        compact: Return compact format for LLM consumption
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with filtered content items
    """
    # Parse and validate priority parameter (supports comma-separated values)
    priorities: list[str] = []
    if priority:
        priorities = [p.strip() for p in priority.split(",")]
        invalid = [p for p in priorities if p not in ["high", "medium", "low"]]
        if invalid:
            raise ValidationError(
                f"Invalid priority value(s): {', '.join(invalid)}. Must be one of: high, medium, low"
            )

    # Validate sort_by parameter
    valid_sort_options = ["priority", "date", "unread"]
    effective_sort = sort_by if sort_by in valid_sort_options else "priority"

    try:
        # Convert time parameters to datetime for storage layer
        since_dt: datetime | None = None
        if since_hours:
            since_dt = datetime.now(UTC) - timedelta(hours=since_hours)
        elif since:
            try:
                since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError as e:
                raise ValidationError(
                    f"Invalid ISO8601 timestamp: {since}. Expected format: 2025-11-05T12:00:00Z"
                ) from e

        content_items = []

        # Handle interesting_override filter first (takes precedence)
        if interesting_override is True:
            content_items = storage.get_flagged_items(limit)
        elif priorities:
            # Get content by specific priority/priorities
            if unread_only:
                # Call storage for each priority and combine results
                for p in priorities:
                    remaining = limit - len(content_items)
                    if remaining <= 0:
                        break
                    items = storage.get_content_by_priority(
                        p, remaining, include_archived, source_filter=source
                    )
                    content_items.extend(items)
            else:
                # Get content with time filter, then filter by priorities
                all_content = storage.get_content_since(
                    since=since_dt,
                    include_archived=include_archived,
                    source_filter=source,
                )
                content_items = [
                    item for item in all_content if item.get("priority") in priorities
                ]
        else:
            # Get content from all priorities
            if unread_only:
                # Get unread from all priorities, respecting limit
                high_items = storage.get_content_by_priority(
                    "high", limit, include_archived, source_filter=source
                )
                remaining_limit = limit - len(high_items)

                medium_items = []
                low_items = []
                if remaining_limit > 0:
                    medium_items = storage.get_content_by_priority(
                        "medium",
                        remaining_limit,
                        include_archived,
                        source_filter=source,
                    )
                    remaining_limit = remaining_limit - len(medium_items)

                if remaining_limit > 0:
                    low_items = storage.get_content_by_priority(
                        "low", remaining_limit, include_archived, source_filter=source
                    )

                content_items = high_items + medium_items + low_items
            else:
                # Get all content (or filtered by time if since/since_hours provided)
                all_content = storage.get_content_since(
                    since=since_dt,
                    include_archived=include_archived,
                    source_filter=source,
                )
                content_items = all_content

        # Apply sorting based on sort_by parameter
        # Helper to get sortable date (ISO strings sort correctly alphabetically)
        def get_date(item: dict) -> str:
            return item.get("published_at") or ""

        priority_order = {"high": 0, "medium": 1, "low": 2, None: 3}

        if effective_sort == "date":
            # Sort by published_at descending (newest first)
            content_items.sort(key=get_date, reverse=True)
        elif effective_sort == "unread":
            # Sort by read status (unread first), then by date descending
            # Use stable sort: first by date desc, then by read status
            content_items.sort(key=get_date, reverse=True)
            content_items.sort(key=lambda x: 1 if x.get("read_at") else 0)
        else:
            # Default: sort by priority ascending, then date descending
            # Use stable sort: first by date desc, then by priority
            content_items.sort(key=get_date, reverse=True)
            content_items.sort(key=lambda x: priority_order.get(x.get("priority"), 3))

        # Deduplicate by fuzzy title matching (80% similarity)
        # Groups similar items, keeps highest priority as primary
        content_items = deduplicate_content(content_items)

        # Apply limit AFTER deduplication to ensure duplicates are properly grouped
        content_items = content_items[:limit]

        # Filter to compact fields if requested
        if compact:
            compact_fields = {
                "id",
                "title",
                "url",
                "priority",
                "published_at",
                "source_name",
                "summary",
                "duplicate_count",
                "duplicate_sources",
            }
            content_items = [
                {k: v for k, v in item.items() if k in compact_fields}
                for item in content_items
            ]

        # Format response consistently with other endpoints
        return {
            "success": True,
            "message": f"Retrieved {len(content_items)} content items",
            "data": {
                "items": content_items,
                "total": len(content_items),
                "filters_applied": {
                    "priority": priority,
                    "unread_only": unread_only,
                    "include_archived": include_archived,
                    "interesting_override": interesting_override,
                    "limit": limit,
                    "since": since,
                    "since_hours": since_hours,
                    "sort_by": effective_sort,
                    "source": source,
                    "compact": compact,
                },
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to get content: {str(e)}") from e


@app.get("/api/search", dependencies=[Depends(verify_api_key)])
async def semantic_search(
    q: str = Query(..., min_length=1, description="Search query"),
    limit: int = Query(20, le=50, ge=1, description="Maximum results to return"),
    min_score: float = Query(
        0.0, ge=0.0, le=1.0, description="Minimum relevance score"
    ),
    source: str | None = Query(
        None, description="Filter by source name (case-insensitive substring match)"
    ),
    compact: bool = Query(
        False, description="Return compact format (excludes content and analysis)"
    ),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Semantic search across all content using embeddings.

    Uses similarity-first ranking: 80% semantic match + 10% priority + 10% authority
    (Search finds what you're looking for, with boost for authoritative sources)

    Args:
        q: Search query text
        limit: Maximum number of results (1-50, default: 20)
        min_score: Minimum relevance score filter (0.0-1.0, default: 0.0)
        source: Filter results to sources containing this substring (case-insensitive)
        compact: Return compact format for LLM consumption
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with ranked search results including relevance_score
    """
    try:
        # Initialize embedder and generate query embedding
        embedder = Embedder()
        query_embedding = embedder.generate_embedding(q)

        # Search content with weighted ranking
        results = storage.search_content(
            query_embedding=query_embedding,
            limit=limit,
            min_score=min_score,
            source_filter=source,
        )

        # Filter to compact fields if requested
        if compact:
            compact_fields = {
                "id",
                "title",
                "url",
                "priority",
                "relevance_score",
                "published_at",
                "source_name",
                "summary",
            }
            results = [
                {k: v for k, v in item.items() if k in compact_fields}
                for item in results
            ]

        return {
            "success": True,
            "message": f"Found {len(results)} results for '{q}'",
            "data": {
                "items": results,
                "total": len(results),
                "query": q,
                "filters_applied": {
                    "limit": limit,
                    "min_score": min_score,
                    "source": source,
                    "compact": compact,
                },
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to search content: {str(e)}") from e


@app.get("/api/entries/{content_id}", dependencies=[Depends(verify_api_key)])
async def get_entry_summary(
    content_id: str,
    include: str | None = Query(None),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Get a single content entry by ID.

    By default returns summary without content field for performance.
    Use ?include=content to get full entry data.

    Args:
        content_id: UUID of the content entry
        include: Optional fields to include ('content' for full entry)
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with entry metadata (excludes content field by default)
        or full entry when include=content

    Raises:
        NotFoundError: If entry with given ID doesn't exist
    """
    try:
        entry = storage.get_content_by_id(content_id)

        if not entry:
            raise NotFoundError("Entry", content_id)

        # Include content field if requested, otherwise filter it out
        if include == "content":
            # Return full entry
            entry_data = entry
        else:
            # Remove content field for lightweight response
            entry_data = {k: v for k, v in entry.items() if k != "content"}

        return {
            "success": True,
            "message": "Entry retrieved successfully",
            "data": entry_data,
        }

    except APIError:
        raise  # Re-raise our custom errors
    except Exception as e:
        raise ServerError(f"Failed to get entry: {str(e)}") from e


@app.get("/api/entries/{content_id}/raw", dependencies=[Depends(verify_api_key)])
async def get_entry_raw(
    content_id: str, storage: Storage = Depends(get_storage)
) -> PlainTextResponse:
    """Get raw content of a single entry as plain text.

    This endpoint returns only the content field as plain text,
    suitable for piping to external tools like fabric.

    Args:
        content_id: UUID of the content entry
        storage: Storage instance injected by FastAPI

    Returns:
        Plain text response with content field only

    Raises:
        NotFoundError: If entry with given ID doesn't exist (404 plain text)
    """
    try:
        entry = storage.get_content_by_id(content_id)

        if not entry:
            return PlainTextResponse("Not found", status_code=404)

        # Extract content field, handle missing gracefully
        content = entry.get("content", "")

        return PlainTextResponse(content or "")

    except Exception as e:
        # For plain text endpoint, return simple error messages
        return PlainTextResponse(f"Error: {str(e)}", status_code=500)


@app.get("/health")
async def health_check(storage: Storage = Depends(get_storage)) -> dict:
    """Health check endpoint that verifies database connectivity (no auth required)."""
    try:
        # Actually check database is accessible
        sources_count = len(storage.get_all_sources())
        return {
            "success": True,
            "message": "Service healthy",
            "data": {
                "service": "prismis-api",
                "database": "connected",
                "sources": sources_count,
            },
        }
    except Exception as e:
        # Let the exception handler format it consistently
        raise ServerError(f"Health check failed: {str(e)}") from e


@app.post("/api/prune", dependencies=[Depends(verify_api_key)])
async def prune_unprioritized(
    days: int | None = None,
    storage: Storage = Depends(get_storage),
) -> dict:
    """Prune (delete) unprioritized content items.

    Args:
        days: Optional age filter - only delete items older than this many days
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with count of deleted items
    """
    try:
        # First get the count for user information
        count = storage.count_unprioritized(days)

        if count == 0:
            return {
                "success": True,
                "message": "No unprioritized items to prune",
                "data": {
                    "deleted": 0,
                    "days_filter": days,
                },
            }

        # Perform the deletion
        deleted = storage.delete_unprioritized(days)

        # Build appropriate message
        if days:
            message = f"Pruned {deleted} unprioritized items older than {days} days"
        else:
            message = f"Pruned {deleted} unprioritized items"

        return {
            "success": True,
            "message": message,
            "data": {
                "deleted": deleted,
                "days_filter": days,
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to prune items: {str(e)}") from e


@app.get("/api/prune/count", dependencies=[Depends(verify_api_key)])
async def count_unprioritized(
    days: int | None = None,
    storage: Storage = Depends(get_storage),
) -> dict:
    """Count unprioritized content items that would be pruned.

    Args:
        days: Optional age filter - only count items older than this many days
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with count of items that would be deleted
    """
    try:
        count = storage.count_unprioritized(days)

        # Build appropriate message
        if days:
            message = f"Found {count} unprioritized items older than {days} days"
        else:
            message = f"Found {count} unprioritized items"

        return {
            "success": True,
            "message": message,
            "data": {
                "count": count,
                "days_filter": days,
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to count unprioritized items: {str(e)}") from e


@app.post("/api/audio/briefings", dependencies=[Depends(verify_api_key)])
async def generate_audio_briefing(
    storage: Storage = Depends(get_storage),
    config: Config = Depends(get_config),
) -> dict:
    """Generate audio briefing from HIGH priority content.

    Creates a Jarvis-style audio briefing using lspeak TTS engine.
    Generation is blocking and takes 10-30 seconds depending on content.

    Args:
        storage: Storage instance injected by FastAPI
        config: Config instance injected by FastAPI

    Returns:
        JSON response with file path and metadata

    Raises:
        ValidationError: If no HIGH priority content available
        ServerError: If lspeak not installed or generation fails
    """
    try:
        # Generate daily report (this gets HIGH priority items)
        generator = ReportGenerator(storage)
        report = generator.generate_daily_report(hours=24)

        # Check if we have HIGH priority content
        if not report.high_priority:
            raise ValidationError(
                "No high priority content available for briefing. "
                "Add content sources or adjust prioritization context."
            )

        # Generate conversational script using LLM
        script_gen = AudioScriptGenerator(config)
        script = script_gen.generate_script(report)

        # Set up output path
        import os
        from datetime import datetime

        # Use ~/.local/share/prismis/audio for output
        audio_dir = (
            Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
            / "prismis"
            / "audio"
        )
        audio_dir.mkdir(parents=True, exist_ok=True)

        # Date-based filename
        date_str = datetime.now().strftime("%Y-%m-%d")
        filename = f"briefing-{date_str}.mp3"
        output_path = audio_dir / filename

        # Generate audio using lspeak
        tts_engine = LspeakTTSEngine(
            provider=config.audio_provider, voice=config.audio_voice
        )
        tts_engine.generate(script, output_path)

        return {
            "success": True,
            "message": f"Audio briefing generated: {filename}",
            "data": {
                "file_path": str(output_path),
                "filename": filename,
                "duration_estimate": "2-5 minutes",
                "generated_at": datetime.now().isoformat(),
                "provider": config.audio_provider,
                "high_priority_count": len(report.high_priority),
            },
        }

    except ValidationError:
        raise  # Re-raise validation errors
    except RuntimeError as e:
        # Handle lspeak not found or generation failures
        error_msg = str(e)
        if "lspeak not found" in error_msg:
            raise ServerError(
                "lspeak is not installed. Install with: "
                "uv tool install git+https://github.com/nickpending/lspeak.git"
            ) from e
        else:
            raise ServerError(f"Audio generation failed: {error_msg}") from e
    except Exception as e:
        raise ServerError(f"Failed to generate audio briefing: {str(e)}") from e


@app.get("/api/archive/status", dependencies=[Depends(verify_api_key)])
async def archive_status(
    storage: Storage = Depends(get_storage),
    config: Config = Depends(get_config),
) -> dict:
    """Get archival status and statistics.

    Args:
        storage: Storage instance injected by FastAPI
        config: Config instance injected by FastAPI

    Returns:
        JSON response with archival configuration and item counts
    """
    try:
        total = storage.count_active() + storage.count_archived()
        archived = storage.count_archived()
        active = storage.count_active()

        return {
            "success": True,
            "message": "Archival status retrieved",
            "data": {
                "enabled": config.archival_enabled,
                "total_items": total,
                "archived_items": archived,
                "active_items": active,
                "windows": {
                    "high_read": config.archival_high_read,
                    "medium_unread": config.archival_medium_unread,
                    "medium_read": config.archival_medium_read,
                    "low_unread": config.archival_low_unread,
                    "low_read": config.archival_low_read,
                },
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to get archival status: {str(e)}") from e


@app.post("/api/context", dependencies=[Depends(verify_api_key)])
async def analyze_context(
    storage: Storage = Depends(get_storage),
    config: Config = Depends(get_config),
) -> dict:
    """Analyze flagged items and suggest topics for context.md.

    Uses LLM to analyze flagged content items and suggest new topics
    that should be added to the user's context.md file.

    Args:
        storage: Storage instance injected by FastAPI
        config: Config instance injected by FastAPI

    Returns:
        JSON response with suggested topics array

    Raises:
        ValidationError: If no flagged items available
        ServerError: If LLM analysis fails
    """
    try:
        # Get flagged items (limit to 50 for token efficiency)
        flagged_items = storage.get_flagged_items(limit=50)

        if not flagged_items:
            raise ValidationError(
                "No items flagged for context analysis. "
                "Use 'i' key in TUI to flag interesting items first."
            )

        # Load current context.md content
        context_text = config.context

        # Initialize context analyzer with LLM config
        analyzer = ContextAnalyzer(
            {
                "model": config.llm_model,
                "api_key": config.llm_api_key,
                "api_base": config.llm_api_base,
                "provider": config.llm_provider,
            }
        )

        # Analyze and get suggestions
        result = analyzer.analyze_flagged_items(flagged_items, context_text)

        return {
            "success": True,
            "message": f"{len(result['suggested_topics'])} topics suggested",
            "data": result,
        }

    except ValidationError:
        raise  # Re-raise validation errors
    except Exception as e:
        obs_log("api.error", endpoint="/api/context", error=str(e))
        raise ServerError(f"Failed to analyze context: {str(e)}") from e


@app.get("/api/statistics", dependencies=[Depends(verify_api_key)])
async def get_statistics(
    storage: Storage = Depends(get_storage),
) -> dict:
    """Get system-wide statistics.

    Returns comprehensive statistics about content, sources, and system state.

    Args:
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with statistics grouped by category
    """
    try:
        # Get all statistics in a single optimized query
        stats = storage.get_statistics()

        return {
            "success": True,
            "message": "Statistics retrieved successfully",
            "data": stats,
        }

    except Exception as e:
        raise ServerError(f"Failed to get statistics: {str(e)}") from e


@app.get("/api/feedback/statistics", dependencies=[Depends(verify_api_key)])
async def get_feedback_statistics(
    since_days: int | None = Query(None, description="Limit to feedback within N days"),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Get user feedback statistics aggregated by source and topic.

    Returns feedback patterns for preference learning:
    - Overall vote totals
    - Per-source upvote/downvote counts and ratios
    - Topics extracted from upvoted/downvoted content
    - Pre-formatted summary for LLM prompt injection

    Args:
        since_days: Optional limit to feedback within N days (None = all time)
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with aggregated feedback statistics
    """
    try:
        stats = storage.get_feedback_statistics(since_days=since_days)

        return {
            "success": True,
            "message": "Feedback statistics retrieved successfully",
            "data": stats,
        }

    except Exception as e:
        raise ServerError(f"Failed to get feedback statistics: {str(e)}") from e


# Mount audio files directory


audio_dir = (
    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    / "prismis"
    / "audio"
)
if audio_dir.exists():
    app.mount("/audio", StaticFiles(directory=str(audio_dir)), name="audio")

# SPA catch-all routes (defined after all API routes)
if static_dir.exists():
    index_path = static_dir / "index.html"

    # Serve index.html for all GET requests to non-API, non-audio paths
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """Serve index.html for SPA client-side routing."""
        # Return 404 for API routes (check with and without leading slash)
        normalized_path = full_path.lstrip("/")
        if normalized_path.startswith("api/") or normalized_path.startswith("audio/"):
            raise HTTPException(status_code=404, detail="Not Found")
        # Serve index.html for SPA routes
        if index_path.exists():
            return FileResponse(index_path)
        raise HTTPException(status_code=404, detail="Not Found")

    # Return 404 for non-GET methods to non-API paths (instead of 405)
    @app.api_route(
        "/{full_path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    )
    async def catch_all_404(full_path: str) -> None:
        """Return 404 for non-GET requests to non-existent routes."""
        raise HTTPException(status_code=404, detail="Not Found")
