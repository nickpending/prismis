"""REST API server for Prismis daemon."""

import re
from typing import Optional
from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api_models import (
    SourceRequest,
    APIResponse,
    SourceResponse,
    ContentUpdateRequest,
)
from .api_errors import (
    APIError,
    ValidationError,
    NotFoundError,
    ServerError,
)
from .auth import verify_api_key
from .storage import Storage
from .validator import SourceValidator
from .reports import ReportGenerator


app = FastAPI(
    title="Prismis API",
    description="REST API for managing content sources",
    version="1.0.0",
)


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError) -> JSONResponse:
    """Handle our custom APIError exceptions with consistent format.

    Formats all errors as: {"success": false, "message": "...", "data": null}
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={"success": False, "message": exc.message, "data": None},
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


# Configure CORS for local access only
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:*", "http://127.0.0.1:*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        raise ServerError(f"Failed to add source: {str(e)}")


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
                    last_fetched=source.get("last_fetched"),
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
        raise ServerError(f"Failed to get sources: {str(e)}")


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
        raise ServerError(f"Failed to update source: {str(e)}")


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
        raise ServerError(f"Failed to remove source: {str(e)}")


@app.patch(
    "/api/content/{content_id}",
    response_model=APIResponse,
    dependencies=[Depends(verify_api_key)],
)
async def update_content(
    content_id: str,
    request: ContentUpdateRequest,
    storage: Storage = Depends(get_storage),
) -> APIResponse:
    """Update content properties (read status, favorited).

    This endpoint allows clients to update content metadata.
    At least one field must be provided in the request.
    """
    try:
        # Update content status
        success = storage.update_content_status(
            content_id, read=request.read, favorited=request.favorited
        )

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
            },
        )

    except APIError:
        raise  # Re-raise our custom errors
    except ValueError as e:
        raise ValidationError(str(e))
    except Exception as e:
        raise ServerError(f"Failed to update content: {str(e)}")


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
        raise ServerError(f"Failed to pause source: {str(e)}")


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
        raise ServerError(f"Failed to resume source: {str(e)}")


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
        raise ServerError(f"Health check failed: {str(e)}")


@app.get("/api/reports", dependencies=[Depends(verify_api_key)])
async def get_reports(
    period: str = "24h",
    storage: Storage = Depends(get_storage),
) -> dict:
    """Generate a content report for the specified period.

    Args:
        period: Time period to report on. Formats:
                - "24h" (default) - Last 24 hours
                - "7d" - Last 7 days
                - "30d" - Last 30 days
                - Or any number followed by 'h' (hours) or 'd' (days)
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with markdown report and metadata
    """
    try:
        # Parse the period parameter
        import re

        match = re.match(r"(\d+)([hd])", period.lower())
        if not match:
            raise ValidationError(
                f"Invalid period format: {period}. Use format like '24h' or '7d'"
            )

        amount = int(match.group(1))
        unit = match.group(2)

        # Convert to hours
        if unit == "d":
            hours = amount * 24
        else:
            hours = amount

        # Reasonable limits
        if hours > 720:  # 30 days max
            raise ValidationError("Period cannot exceed 30 days (720 hours)")
        if hours < 1:
            raise ValidationError("Period must be at least 1 hour")

        # Generate the report
        generator = ReportGenerator(storage)
        report = generator.generate_daily_report(hours=hours)
        markdown = generator.format_as_markdown(report)

        return {
            "success": True,
            "message": f"Report generated for last {period}",
            "data": {
                "markdown": markdown,
                "period": period,
                "period_hours": hours,
                "generated_at": report.generated_at.isoformat(),
                "statistics": {
                    "total_items": report.total_items,
                    "high_priority": len(report.high_priority),
                    "medium_priority": len(report.medium_priority),
                    "low_priority": len(report.low_priority),
                    "top_sources": [
                        {"name": name, "count": count}
                        for name, count in report.top_sources
                    ],
                    "key_themes": report.key_themes,
                },
            },
        }

    except ValidationError:
        raise  # Re-raise validation errors
    except Exception as e:
        raise ServerError(f"Failed to generate report: {str(e)}")


@app.post("/api/prune", dependencies=[Depends(verify_api_key)])
async def prune_unprioritized(
    days: Optional[int] = None,
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
        raise ServerError(f"Failed to prune items: {str(e)}")


@app.get("/api/prune/count", dependencies=[Depends(verify_api_key)])
async def count_unprioritized(
    days: Optional[int] = None,
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
        raise ServerError(f"Failed to count unprioritized items: {str(e)}")
