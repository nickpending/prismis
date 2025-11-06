"""Repository pattern storage layer for Prismis daemon."""

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from operator import itemgetter
from pathlib import Path
from typing import List, Dict, Any, Optional, Union, Tuple, Set

try:
    from .database import get_db_connection
    from .models import ContentItem
except ImportError:
    # Handle case where we're imported from outside the package
    from database import get_db_connection
    from models import ContentItem


class Storage:
    """Repository for all database operations.

    Implements the repository pattern - all SQL stays in this class.
    Uses connection reuse pattern for efficiency.
    """

    def __init__(self, db_path: Optional[Path] = None):
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

    def add_source(self, url: str, source_type: str, name: Optional[str] = None) -> str:
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
            raise sqlite3.Error(f"Failed to add source: {e}")

    def get_active_sources(self) -> List[Dict[str, Any]]:
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
            raise sqlite3.Error(f"Failed to get active sources: {e}")

    def add_content(self, item: Union[ContentItem, Dict[str, Any]]) -> Optional[str]:
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
            return item.id

        except sqlite3.Error as e:
            conn.rollback()
            raise sqlite3.Error(f"Failed to add content: {e}")

    def create_or_update_content(
        self, item: Union[ContentItem, Dict[str, Any]]
    ) -> Tuple[str, bool]:
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
            raise sqlite3.Error(f"Failed to create or update content: {e}")

    def get_existing_external_ids(self, source_id: str) -> Set[str]:
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
            raise sqlite3.Error(f"Failed to get existing external_ids: {e}")

    def _get_by_external_id(self, external_id: str) -> Optional[Dict[str, Any]]:
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
            raise sqlite3.Error(f"Failed to get content by external_id: {e}")

    def get_content_by_priority(
        self, priority: str, limit: int = 50, include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """Get unread content by priority level.

        Args:
            priority: Priority level ('high', 'medium', 'low')
            limit: Maximum number of items to return
            include_archived: Include archived content if True

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

            # Add archived filter unless explicitly including archived
            if not include_archived:
                query += " AND c.archived_at IS NULL"

            query += " ORDER BY c.published_at DESC LIMIT ?"

            cursor = self.conn.execute(query, (priority, limit))

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
                        "notes": row["notes"],
                    }
                )

            return content

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by priority: {e}")

    def get_content_since(
        self, since: Optional[datetime] = None, include_archived: bool = False
    ) -> List[Dict[str, Any]]:
        """Get content since a specific timestamp, or all content if since is None.

        Args:
            since: Timestamp to filter content (returns content published after this time).
                   If None, returns all content regardless of time.
            include_archived: Include archived content if True

        Returns:
            List of content dictionaries with source information
        """
        try:
            # Build query with optional time filter
            query = """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                JOIN sources s ON c.source_id = s.id
                WHERE c.priority IS NOT NULL
            """

            params = []
            if since is not None:
                query += " AND c.fetched_at > ?"
                params.append(since.strftime("%Y-%m-%d %H:%M:%S.%f+00:00"))

            # Add archived filter unless explicitly including archived
            if not include_archived:
                query += " AND c.archived_at IS NULL"

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
                        "notes": row["notes"],
                    }
                )

            return content

        except sqlite3.Error as e:
            since_str = since.isoformat() if since else "beginning"
            raise sqlite3.Error(f"Failed to get content since {since_str}: {e}")

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
            raise sqlite3.Error(f"Failed to mark content as read: {e}")

    def update_source_fetch_status(
        self, source_id: str, success: bool, error_message: Optional[str] = None
    ) -> None:
        """Update source after fetch attempt.

        Args:
            source_id: UUID of the source
            success: Whether fetch was successful
            error_message: Error message if fetch failed
        """
        try:
            if success:
                self.conn.execute(
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
                self.conn.execute(
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

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to update source status: {e}")

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

    def get_all_sources(self) -> List[Dict[str, Any]]:
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
            raise sqlite3.Error(f"Failed to get all sources: {e}")

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
            raise sqlite3.Error(f"Failed to pause source: {e}")

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
            raise sqlite3.Error(f"Failed to resume source: {e}")

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
            raise sqlite3.Error(f"Failed to remove source: {e}")

    def update_content_status(
        self,
        content_id: str,
        read: Optional[bool] = None,
        favorited: Optional[bool] = None,
    ) -> bool:
        """Update read and/or favorited status of content.

        Args:
            content_id: UUID of the content to update
            read: Set read status if provided
            favorited: Set favorited status if provided

        Returns:
            True if content was updated, False if not found

        Raises:
            ValueError: If no update parameters provided
            sqlite3.Error: If database operation fails
        """
        if read is None and favorited is None:
            raise ValueError("At least one of read or favorited must be provided")

        try:
            # Use separate queries for each case to avoid dynamic SQL
            # Auto-unarchive when favoriting (archived_at = NULL)
            if read is not None and favorited is not None:
                # Update both fields
                cursor = self.conn.execute(
                    "UPDATE content SET read = ?, favorited = ?, archived_at = NULL WHERE id = ?",
                    (1 if read else 0, 1 if favorited else 0, content_id),
                )
            elif read is not None:
                # Update only read status (no unarchiving)
                cursor = self.conn.execute(
                    "UPDATE content SET read = ? WHERE id = ?",
                    (1 if read else 0, content_id),
                )
            else:  # favorited is not None
                # Update only favorited status (auto-unarchive if favoriting)
                cursor = self.conn.execute(
                    "UPDATE content SET favorited = ?, archived_at = NULL WHERE id = ?",
                    (1 if favorited else 0, content_id),
                )

            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to update content status: {e}")

    def get_content_by_id(self, content_id: str) -> Optional[Dict[str, Any]]:
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
                    "notes": row["notes"],
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by ID: {e}")

    def get_latest_content_for_source(self, source_id: str) -> Optional[Dict[str, Any]]:
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
                    "notes": row["notes"],
                    "source_name": row["source_name"],
                    "source_type": row["source_type"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get latest content for source: {e}")

    def count_unprioritized(self, days: Optional[int] = None) -> int:
        """Count unprioritized content items, optionally filtered by age.

        Args:
            days: If provided, only count items older than this many days

        Returns:
            Count of unprioritized items

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            query = """
                SELECT COUNT(*)
                FROM content
                WHERE (priority IS NULL OR priority = '')
            """
            params = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
                query += " AND published_at < ?"
                params.append(cutoff.isoformat())

            cursor = self.conn.execute(query, params)
            return cursor.fetchone()[0]

        except Exception as e:
            # Return 0 on error for safety
            print(f"Error counting unprioritized items: {e}")
            return 0

    def delete_unprioritized(self, days: Optional[int] = None) -> int:
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

            query = """
                DELETE FROM content
                WHERE (priority IS NULL OR priority = '')
            """
            params = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(timezone.utc) - timedelta(days=days)
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
            raise sqlite3.Error(f"Failed to delete unprioritized items: {e}")

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
            raise sqlite3.Error(f"Failed to cleanup orphaned vectors: {e}")

    def add_embedding(
        self, content_id: str, embedding: List[float], model: str = "all-MiniLM-L6-v2"
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
            raise sqlite3.Error(f"Failed to add embedding: {e}")

    def search_content(
        self,
        query_embedding: List[float],
        limit: int = 20,
        min_score: float = 0.0,
    ) -> List[Dict[str, Any]]:
        """Semantic search using similarity-first ranking.

        Ranking formula: score = (similarity * 0.90) + (priority_weight * 0.10)

        Search prioritizes semantic match - you want what you searched for, not just
        high-priority content. Priority provides minor boost to break ties.

        Args:
            query_embedding: Query vector (384 dimensions)
            limit: Maximum number of results to return
            min_score: Minimum relevance score (0.0-1.0)

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

            cursor = self.conn.execute(query, content_ids)

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

                # Similarity-first ranking: 90% semantic match, 10% priority boost
                # Search is about finding what you're looking for, not surfacing important content
                relevance_score = similarity * 0.90 + priority_weight * 0.10

                # Apply minimum score filter
                if relevance_score >= min_score:
                    content["relevance_score"] = round(relevance_score, 3)
                    results.append(content)

            # Sort by final relevance score and limit
            results.sort(key=itemgetter("relevance_score"), reverse=True)
            return results[:limit]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to search content: {e}")

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
            raise sqlite3.Error(f"Failed to count content without embeddings: {e}")

    def get_content_without_embeddings(self, limit: int = 100) -> List[Dict[str, Any]]:
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
            raise sqlite3.Error(f"Failed to get content without embeddings: {e}")

    def archive_old_content(self, config: Dict[str, Any]) -> int:
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
            raise sqlite3.Error(f"Failed to archive content: {e}")

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
            raise sqlite3.Error(f"Failed to count archived items: {e}")

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
            raise sqlite3.Error(f"Failed to count active items: {e}")
