"""REST API server for Prismis daemon."""

import re
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, Depends, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

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
from .audio import AudioScriptGenerator, LspeakTTSEngine
from .config import Config


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


@app.get("/api/content", dependencies=[Depends(verify_api_key)])
async def get_content(
    priority: Optional[str] = Query(None, regex="^(high|medium|low)$"),
    unread_only: bool = Query(False),
    limit: int = Query(50, le=100, ge=1),
    since_hours: int = Query(24, ge=1, le=720),
    storage: Storage = Depends(get_storage),
) -> dict:
    """Get content items with optional filtering.

    Args:
        priority: Filter by priority level ('high', 'medium', 'low')
        unread_only: Only return unread items (default: False)
        limit: Maximum number of items to return (1-100, default: 50)
        since_hours: Hours of content to fetch (1-720, default: 24)
        storage: Storage instance injected by FastAPI

    Returns:
        JSON response with filtered content items
    """
    try:
        content_items = []

        if priority:
            # Get content by specific priority
            if unread_only:
                # Use existing method that only returns unread items
                content_items = storage.get_content_by_priority(priority, limit)
            else:
                # Need to modify query to include read items - use get_content_since with filter
                all_recent = storage.get_content_since(hours=since_hours)
                content_items = [
                    item for item in all_recent if item.get("priority") == priority
                ][:limit]
        else:
            # Get content from all priorities
            if unread_only:
                # Get unread from all priorities, respecting limit
                high_items = storage.get_content_by_priority("high", limit)
                remaining_limit = limit - len(high_items)

                medium_items = []
                low_items = []
                if remaining_limit > 0:
                    medium_items = storage.get_content_by_priority(
                        "medium", remaining_limit
                    )
                    remaining_limit = remaining_limit - len(medium_items)

                if remaining_limit > 0:
                    low_items = storage.get_content_by_priority("low", remaining_limit)

                content_items = high_items + medium_items + low_items
            else:
                # Get all content from recent period
                all_recent = storage.get_content_since(hours=since_hours)
                content_items = all_recent[:limit]

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
                    "limit": limit,
                    "since_hours": since_hours,
                },
            },
        }

    except Exception as e:
        raise ServerError(f"Failed to get content: {str(e)}")


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
            )
        else:
            raise ServerError(f"Audio generation failed: {error_msg}")
    except Exception as e:
        raise ServerError(f"Failed to generate audio briefing: {str(e)}")


# Mount audio files directory
import os

audio_dir = (
    Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    / "prismis"
    / "audio"
)
if audio_dir.exists():
    app.mount("/audio", StaticFiles(directory=str(audio_dir)), name="audio")

# Mount static files LAST to avoid intercepting API routes
if static_dir.exists():
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
