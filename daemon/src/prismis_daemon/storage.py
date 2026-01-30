"""Repository pattern storage layer for Prismis daemon."""

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from operator import itemgetter
from pathlib import Path
from typing import Any

try:
    from .database import get_db_connection
    from .models import ContentItem
    from .observability import log as obs_log
except ImportError:
    # Handle case where we're imported from outside the package
    from database import get_db_connection
    from models import ContentItem

    try:
        from observability import log as obs_log
    except ImportError:
        # Graceful degradation if observability not available
        def obs_log(*args, **kwargs) -> None:
            pass


class Storage:
    """Repository for all database operations.

    Implements the repository pattern - all SQL stays in this class.
    Uses connection reuse pattern for efficiency.
    """

    # Prune protection WHERE clause - items excluded from deletion
    # Used by both count_unprioritized() and delete_unprioritized()
    PRUNE_EXCLUSION_WHERE = """
        (priority IS NULL OR priority = '')
        AND favorited = 0
        AND (interesting_override = 0 OR interesting_override IS NULL)
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize storage with database connection.

        Args:
            db_path: Optional custom database path for testing.
                     Defaults to $XDG_DATA_HOME/prismis/prismis.db
                     (or ~/.local/share/prismis/prismis.db)
        """
        self.db_path = db_path
        self._conn = None  # Lazy connection initialization
        # Test that we can create a connection
        test_conn = get_db_connection(self.db_path)
        test_conn.close()

    @property
    def conn(self) -> sqlite3.Connection:
        """Get or create database connection with lazy initialization.

        Returns existing connection if available, creates new one if not.
        Connection is reused across multiple operations for efficiency.
        """
        if self._conn is None:
            self._conn = get_db_connection(self.db_path)
        return self._conn

    def close(self) -> None:
        """Close the database connection if open.

        Should be called when Storage instance is done with all operations.
        """
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        """Context manager entry - returns self for use in with statements."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures connection is closed."""
        self.close()

    def add_source(self, url: str, source_type: str, name: str | None = None) -> str:
        """Add a new content source to the database.

        Args:
            url: The source URL (RSS feed, Reddit sub, YouTube channel, file URL)
            source_type: Type of source ('rss', 'reddit', 'youtube', 'file')
            name: Optional human-readable name for the source

        Returns:
            The UUID of the inserted source

        Raises:
            ValueError: If source_type is invalid
            sqlite3.Error: If database operation fails
        """
        if source_type not in ("rss", "reddit", "youtube", "file"):
            raise ValueError(f"Invalid source type: {source_type}")

        # Use reusable connection for better performance
        try:
            # Check if source already exists
            cursor = self.conn.execute("SELECT id FROM sources WHERE url = ?", (url,))
            existing = cursor.fetchone()

            if existing:
                # Source already exists, return its UUID
                return existing[0]

            # Generate new UUID and insert
            source_id = str(uuid.uuid4())
            self.conn.execute(
                """
                INSERT INTO sources (id, url, type, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (source_id, url, source_type, name),
            )

            self.conn.commit()
            return source_id

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to add source: {e}") from e

    def get_active_sources(self) -> list[dict[str, Any]]:
        """Get all active content sources.

        Returns:
            List of source dictionaries with all fields
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT id, url, type, name, active, error_count, 
                       last_error, last_fetched_at, created_at, updated_at
                FROM sources
                WHERE active = 1
                ORDER BY id
                """
            )

            sources = []
            for row in cursor.fetchall():
                sources.append(
                    {
                        "id": row["id"],
                        "url": row["url"],
                        "type": row["type"],
                        "name": row["name"],
                        "active": bool(row["active"]),
                        "error_count": row["error_count"],
                        "last_error": row["last_error"],
                        "last_fetched_at": row["last_fetched_at"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )

            return sources

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get active sources: {e}") from e

    def add_content(self, item: ContentItem | dict[str, Any]) -> str | None:
        """Add content item to database with deduplication.

        Uses external_id for deduplication - if content with same
        external_id exists, it won't be inserted again.

        Args:
            item: ContentItem to store, or dict with content data

        Returns:
            The UUID of the inserted content, or None if duplicate

        Raises:
            sqlite3.Error: If database operation fails
        """
        start_time = time.time()

        # Convert dict to ContentItem if needed
        if isinstance(item, dict):
            # If no source_id provided, use the first available source
            source_id = item.get("source_id")
            if not source_id:
                sources = self.get_active_sources()
                if sources:
                    source_id = sources[0]["id"]
                else:
                    raise ValueError(
                        "No source_id provided and no active sources available"
                    )

            # Create ContentItem from dict with required fields
            content_item = ContentItem(
                id=str(uuid.uuid4()),
                external_id=item.get("external_id", str(uuid.uuid4())),
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("content", ""),
                source_id=source_id,
            )
            # Set optional fields if provided
            if "summary" in item:
                content_item.summary = item["summary"]
            if "analysis" in item:
                content_item.analysis = item["analysis"]
            if "priority" in item:
                content_item.priority = item["priority"]
            if "published_at" in item:
                content_item.published_at = item["published_at"]
            if "fetched_at" in item:
                content_item.fetched_at = item["fetched_at"]
            if "read" in item:
                content_item.read = item["read"]
            if "favorited" in item:
                content_item.favorited = item["favorited"]
            if "notes" in item:
                content_item.notes = item["notes"]
            item = content_item

        conn = get_db_connection(self.db_path)
        try:
            # Check if content already exists (deduplication)
            cursor = conn.execute(
                "SELECT id FROM content WHERE external_id = ?", (item.external_id,)
            )
            existing = cursor.fetchone()

            if existing:
                # Duplicate - external_id already exists
                duration_ms = int((time.time() - start_time) * 1000)
                obs_log(
                    "db.insert",
                    table="content",
                    operation="add_content",
                    row_count=0,
                    duration_ms=duration_ms,
                    status="duplicate",
                )
                return None

            # Serialize analysis dict to JSON if present
            analysis_json = None
            if item.analysis:
                analysis_json = json.dumps(item.analysis)

            conn.execute(
                """
                INSERT INTO content (
                    id, source_id, external_id, title, url, content,
                    summary, analysis, priority, published_at,
                    fetched_at, read, favorited, notes,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    item.id,  # Use the UUID from the item
                    item.source_id,
                    item.external_id,
                    item.title,
                    item.url,
                    item.content,
                    item.summary,
                    analysis_json,
                    item.priority,
                    item.published_at,
                    item.fetched_at or datetime.now(),
                    item.read,
                    item.favorited,
                    item.notes,
                ),
            )

            conn.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.insert",
                table="content",
                operation="add_content",
                row_count=1,
                duration_ms=duration_ms,
                status="success",
            )
            return item.id

        except sqlite3.Error as e:
            conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.insert",
                table="content",
                operation="add_content",
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise sqlite3.Error(f"Failed to add content: {e}") from e

    def create_or_update_content(
        self, item: ContentItem | dict[str, Any]
    ) -> tuple[str, bool]:
        """Create new or update existing content with deduplication tracking.

        This is the enhanced version of add_content that returns tracking info
        for the deduplication system. For existing items, only metadata fields
        are updated (summary, analysis, priority).

        Args:
            item: ContentItem to store, or dict with content data

        Returns:
            Tuple of (content_id, is_new) where:
            - content_id: UUID of the content (existing or new)
            - is_new: True if content was created, False if updated

        Raises:
            sqlite3.Error: If database operation fails
        """
        # Convert dict to ContentItem if needed (same logic as add_content)
        if isinstance(item, dict):
            # If no source_id provided, use the first available source
            source_id = item.get("source_id")
            if not source_id:
                sources = self.get_active_sources()
                if sources:
                    source_id = sources[0]["id"]
                else:
                    raise ValueError(
                        "No source_id provided and no active sources available"
                    )

            # Create ContentItem from dict with required fields
            content_item = ContentItem(
                id=str(uuid.uuid4()),
                external_id=item.get("external_id", str(uuid.uuid4())),
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("content", ""),
                source_id=source_id,
            )
            # Set optional fields if provided
            if "summary" in item:
                content_item.summary = item["summary"]
            if "analysis" in item:
                content_item.analysis = item["analysis"]
            if "priority" in item:
                content_item.priority = item["priority"]
            if "published_at" in item:
                content_item.published_at = item["published_at"]
            if "fetched_at" in item:
                content_item.fetched_at = item["fetched_at"]
            if "read" in item:
                content_item.read = item["read"]
            if "favorited" in item:
                content_item.favorited = item["favorited"]
            if "notes" in item:
                content_item.notes = item["notes"]
            item = content_item

        try:
            # Check if content already exists using helper method
            existing = self._get_by_external_id(item.external_id)

            if existing:
                # Update existing content (metadata only)
                analysis_json = None
                if item.analysis:
                    analysis_json = json.dumps(item.analysis)

                self.conn.execute(
                    """
                    UPDATE content 
                    SET content = ?, summary = ?, analysis = ?, priority = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE external_id = ?
                    """,
                    (
                        item.content,
                        item.summary,
                        analysis_json,
                        item.priority,
                        item.external_id,
                    ),
                )
                self.conn.commit()
                return existing["id"], False

            else:
                # Create new content (same logic as add_content)
                analysis_json = None
                if item.analysis:
                    analysis_json = json.dumps(item.analysis)

                self.conn.execute(
                    """
                    INSERT INTO content (
                        id, source_id, external_id, title, url, content,
                        summary, analysis, priority, published_at,
                        fetched_at, read, favorited, notes,
                        created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                    """,
                    (
                        item.id,
                        item.source_id,
                        item.external_id,
                        item.title,
                        item.url,
                        item.content,
                        item.summary,
                        analysis_json,
                        item.priority,
                        item.published_at,
                        item.fetched_at or datetime.now(),
                        item.read,
                        item.favorited,
                        item.notes,
                    ),
                )
                self.conn.commit()
                return item.id, True

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to create or update content: {e}") from e

    def get_existing_external_ids(self, source_id: str) -> set[str]:
        """Get all external_ids for a source to enable bulk deduplication filtering.

        This is used by the orchestrator to pre-filter items before processing,
        providing efficient O(1) lookup for duplicate detection.

        Args:
            source_id: UUID of the source to get external_ids for

        Returns:
            Set of external_id strings for the source

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                "SELECT external_id FROM content WHERE source_id = ?", (source_id,)
            )
            # Use set comprehension for O(1) lookup performance
            return {row[0] for row in cursor.fetchall()}

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get existing external_ids: {e}") from e

    def _get_by_external_id(self, external_id: str) -> dict[str, Any] | None:
        """Find content by external_id (private helper method).

        This is used internally by create_or_update_content() for single
        item lookup. Returns the full content record if found.

        Args:
            external_id: The external_id to search for

        Returns:
            Dict with content fields if found, None otherwise

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT id, source_id, external_id, title, url, content,
                       summary, analysis, priority, published_at, fetched_at,
                       read, favorited, notes, created_at, updated_at
                FROM content 
                WHERE external_id = ?
                """,
                (external_id,),
            )
            row = cursor.fetchone()

            if row:
                # Convert sqlite3.Row to dict
                return {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "content": row["content"],
                    "summary": row["summary"],
                    "analysis": json.loads(row["analysis"])
                    if row["analysis"]
                    else None,
                    "priority": row["priority"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "read": bool(row["read"]),
                    "favorited": bool(row["favorited"]),
                    "notes": row["notes"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by external_id: {e}") from e

    def get_content_by_priority(
        self,
        priority: str,
        limit: int = 50,
        include_archived: bool = False,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get unread content by priority level.

        Args:
            priority: Priority level ('high', 'medium', 'low')
            limit: Maximum number of items to return
            include_archived: Include archived content if True
            source_filter: Filter by source name (case-insensitive substring match)

        Returns:
            List of content dictionaries
        """
        try:
            # Build query with optional archived filter
            query = """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                JOIN sources s ON c.source_id = s.id
                WHERE c.priority = ? AND c.read = 0
            """
            params: list[Any] = [priority]

            # Add archived filter unless explicitly including archived
            if not include_archived:
                query += " AND c.archived_at IS NULL"

            # Add source filter if provided
            if source_filter:
                query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                params.append(source_filter)

            query += " ORDER BY c.published_at DESC LIMIT ?"
            params.append(limit)

            cursor = self.conn.execute(query, tuple(params))

            content = []
            for row in cursor.fetchall():
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    analysis = json.loads(row["analysis"])

                content.append(
                    {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "source_name": row["source_name"],
                        "source_type": row["source_type"],
                        "external_id": row["external_id"],
                        "title": row["title"],
                        "url": row["url"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "analysis": analysis,
                        "priority": row["priority"],
                        "published_at": row["published_at"],
                        "fetched_at": row["fetched_at"],
                        "read": bool(row["read"]),
                        "favorited": bool(row["favorited"]),
                        "interesting_override": bool(row["interesting_override"]),
                        "user_feedback": row["user_feedback"],
                        "notes": row["notes"],
                    }
                )

            return content

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by priority: {e}") from e

    def get_content_since(
        self,
        since: datetime | None = None,
        include_archived: bool = False,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get content since a specific timestamp, or all content if since is None.

        Args:
            since: Timestamp to filter content (returns content published after this time).
                   If None, returns all content regardless of time.
            include_archived: Include archived content if True
            source_filter: Filter by source name (case-insensitive substring match)

        Returns:
            List of content dictionaries with source information
        """
        try:
            # Build query with optional time filter
            query = """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                JOIN sources s ON c.source_id = s.id
                WHERE 1=1
            """

            params: list[Any] = []
            if since is not None:
                query += " AND c.fetched_at > ?"
                params.append(since.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"))

            # Add archived filter unless explicitly including archived
            if not include_archived:
                query += " AND c.archived_at IS NULL"

            # Add source filter if provided
            if source_filter:
                query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                params.append(source_filter)

            query += " ORDER BY c.priority ASC, c.published_at DESC"

            cursor = self.conn.execute(query, tuple(params))

            content = []
            for row in cursor.fetchall():
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    analysis = json.loads(row["analysis"])

                content.append(
                    {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "source_name": row["source_name"],
                        "source_type": row["source_type"],
                        "external_id": row["external_id"],
                        "title": row["title"],
                        "url": row["url"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "analysis": analysis,
                        "priority": row["priority"],
                        "published_at": row["published_at"],
                        "fetched_at": row["fetched_at"],
                        "read": bool(row["read"]),
                        "favorited": bool(row["favorited"]),
                        "interesting_override": bool(row["interesting_override"]),
                        "user_feedback": row["user_feedback"],
                        "notes": row["notes"],
                    }
                )

            return content

        except sqlite3.Error as e:
            since_str = since.isoformat() if since else "beginning"
            raise sqlite3.Error(f"Failed to get content since {since_str}: {e}") from e

    def mark_content_read(self, content_id: str) -> bool:
        """Mark a content item as read.

        Args:
            content_id: UUID of the content to mark as read

        Returns:
            True if content was marked read, False if not found
        """
        try:
            cursor = self.conn.execute(
                """
                UPDATE content 
                SET read = 1, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (content_id,),
            )
            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to mark content as read: {e}") from e

    def update_source_fetch_status(
        self, source_id: str, success: bool, error_message: str | None = None
    ) -> None:
        """Update source after fetch attempt.

        Args:
            source_id: UUID of the source
            success: Whether fetch was successful
            error_message: Error message if fetch failed
        """
        start_time = time.time()
        try:
            if success:
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET last_fetched_at = CURRENT_TIMESTAMP,
                        error_count = 0,
                        last_error = NULL,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (source_id,),
                )
            else:
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET error_count = error_count + 1,
                        last_error = ?,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (error_message, source_id),
                )

                # Deactivate source after 5 consecutive errors
                self.conn.execute(
                    """
                    UPDATE sources
                    SET active = 0
                    WHERE id = ? AND error_count >= 5
                    """,
                    (source_id,),
                )

            self.conn.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.update",
                table="sources",
                operation="update_source_fetch_status",
                row_count=cursor.rowcount,
                duration_ms=duration_ms,
                status="success" if success else "error_tracked",
            )

        except sqlite3.Error as e:
            self.conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.update",
                table="sources",
                operation="update_source_fetch_status",
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise sqlite3.Error(f"Failed to update source status: {e}") from e

    def update_source(self, source_id: str, update_data: dict) -> bool:
        """Update source properties (name and/or URL).

        Args:
            source_id: UUID of the source to update
            update_data: Dict with fields to update (name, url)

        Returns:
            True if update was successful, False otherwise
        """
        try:
            # Build UPDATE query with safe parameterized approach
            if "name" in update_data and "url" in update_data:
                # Update both name and URL
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (update_data["name"], update_data["url"], source_id),
                )
            elif "name" in update_data:
                # Update only name
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (update_data["name"], source_id),
                )
            elif "url" in update_data:
                # Update only URL
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET url = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (update_data["url"], source_id),
                )
            else:
                # No fields to update
                return False

            self.conn.commit()

            # Return True if a row was updated
            return cursor.rowcount > 0

        except sqlite3.Error:
            self.conn.rollback()
            # Failed to update source
            return False

    def get_all_sources(self) -> list[dict[str, Any]]:
        """Get all content sources (active and inactive).

        Returns:
            List of source dictionaries with all fields
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT id, url, type, name, active, error_count, 
                       last_error, last_fetched_at, created_at, updated_at
                FROM sources
                ORDER BY created_at DESC
                """
            )

            sources = []
            for row in cursor.fetchall():
                sources.append(
                    {
                        "id": row["id"],
                        "url": row["url"],
                        "type": row["type"],
                        "name": row["name"],
                        "active": bool(row["active"]),
                        "error_count": row["error_count"],
                        "last_error": row["last_error"],
                        "last_fetched_at": row["last_fetched_at"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )

            return sources

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get all sources: {e}") from e

    def pause_source(self, source_id: str) -> bool:
        """Pause a content source (set inactive).

        Args:
            source_id: UUID of the source to pause

        Returns:
            True if source was paused, False if not found

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """UPDATE sources 
                   SET active = 0, updated_at = CURRENT_TIMESTAMP 
                   WHERE id = ?""",
                (source_id,),
            )
            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to pause source: {e}") from e

    def resume_source(self, source_id: str) -> bool:
        """Resume a paused content source (set active and reset errors).

        Args:
            source_id: UUID of the source to resume

        Returns:
            True if source was resumed, False if not found

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """UPDATE sources 
                   SET active = 1, error_count = 0, last_error = NULL, 
                       updated_at = CURRENT_TIMESTAMP 
                   WHERE id = ?""",
                (source_id,),
            )
            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to resume source: {e}") from e

    def remove_source(self, source_id: str) -> bool:
        """Remove a content source from the database.

        This will preserve favorited content by setting their source_id to NULL,
        while deleting all non-favorited content from the source.

        Args:
            source_id: UUID of the source to remove

        Returns:
            True if source was removed, False if not found

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # First, preserve favorited content by setting source_id to NULL
            self.conn.execute(
                "UPDATE content SET source_id = NULL WHERE source_id = ? AND favorited = 1",
                (source_id,),
            )

            # Then delete all non-favorited content from this source
            self.conn.execute(
                "DELETE FROM content WHERE source_id = ? AND favorited = 0",
                (source_id,),
            )

            # Clean up orphaned vectors (virtual tables don't support CASCADE)
            self.conn.execute(
                "DELETE FROM vec_content WHERE content_id NOT IN (SELECT id FROM content)"
            )

            # Finally, delete the source itself
            cursor = self.conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to remove source: {e}") from e

    def update_content_status(
        self,
        content_id: str,
        read: bool | None = None,
        favorited: bool | None = None,
        interesting_override: bool | None = None,
        user_feedback: str | None = "__NOT_PROVIDED__",
    ) -> bool:
        """Update read, favorited, interesting_override, and/or user_feedback status.

        Args:
            content_id: UUID of the content to update
            read: Set read status if provided
            favorited: Set favorited status if provided
            interesting_override: Set interesting_override flag if provided
            user_feedback: Set user feedback ('up', 'down', or None to clear).
                          Use special value "__NOT_PROVIDED__" to indicate param was not passed.

        Returns:
            True if content was updated, False if not found

        Raises:
            ValueError: If no update parameters provided or invalid user_feedback value
            sqlite3.Error: If database operation fails
        """
        # Check if user_feedback was explicitly provided (not the default sentinel)
        user_feedback_provided = user_feedback != "__NOT_PROVIDED__"

        if (
            read is None
            and favorited is None
            and interesting_override is None
            and not user_feedback_provided
        ):
            raise ValueError(
                "At least one of read, favorited, interesting_override, or user_feedback must be provided"
            )

        # Validate user_feedback if provided
        if user_feedback_provided and user_feedback not in ("up", "down", None):
            raise ValueError(
                f"Invalid user_feedback value: {user_feedback}. Must be 'up', 'down', or None"
            )

        start_time = time.time()
        try:
            # Build SET clause with hardcoded field names (safe - not user input)
            updates = []
            params = []

            if read is not None:
                updates.append("read = ?")
                params.append(1 if read else 0)

            if favorited is not None:
                updates.append("favorited = ?")
                params.append(1 if favorited else 0)
                # Auto-unarchive when favoriting
                if favorited:
                    updates.append("archived_at = NULL")

            if interesting_override is not None:
                updates.append("interesting_override = ?")
                params.append(1 if interesting_override else 0)

            if user_feedback_provided:
                updates.append("user_feedback = ?")
                params.append(user_feedback)  # Can be 'up', 'down', or None

            params.append(content_id)

            # Field names are constants, only values are parameterized
            query = "UPDATE content SET " + ", ".join(updates) + " WHERE id = ?"  # noqa: S608
            cursor = self.conn.execute(query, params)

            self.conn.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            row_count = cursor.rowcount
            obs_log(
                "db.update",
                table="content",
                operation="update_content_status",
                row_count=row_count,
                duration_ms=duration_ms,
                status="success" if row_count > 0 else "not_found",
            )
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.update",
                table="content",
                operation="update_content_status",
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise sqlite3.Error(f"Failed to update content status: {e}") from e

    def flag_interesting(self, content_id: str) -> bool:
        """Flag a content item as interesting for context analysis.

        Args:
            content_id: UUID of the content to flag

        Returns:
            True if content was flagged, False if not found

        Raises:
            sqlite3.Error: If database operation fails
        """
        start_time = time.time()
        try:
            cursor = self.conn.execute(
                "UPDATE content SET interesting_override = 1 WHERE id = ?",
                (content_id,),
            )
            self.conn.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            row_count = cursor.rowcount
            obs_log(
                "db.update",
                table="content",
                operation="flag_interesting",
                row_count=row_count,
                duration_ms=duration_ms,
                status="success" if row_count > 0 else "not_found",
            )
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.update",
                table="content",
                operation="flag_interesting",
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise sqlite3.Error(f"Failed to flag content as interesting: {e}") from e

    def get_flagged_items(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get content items flagged as interesting.

        Only returns unprioritized items (priority=NULL or LOW) that have been
        flagged by the user for context analysis.

        Args:
            limit: Maximum number of items to return (default 50)

        Returns:
            List of content dictionaries with source information

        Raises:
            sqlite3.Error: If database operation fails
        """
        start_time = time.time()
        try:
            cursor = self.conn.execute(
                """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.interesting_override = 1
                  AND (c.priority IS NULL OR c.priority = 'low')
                  AND c.archived_at IS NULL
                ORDER BY c.fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
            duration_ms = int((time.time() - start_time) * 1000)

            # Convert to list of dicts with JSON parsing
            results = []
            for row in rows:
                content_dict = dict(row)
                # Parse JSON analysis if present
                if content_dict.get("analysis"):
                    try:
                        content_dict["analysis"] = json.loads(content_dict["analysis"])
                    except json.JSONDecodeError:
                        content_dict["analysis"] = None
                results.append(content_dict)

            obs_log(
                "db.select",
                table="content",
                operation="get_flagged_items",
                row_count=len(results),
                duration_ms=duration_ms,
                status="success",
            )
            return results

        except sqlite3.Error as e:
            duration_ms = int((time.time() - start_time) * 1000)
            obs_log(
                "db.select",
                table="content",
                operation="get_flagged_items",
                error=str(e),
                duration_ms=duration_ms,
                status="error",
            )
            raise sqlite3.Error(f"Failed to get flagged items: {e}") from e

    def get_content_by_id(self, content_id: str) -> dict[str, Any] | None:
        """Get a single content item by ID.

        Args:
            content_id: UUID of the content

        Returns:
            Content dictionary if found, None otherwise

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.id = ?
                """,
                (content_id,),
            )
            row = cursor.fetchone()

            if row:
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    try:
                        analysis = json.loads(row["analysis"])
                    except json.JSONDecodeError:
                        analysis = None

                return {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "content": row["content"],
                    "summary": row["summary"],
                    "analysis": analysis,
                    "priority": row["priority"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "read": bool(row["read"]),
                    "favorited": bool(row["favorited"]),
                    "interesting_override": bool(row["interesting_override"]),
                    "user_feedback": row["user_feedback"],
                    "notes": row["notes"],
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by ID: {e}") from e

    def get_latest_content_for_source(self, source_id: str) -> dict[str, Any] | None:
        """Get the most recent content item for a given source.

        Args:
            source_id: UUID of the source

        Returns:
            Most recent content dictionary if found, None otherwise

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.source_id = ?
                ORDER BY c.fetched_at DESC
                LIMIT 1
                """,
                (source_id,),
            )
            row = cursor.fetchone()

            if row:
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    try:
                        analysis = json.loads(row["analysis"])
                    except json.JSONDecodeError:
                        analysis = None

                return {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "content": row["content"],
                    "summary": row["summary"],
                    "analysis": analysis,
                    "priority": row["priority"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "read": bool(row["read"]),
                    "favorited": bool(row["favorited"]),
                    "interesting_override": bool(row["interesting_override"]),
                    "notes": row["notes"],
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get latest content for source: {e}") from e

    def count_unprioritized(self, days: int | None = None) -> int:
        """Count unprioritized content items, optionally filtered by age.

        Args:
            days: If provided, only count items older than this many days

        Returns:
            Count of unprioritized items

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # PRUNE_EXCLUSION_WHERE is a class constant (not user input)
            query = "SELECT COUNT(*) FROM content WHERE " + self.PRUNE_EXCLUSION_WHERE  # noqa: S608
            params = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(UTC) - timedelta(days=days)
                query += " AND published_at < ?"
                params.append(cutoff.isoformat())

            cursor = self.conn.execute(query, params)
            return cursor.fetchone()[0]

        except Exception as e:
            # Return 0 on error for safety
            print(f"Error counting unprioritized items: {e}")
            return 0

    def delete_unprioritized(self, days: int | None = None) -> int:
        """Delete unprioritized content items, optionally filtered by age.

        Uses a transaction for safety and returns the count of deleted items.

        Args:
            days: If provided, only delete items older than this many days

        Returns:
            Number of items deleted

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # First get the count for return value
            count = self.count_unprioritized(days)

            if count == 0:
                return 0

            # PRUNE_EXCLUSION_WHERE is a class constant (not user input)
            query = "DELETE FROM content WHERE " + self.PRUNE_EXCLUSION_WHERE  # noqa: S608
            params = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(UTC) - timedelta(days=days)
                query += " AND published_at < ?"
                params.append(cutoff.isoformat())

            # Execute deletion in a transaction
            cursor = self.conn.execute(query, params)

            # Clean up orphaned vectors (virtual tables don't support CASCADE)
            self.conn.execute(
                "DELETE FROM vec_content WHERE content_id NOT IN (SELECT id FROM content)"
            )

            self.conn.commit()

            # Return the actual number of rows deleted
            return cursor.rowcount

        except Exception as e:
            # Rollback on error
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to delete unprioritized items: {e}") from e

    def cleanup_orphaned_vectors(self) -> int:
        """Clean up orphaned vectors from vec_content table.

        Virtual tables don't support CASCADE, so vectors can remain after
        content deletion. This method removes vectors whose content_id
        no longer exists in the content table.

        Returns:
            Number of orphaned vectors deleted

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # Count orphans first
            cursor = self.conn.execute(
                """
                SELECT COUNT(*) FROM vec_content
                WHERE content_id NOT IN (SELECT id FROM content)
                """
            )
            count = cursor.fetchone()[0]

            if count == 0:
                return 0

            # Delete orphaned vectors
            cursor = self.conn.execute(
                "DELETE FROM vec_content WHERE content_id NOT IN (SELECT id FROM content)"
            )
            self.conn.commit()

            return cursor.rowcount

        except Exception as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to cleanup orphaned vectors: {e}") from e

    def add_embedding(
        self, content_id: str, embedding: list[float], model: str = "all-MiniLM-L6-v2"
    ) -> None:
        """Store embedding vector for content item.

        Args:
            content_id: UUID of the content
            embedding: List of floats (384 dimensions for all-MiniLM-L6-v2)
            model: Model name used to generate embedding

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            import struct

            # Convert list of floats to blob for storage
            embedding_blob = struct.pack(f"{len(embedding)}f", *embedding)

            # Insert or replace embedding
            self.conn.execute(
                """
                INSERT OR REPLACE INTO embeddings (content_id, embedding, model, created_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (content_id, embedding_blob, model),
            )

            # Also update vec_content virtual table for search
            # Convert to format sqlite-vec expects
            embedding_json = json.dumps(embedding)
            self.conn.execute(
                """
                INSERT OR REPLACE INTO vec_content (content_id, embedding)
                VALUES (?, ?)
                """,
                (content_id, embedding_json),
            )

            self.conn.commit()

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to add embedding: {e}") from e

    def _calculate_source_authority(
        self, source_name: str | None, source_type: str | None
    ) -> float:
        """Derive source authority from metadata for search ranking.

        Primary/official sources rank higher than social discussion.
        No stored column needed - calculated at query time.

        Args:
            source_name: Name of the source (e.g., "Anthropic Research")
            source_type: Type of source (rss, reddit, youtube, file)

        Returns:
            Authority score 0.0-1.0
        """
        # Primary sources (official channels) get highest authority
        if source_name and "anthropic" in source_name.lower():
            return 1.0

        # Type-based defaults
        type_authority = {
            "file": 0.9,  # User-added content, intentional
            "rss": 0.6,  # Curated feeds, generally reliable
            "youtube": 0.5,  # Mixed quality
            "reddit": 0.3,  # Discussion/noise, lower signal
        }
        return type_authority.get(source_type or "", 0.5)

    def search_content(
        self,
        query_embedding: list[float],
        limit: int = 20,
        min_score: float = 0.0,
        source_filter: str | None = None,
    ) -> list[dict[str, Any]]:
        """Semantic search using similarity-first ranking with source authority.

        Ranking formula: score = (similarity * 0.80) + (priority * 0.10) + (authority * 0.10)

        Search prioritizes semantic match, with boosts for priority and source authority.
        Authoritative sources (Anthropic, user files) rank higher than social discussion.

        Args:
            query_embedding: Query vector (384 dimensions)
            limit: Maximum number of results to return
            min_score: Minimum relevance score (0.0-1.0)
            source_filter: Optional substring to filter source names (case-insensitive)

        Returns:
            List of content dicts with relevance_score field

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # First get top candidates by similarity from vec_content
            embedding_json = json.dumps(query_embedding)

            # Get top 100 candidates by similarity (we'll re-rank)
            cursor = self.conn.execute(
                """
                SELECT
                    content_id,
                    distance
                FROM vec_content
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT 100
                """,
                (embedding_json,),
            )

            candidates = cursor.fetchall()
            if not candidates:
                return []

            # Get content details for candidates
            content_ids = [row["content_id"] for row in candidates]

            # Build safe IN clause with parameterized placeholders
            # Note: placeholders is just "?,?,?" string, not user input
            placeholders = ",".join(["?"] * len(content_ids))
            query = (
                "SELECT c.*, s.name as source_name, s.type as source_type "
                "FROM content c "
                "LEFT JOIN sources s ON c.source_id = s.id "
                "WHERE c.id IN (" + placeholders + ")"
            )
            params: list[Any] = list(content_ids)

            # Add source filter at SQL level if provided
            if source_filter:
                query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                params.append(source_filter)

            cursor = self.conn.execute(query, params)

            # Build dict of content by id
            content_by_id = {}
            for row in cursor.fetchall():
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    try:
                        analysis = json.loads(row["analysis"])
                    except json.JSONDecodeError:
                        analysis = None

                content_by_id[row["id"]] = {
                    "id": row["id"],
                    "source_id": row["source_id"],
                    "external_id": row["external_id"],
                    "title": row["title"],
                    "url": row["url"],
                    "content": row["content"],
                    "summary": row["summary"],
                    "analysis": analysis,
                    "priority": row["priority"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "read": bool(row["read"]),
                    "favorited": bool(row["favorited"]),
                    "user_feedback": row["user_feedback"],
                    "notes": row["notes"],
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }

            # Calculate weighted scores and re-rank
            results = []
            for candidate in candidates:
                content_id = candidate["content_id"]
                if content_id not in content_by_id:
                    continue

                # Make a copy to avoid modifying original dict
                content = content_by_id[content_id].copy()

                # Similarity score (cosine distance from vec_content, invert to similarity)
                similarity = 1.0 - float(candidate["distance"])

                # Priority weight (minor boost for high-priority content)
                priority_weights = {"high": 1.0, "medium": 0.5, "low": 0.0}
                priority_weight = priority_weights.get(content["priority"], 0.0)

                # Source authority (primary sources > social discussion)
                authority = self._calculate_source_authority(
                    content["source_name"], content["source_type"]
                )

                # Ranking: 80% semantic match, 10% priority, 10% source authority
                # Authoritative sources win ties over Reddit/social chatter
                relevance_score = (
                    similarity * 0.80 + priority_weight * 0.10 + authority * 0.10
                )

                # Apply minimum score filter
                if relevance_score >= min_score:
                    content["relevance_score"] = round(relevance_score, 3)
                    results.append(content)

            # Sort by final relevance score and limit
            results.sort(key=itemgetter("relevance_score"), reverse=True)
            return results[:limit]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to search content: {e}") from e

    def count_content_without_embeddings(self) -> int:
        """Count content items without embeddings.

        Returns:
            Count of items missing embeddings

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM content
                WHERE id NOT IN (SELECT content_id FROM embeddings)
                """
            )
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            raise sqlite3.Error(
                f"Failed to count content without embeddings: {e}"
            ) from e

    def get_content_without_embeddings(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get content items that don't have embeddings yet.

        Used for batch embedding generation.

        Args:
            limit: Maximum number of items to return

        Returns:
            List of content dicts without embeddings

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.id NOT IN (SELECT content_id FROM embeddings)
                ORDER BY c.fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )

            results = []
            for row in cursor.fetchall():
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    try:
                        analysis = json.loads(row["analysis"])
                    except json.JSONDecodeError:
                        analysis = None

                results.append(
                    {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "external_id": row["external_id"],
                        "title": row["title"],
                        "url": row["url"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "analysis": analysis,
                        "priority": row["priority"],
                        "published_at": row["published_at"],
                        "fetched_at": row["fetched_at"],
                        "read": bool(row["read"]),
                        "favorited": bool(row["favorited"]),
                        "notes": row["notes"],
                        "source_name": row["source_name"],
                        "source_type": row["source_type"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )

            return results

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content without embeddings: {e}") from e

    def count_content_without_analysis(self) -> int:
        """Count content items without complete analysis.

        Checks for complete analysis failures where all three fields are NULL.
        Does not count items with NULL priority but valid summary/analysis
        (those were successfully analyzed but filtered as not relevant).

        Returns:
            Count of items needing analysis

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT COUNT(*)
                FROM content
                WHERE priority IS NULL
                  AND summary IS NULL
                  AND analysis IS NULL
                  AND archived_at IS NULL
                """
            )
            return cursor.fetchone()[0]
        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to count content without analysis: {e}") from e

    def get_content_without_analysis(self, limit: int = 100) -> list[dict[str, Any]]:
        """Get content items that lack complete analysis.

        Returns items where all three fields (priority, summary, analysis) are NULL,
        indicating complete analysis failure. Does not return items with NULL priority
        but valid summary/analysis (those were successfully filtered as not relevant).

        Args:
            limit: Maximum number of items to return

        Returns:
            List of content dicts without complete analysis

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.priority IS NULL
                  AND c.summary IS NULL
                  AND c.analysis IS NULL
                  AND c.archived_at IS NULL
                ORDER BY c.fetched_at DESC
                LIMIT ?
                """,
                (limit,),
            )

            results = []
            for row in cursor.fetchall():
                # Parse JSON analysis if present
                analysis = None
                if row["analysis"]:
                    try:
                        analysis = json.loads(row["analysis"])
                    except json.JSONDecodeError:
                        analysis = None

                results.append(
                    {
                        "id": row["id"],
                        "source_id": row["source_id"],
                        "external_id": row["external_id"],
                        "title": row["title"],
                        "url": row["url"],
                        "content": row["content"],
                        "summary": row["summary"],
                        "analysis": analysis,
                        "priority": row["priority"],
                        "published_at": row["published_at"],
                        "fetched_at": row["fetched_at"],
                        "read": bool(row["read"]),
                        "favorited": bool(row["favorited"]),
                        "notes": row["notes"],
                        "source_name": row["source_name"],
                        "source_type": row["source_type"],
                        "created_at": row["created_at"],
                        "updated_at": row["updated_at"],
                    }
                )

            return results

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content without analysis: {e}") from e

    def archive_old_content(self, config: dict[str, Any]) -> int:
        """Archive content based on priority-aware aging windows.

        Args:
            config: Dict with archival window configuration:
                - high_read: Days for read HIGH items (None = never)
                - medium_unread: Days for unread MEDIUM items
                - medium_read: Days for read MEDIUM items
                - low_unread: Days for unread LOW items
                - low_read: Days for read LOW items

        Returns:
            Count of items archived

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # Build parameters for datetime modifiers
            params = []

            # HIGH: Only read + N days (or skip if None)
            if config.get("high_read") is not None:
                params.append(f"-{config['high_read']} days")
            else:
                # Never archive HIGH - use impossibly old date
                params.append("-10000 days")

            # MEDIUM: Unread N days OR read N days
            params.extend(
                [
                    f"-{config['medium_unread']} days",
                    f"-{config['medium_read']} days",
                ]
            )

            # LOW: Unread N days OR read N days
            params.extend(
                [
                    f"-{config['low_unread']} days",
                    f"-{config['low_read']} days",
                ]
            )

            # Single complex UPDATE with priority-aware windows
            query = """
                UPDATE content
                SET archived_at = CURRENT_TIMESTAMP
                WHERE archived_at IS NULL
                  AND favorited = 0
                  AND notes IS NULL
                  AND (
                    -- HIGH: Only read + N days (or never if high_read is None)
                    (priority = 'high' AND read = 1 AND fetched_at < datetime('now', ?))
                    OR
                    -- MEDIUM: Unread N days OR read N days
                    (priority = 'medium' AND (
                      (read = 0 AND fetched_at < datetime('now', ?))
                      OR (read = 1 AND fetched_at < datetime('now', ?))
                    ))
                    OR
                    -- LOW: Unread N days OR read N days
                    (priority = 'low' AND (
                      (read = 0 AND fetched_at < datetime('now', ?))
                      OR (read = 1 AND fetched_at < datetime('now', ?))
                    ))
                  )
            """

            cursor = self.conn.execute(query, params)
            self.conn.commit()
            return cursor.rowcount

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to archive content: {e}") from e

    def count_archived(self) -> int:
        """Count archived content items.

        Returns:
            Number of archived items

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM content WHERE archived_at IS NOT NULL"
            )
            return cursor.fetchone()[0]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to count archived items: {e}") from e

    def count_active(self) -> int:
        """Count active (non-archived) content items.

        Returns:
            Number of active items

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                "SELECT COUNT(*) FROM content WHERE archived_at IS NULL"
            )
            return cursor.fetchone()[0]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to count active items: {e}") from e

    def count_by_priority(self) -> dict[str, int]:
        """Count content items by priority level.

        Returns:
            Dictionary with counts for each priority level:
            - high: Count of high priority items
            - medium: Count of medium priority items
            - low: Count of low priority items
            - unprioritized: Count of items with NULL priority

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute("""
                SELECT
                    COALESCE(priority, 'unprioritized') as priority_level,
                    COUNT(*) as count
                FROM content
                WHERE archived_at IS NULL
                GROUP BY priority_level
            """)

            result = {"high": 0, "medium": 0, "low": 0, "unprioritized": 0}
            for row in cursor.fetchall():
                priority = row[0]
                count = row[1]
                result[priority] = count

            return result

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to count by priority: {e}") from e

    def count_by_read_status(self) -> dict[str, int]:
        """Count content items by read status.

        Returns:
            Dictionary with counts:
            - read: Count of read items
            - unread: Count of unread items

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute("""
                SELECT
                    CASE WHEN read THEN 'read' ELSE 'unread' END as status,
                    COUNT(*) as count
                FROM content
                WHERE archived_at IS NULL
                GROUP BY read
            """)

            result = {"read": 0, "unread": 0}
            for row in cursor.fetchall():
                status = row[0]
                count = row[1]
                result[status] = count

            return result

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to count by read status: {e}") from e

    def get_statistics(self) -> dict[str, Any]:
        """Get all system statistics in a single optimized query.

        Returns comprehensive statistics about content and sources using
        a single query with conditional aggregation for optimal performance.

        Returns:
            Dictionary with content and source statistics:
            - content.total: Total content items
            - content.active: Active (non-archived) items
            - content.archived: Archived items
            - content.by_priority: Counts by priority level
            - content.by_read_status: Counts by read status
            - sources.total: Total sources
            - sources.active: Active sources
            - sources.paused: Paused sources

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # Single query with conditional aggregation for content stats
            content_cursor = self.conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN archived_at IS NULL THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN archived_at IS NOT NULL THEN 1 ELSE 0 END) as archived,
                    SUM(CASE WHEN archived_at IS NULL AND priority = 'high' THEN 1 ELSE 0 END) as high,
                    SUM(CASE WHEN archived_at IS NULL AND priority = 'medium' THEN 1 ELSE 0 END) as medium,
                    SUM(CASE WHEN archived_at IS NULL AND priority = 'low' THEN 1 ELSE 0 END) as low,
                    SUM(CASE WHEN archived_at IS NULL AND priority IS NULL THEN 1 ELSE 0 END) as unprioritized,
                    SUM(CASE WHEN archived_at IS NULL AND read = 1 THEN 1 ELSE 0 END) as read,
                    SUM(CASE WHEN archived_at IS NULL AND read = 0 THEN 1 ELSE 0 END) as unread
                FROM content
            """)

            content_row = content_cursor.fetchone()

            # Single query for source stats
            source_cursor = self.conn.execute("""
                SELECT
                    COUNT(*) as total,
                    SUM(CASE WHEN active = 1 THEN 1 ELSE 0 END) as active,
                    SUM(CASE WHEN active = 0 THEN 1 ELSE 0 END) as paused
                FROM sources
            """)

            source_row = source_cursor.fetchone()

            return {
                "content": {
                    "total": content_row[0] or 0,
                    "active": content_row[1] or 0,
                    "archived": content_row[2] or 0,
                    "by_priority": {
                        "high": content_row[3] or 0,
                        "medium": content_row[4] or 0,
                        "low": content_row[5] or 0,
                        "unprioritized": content_row[6] or 0,
                    },
                    "by_read_status": {
                        "read": content_row[7] or 0,
                        "unread": content_row[8] or 0,
                    },
                },
                "sources": {
                    "total": source_row[0] or 0,
                    "active": source_row[1] or 0,
                    "paused": source_row[2] or 0,
                },
            }

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get statistics: {e}") from e

    def get_feedback_statistics(
        self, since_days: int | None = None
    ) -> dict[str, Any]:
        """Get user feedback statistics aggregated by source and topic.

        Provides aggregated feedback data for preference learning (003).
        Includes per-source vote counts, ratios, and extracted topics.

        Args:
            since_days: Optional limit to feedback within N days (None = all time)

        Returns:
            Dictionary with feedback statistics:
            - totals: Overall upvote/downvote counts
            - by_source: Per-source breakdown with ratios
            - topics_upvoted: Topics from upvoted content (from analysis.matched_interests)
            - topics_downvoted: Topics from downvoted content
            - for_llm_context: Pre-formatted summary for prompt injection

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # Build time filter
            time_filter = ""
            if since_days:
                time_filter = f"AND c.updated_at >= datetime('now', '-{since_days} days')"

            # Overall totals
            totals_cursor = self.conn.execute(f"""
                SELECT
                    SUM(CASE WHEN user_feedback = 'up' THEN 1 ELSE 0 END) as upvotes,
                    SUM(CASE WHEN user_feedback = 'down' THEN 1 ELSE 0 END) as downvotes,
                    COUNT(CASE WHEN user_feedback IS NOT NULL THEN 1 END) as total_votes
                FROM content c
                WHERE user_feedback IS NOT NULL {time_filter}
            """)
            totals_row = totals_cursor.fetchone()

            # Per-source breakdown
            source_cursor = self.conn.execute(f"""
                SELECT
                    s.name,
                    s.id,
                    SUM(CASE WHEN c.user_feedback = 'up' THEN 1 ELSE 0 END) as upvotes,
                    SUM(CASE WHEN c.user_feedback = 'down' THEN 1 ELSE 0 END) as downvotes,
                    COUNT(CASE WHEN c.user_feedback IS NOT NULL THEN 1 END) as total
                FROM content c
                JOIN sources s ON c.source_id = s.id
                WHERE c.user_feedback IS NOT NULL {time_filter}
                GROUP BY s.id, s.name
                HAVING total > 0
                ORDER BY total DESC
            """)

            by_source = []
            for row in source_cursor.fetchall():
                total = row[4]
                upvotes = row[2]
                downvotes = row[3]
                ratio = upvotes / total if total > 0 else 0.0
                by_source.append({
                    "source_name": row[0],
                    "source_id": row[1],
                    "upvotes": upvotes,
                    "downvotes": downvotes,
                    "total": total,
                    "upvote_ratio": round(ratio, 2),
                })

            # Extract topics from upvoted content (from analysis.matched_interests)
            upvoted_cursor = self.conn.execute(f"""
                SELECT c.analysis, c.title
                FROM content c
                WHERE c.user_feedback = 'up' {time_filter}
                AND c.analysis IS NOT NULL
            """)

            topics_upvoted: dict[str, int] = {}
            for row in upvoted_cursor.fetchall():
                try:
                    analysis = json.loads(row[0]) if row[0] else {}
                    interests = analysis.get("matched_interests", [])
                    for interest in interests:
                        if interest:
                            topics_upvoted[interest] = topics_upvoted.get(interest, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            # Extract topics from downvoted content
            downvoted_cursor = self.conn.execute(f"""
                SELECT c.analysis, c.title
                FROM content c
                WHERE c.user_feedback = 'down' {time_filter}
                AND c.analysis IS NOT NULL
            """)

            topics_downvoted: dict[str, int] = {}
            for row in downvoted_cursor.fetchall():
                try:
                    analysis = json.loads(row[0]) if row[0] else {}
                    interests = analysis.get("matched_interests", [])
                    for interest in interests:
                        if interest:
                            topics_downvoted[interest] = topics_downvoted.get(interest, 0) + 1
                except (json.JSONDecodeError, TypeError):
                    pass

            # Sort topics by frequency
            sorted_upvoted = sorted(topics_upvoted.items(), key=lambda x: x[1], reverse=True)
            sorted_downvoted = sorted(topics_downvoted.items(), key=lambda x: x[1], reverse=True)

            # Build LLM context summary (for 003)
            llm_context_parts = []
            if sorted_upvoted:
                top_liked = [t[0] for t in sorted_upvoted[:5]]
                llm_context_parts.append(f"User prefers: {', '.join(top_liked)}")
            if sorted_downvoted:
                top_disliked = [t[0] for t in sorted_downvoted[:5]]
                llm_context_parts.append(f"User dislikes: {', '.join(top_disliked)}")

            # Add source preferences
            trusted_sources = [s["source_name"] for s in by_source if s["upvote_ratio"] >= 0.7 and s["total"] >= 2]
            untrusted_sources = [s["source_name"] for s in by_source if s["upvote_ratio"] <= 0.3 and s["total"] >= 2]
            if trusted_sources:
                llm_context_parts.append(f"Trusted sources: {', '.join(trusted_sources[:3])}")
            if untrusted_sources:
                llm_context_parts.append(f"Less trusted sources: {', '.join(untrusted_sources[:3])}")

            return {
                "totals": {
                    "upvotes": totals_row[0] or 0,
                    "downvotes": totals_row[1] or 0,
                    "total_votes": totals_row[2] or 0,
                },
                "by_source": by_source,
                "topics_upvoted": [{"topic": t[0], "count": t[1]} for t in sorted_upvoted],
                "topics_downvoted": [{"topic": t[0], "count": t[1]} for t in sorted_downvoted],
                "for_llm_context": " | ".join(llm_context_parts) if llm_context_parts else None,
                "time_period": f"last {since_days} days" if since_days else "all time",
            }

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get feedback statistics: {e}") from e
