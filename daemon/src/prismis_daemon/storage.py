"""Repository pattern storage layer for Prismis daemon."""

import json
import sqlite3
import time
import uuid
from datetime import UTC, datetime, timedelta
from operator import itemgetter
from pathlib import Path
from typing import Any, ClassVar

from .database import get_db_connection
from .models import ContentItem
from .observability import log as obs_log
from .readability import is_readable


def utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string with an explicit +00:00 offset.

    The single timestamp source for every application write to a datetime column
    (INV-STORAGE-TS-1).
    """
    return datetime.now(UTC).isoformat()


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
        AND (user_feedback != 'up' OR user_feedback IS NULL)
    """

    def __init__(self, db_path: Path | None = None):
        """Initialize storage with database connection.

        Args:
            db_path: Optional custom database path for testing.
                     Defaults to $XDG_DATA_HOME/prismis/prismis.db
                     (or ~/.local/share/prismis/prismis.db)
        """
        self.db_path = db_path
        self._conn: sqlite3.Connection | None = None  # Lazy connection initialization
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

    @staticmethod
    def _parse_analysis_json(raw: str | None) -> dict[str, Any] | None:
        """Parse a content row's analysis JSON text, or None.

        None on an empty/NULL column and on JSON that fails to parse -- a read path
        never raises over a corrupt or partial analysis write. The one parse-or-None
        rule every row-mapping method below shares, instead of a copy of the same
        try/except at each of them.
        """
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _kind_filter_sql(kind_filter: list[str] | None, params: list[Any]) -> str:
        """The `AND json_extract(...) IN (...)` fragment for a kind filter (SC-1/SC-2).

        Appends `kind_filter`'s values onto `params` (in placeholder order) and
        returns the fragment to concatenate onto the caller's own WHERE clause, or
        `""` when no filter is given -- the one place this fragment is spelled,
        shared by every kind-filtered query (get_content_by_priority,
        get_content_since, get_flagged_items, and search_content's KNN candidate
        subquery) instead of a hand-copied string at each of them.
        """
        if not kind_filter:
            return ""
        placeholders = ",".join(["?"] * len(kind_filter))
        params.extend(kind_filter)
        return f" AND json_extract(c.analysis, '$.kind') IN ({placeholders})"

    @staticmethod
    def _source_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Map a `sources` row to its canonical dict.

        get_active_sources and get_all_sources run different WHERE/ORDER BY
        clauses over the same 10-column SELECT and used to hand-copy this same
        dict literal each; one mapper, both callers.
        """
        return {
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

    def _content_item_from_dict(self, item: dict[str, Any]) -> ContentItem:
        """Coerce a raw content dict into a ContentItem, defaulting source_id.

        Pure -- no DB write -- so each of add_content and create_or_update_content
        keeps its own connection choice for the insert/update that follows (they
        intentionally differ: add_content opens a fresh connection per call,
        create_or_update_content reuses self.conn).
        """
        source_id = item.get("source_id")
        if not source_id:
            sources = self.get_active_sources()
            if sources:
                source_id = sources[0]["id"]
            else:
                raise ValueError(
                    "No source_id provided and no active sources available"
                )

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
        return content_item

    def _content_row_common_fields(self, row: sqlite3.Row) -> dict[str, Any]:
        """The content-table fields every content-read method returns, whether
        or not its query joins sources.

        `_get_by_external_id`'s own SELECT lists these columns explicitly (no
        join); every joined read's `c.*` selects them too -- one shared base
        instead of two independently hand-copied dict literals.
        """
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "title": row["title"],
            "url": row["url"],
            "content": row["content"],
            "summary": row["summary"],
            "analysis": self._parse_analysis_json(row["analysis"]),
            "priority": row["priority"],
            "published_at": row["published_at"],
            "fetched_at": row["fetched_at"],
            "read": bool(row["read"]),
            "favorited": bool(row["favorited"]),
            "notes": row["notes"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _content_row_to_dict(
        self, row: sqlite3.Row, *, omit: tuple[str, ...] = ()
    ) -> dict[str, Any]:
        """Map a `content c JOIN/LEFT JOIN sources s` row to its canonical dict.

        Every joined content-read method below selects `c.*` plus
        `source_name`/`source_type` from the sources join, so the row always
        carries this full field set regardless of which subset a given
        caller has historically returned. `omit` lets a caller keep its own
        narrower shape (see each call site) instead of silently starting to
        return fields it never did -- get_latest_content_for_source is the
        one caller that takes the full set on purpose: it used to drop
        user_feedback, unlike every other read method here, which was a
        latent bug, not a narrower contract.
        """
        full = {
            **self._content_row_common_fields(row),
            "interesting_override": bool(row["interesting_override"]),
            "user_feedback": row["user_feedback"],
            "source_name": row["source_name"],
            "source_type": row["source_type"],
        }
        for key in omit:
            del full[key]
        return full

    def _map_content_rows(
        self, rows: list[sqlite3.Row], *, omit: tuple[str, ...] = ()
    ) -> list[dict[str, Any]]:
        """Map every row in `rows` through `_content_row_to_dict`.

        Every content-list read shares this same mapping step over its own
        `cursor.fetchall()`.
        """
        return [self._content_row_to_dict(row, omit=omit) for row in rows]

    def _query_joined_content(
        self,
        where_sql: str,
        params: tuple[Any, ...] | list[Any] = (),
        *,
        omit: tuple[str, ...] = (),
    ) -> list[dict[str, Any]]:
        """Run the canonical `content c LEFT JOIN sources s` SELECT, ending in
        `where_sql`, and map every row.

        get_content_by_id, get_latest_content_for_source, get_content_without_
        embeddings, get_content_without_analysis, get_content_needing_kind, and
        search_content's candidate lookup all run this exact SELECT header and
        differ only in their WHERE/ORDER BY/LIMIT clause, their param list, and
        which canonical fields they return.
        """
        # String concatenation, not an f-string -- matches this file's existing
        # pattern for a hardcoded (never user-input) dynamic SQL fragment (see
        # PRUNE_EXCLUSION_WHERE and every `query +=` above).
        cursor = self.conn.execute(
            """
            SELECT c.*, s.name as source_name, s.type as source_type
            FROM content c
            LEFT JOIN sources s ON c.source_id = s.id
            """
            + where_sql,
            tuple(params),
        )
        return self._map_content_rows(cursor.fetchall(), omit=omit)

    @staticmethod
    def _content_insert_values(item: ContentItem) -> tuple[str | None, str | None, str]:
        """Derive the insert-time-only fields from a ContentItem.

        The JSON-serialized analysis, and the ISO-string published_at/
        fetched_at -- Python 3.12 deprecated the default sqlite3 datetime
        adapter, so both datetimes get converted to ISO strings here rather
        than bound directly (matches deep_extractor.py's canonical UTC-ISO
        timestamp shape). add_content and create_or_update_content's create
        branch both derive these before their own (different) INSERT.
        """
        analysis_json = json.dumps(item.analysis) if item.analysis else None
        published_at_iso = item.published_at.isoformat() if item.published_at else None
        fetched_at_iso = (
            item.fetched_at.isoformat()
            if item.fetched_at
            else utc_now_iso()
        )
        return analysis_json, published_at_iso, fetched_at_iso

    @staticmethod
    def _insert_content_row(
        conn: sqlite3.Connection,
        item: ContentItem,
        analysis_json: str | None,
        published_at_iso: str | None,
        fetched_at_iso: str,
    ) -> None:
        """Run the canonical content INSERT against the connection the caller
        supplies.

        add_content and create_or_update_content intentionally use different
        connections (a fresh one vs the shared instance connection) -- the
        connection stays a parameter here so that choice stays visible at
        each call site instead of being decided inside this helper.
        """
        now = utc_now_iso()
        conn.execute(
            """
            INSERT INTO content (
                id, source_id, external_id, title, url, content,
                summary, analysis, priority, published_at,
                fetched_at, read, favorited, notes,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                published_at_iso,
                fetched_at_iso,
                item.read,
                item.favorited,
                item.notes,
                now,
                now,
            ),
        )

    def _log_update_result(
        self,
        operation: str,
        duration_ms: int,
        row_count: int | None = None,
        error: str | None = None,
    ) -> None:
        """Emit the db.update event a single-row content UPDATE always logs.

        update_content_status and flag_interesting both wrap their UPDATE in
        the same success/not_found-by-row_count, error-by-exception shape;
        one obs_log call site instead of two hand-copied ones.
        """
        if error is not None:
            obs_log(
                "db.update",
                table="content",
                operation=operation,
                error=error,
                duration_ms=duration_ms,
                status="error",
            )
        else:
            obs_log(
                "db.update",
                table="content",
                operation=operation,
                row_count=row_count,
                duration_ms=duration_ms,
                status="success" if row_count else "not_found",
            )

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
            now = utc_now_iso()
            self.conn.execute(
                """
                INSERT INTO sources (id, url, type, name, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (source_id, url, source_type, name, now, now),
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

            sources = [self._source_row_to_dict(row) for row in cursor.fetchall()]

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
            item = self._content_item_from_dict(item)

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

            analysis_json, published_at_iso, fetched_at_iso = (
                self._content_insert_values(item)
            )
            self._insert_content_row(
                conn, item, analysis_json, published_at_iso, fetched_at_iso
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
            item = self._content_item_from_dict(item)

        start_time = time.time()

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
                    SET content = ?, summary = ?, analysis = ?, priority = ?, updated_at = ?
                    WHERE external_id = ?
                    """,
                    (
                        item.content,
                        item.summary,
                        analysis_json,
                        item.priority,
                        utc_now_iso(),
                        item.external_id,
                    ),
                )
                self.conn.commit()
                obs_log(
                    "db.insert",
                    table="content",
                    operation="create_or_update_content",
                    row_count=1,
                    duration_ms=int((time.time() - start_time) * 1000),
                    status="updated",
                )
                return existing["id"], False

            else:
                # Create new content (same logic as add_content)
                analysis_json, published_at_iso, fetched_at_iso = (
                    self._content_insert_values(item)
                )
                self._insert_content_row(
                    self.conn, item, analysis_json, published_at_iso, fetched_at_iso
                )
                self.conn.commit()
                obs_log(
                    "db.insert",
                    table="content",
                    operation="create_or_update_content",
                    row_count=1,
                    duration_ms=int((time.time() - start_time) * 1000),
                    status="created",
                )
                return item.id, True

        except sqlite3.Error as e:
            self.conn.rollback()
            obs_log(
                "db.insert",
                table="content",
                operation="create_or_update_content",
                duration_ms=int((time.time() - start_time) * 1000),
                status="error",
                error=str(e),
            )
            raise sqlite3.Error(f"Failed to create or update content: {e}") from e

    def update_analysis(self, content_id: str, analysis: dict) -> bool:
        """Patch the analysis JSON column for a content row.

        Used by the on-demand extraction endpoint to store deep extraction
        without rewriting other fields. Caller is responsible for merging
        any new sub-keys into the analysis dict before passing it in.

        Args:
            content_id: UUID of the content row
            analysis: Full analysis dict to persist (caller merges as needed)

        Returns:
            True if a row was updated, False if no row matched.

        Raises:
            sqlite3.Error: on write failure
        """
        try:
            analysis_json = json.dumps(analysis)
            cursor = self.conn.execute(
                "UPDATE content SET analysis = ?, updated_at = ? WHERE id = ?",
                (analysis_json, utc_now_iso(), content_id),
            )
            self.conn.commit()
            return cursor.rowcount > 0
        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to update analysis: {e}") from e

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

    def get_readable_external_ids(self, source_id: str) -> set[str]:
        """external_ids for `source_id` whose stored analysis marks them
        settled -- title_only false or absent, or title-only only because the
        light model judged it not substantive (`title_only_reason` starting
        `model:`), which re-extraction cannot change (SC-4).

        The orchestrator hands this set (not `get_existing_external_ids`'s full
        set) to the RSS/Reddit/YouTube fetchers so they skip re-extraction for
        items already known to be real content, while a title-only item's
        external_id stays out of this set so a later fetch cycle retries it.

        Args:
            source_id: UUID of the source to get readable external_ids for

        Returns:
            Set of external_id strings stored readably for the source

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            cursor = self.conn.execute(
                """
                SELECT external_id FROM content
                WHERE source_id = ?
                  AND (json_extract(analysis, '$.title_only') IS NULL
                       OR json_extract(analysis, '$.title_only') = 0
                       OR json_extract(analysis, '$.title_only_reason') LIKE 'model:%')
                """,
                (source_id,),
            )
            return {row[0] for row in cursor.fetchall()}

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get readable external_ids: {e}") from e

    def get_unreadable_content(
        self, source_type: str, limit: int, batch_size: int = 500
    ) -> list[dict[str, Any]]:
        """Non-archived content of `source_type` whose stored `content`
        `readability.is_readable` rejects, oldest fetched first, bounded by
        `limit` (SC-2, refetch-unreadable).

        Unlike `get_readable_external_ids`, this checks the content's actual
        shape rather than a `title_only` key -- items analysed before gh #80
        predate that key ever being written, and a `title_only`-based query
        would silently skip every one of them, the exact gap this backfill
        exists to close. Readability is a Python-side check, so rows are
        paged in (oldest fetched_at first) and filtered here rather than in
        SQL, stopping as soon as `limit` unreadable rows are found.

        Args:
            source_type: 'youtube', 'rss' or 'reddit'
            limit: Maximum number of unreadable items to return
            batch_size: Page size for the underlying scan

        Returns:
            List of content dicts, oldest fetched_at first

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            selected: list[dict[str, Any]] = []
            offset = 0
            while len(selected) < limit:
                cursor = self.conn.execute(
                    """
                    SELECT c.*, s.name as source_name, s.type as source_type
                    FROM content c
                    JOIN sources s ON c.source_id = s.id
                    WHERE s.type = ? AND c.archived_at IS NULL
                    ORDER BY c.fetched_at ASC
                    LIMIT ? OFFSET ?
                    """,
                    (source_type, batch_size, offset),
                )
                rows = cursor.fetchall()
                if not rows:
                    break

                for row in self._map_content_rows(
                    rows, omit=("interesting_override", "user_feedback")
                ):
                    if not is_readable(row["content"]):
                        selected.append(row)
                        if len(selected) >= limit:
                            break

                offset += batch_size

            return selected

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get unreadable content: {e}") from e

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
                return self._content_row_common_fields(row)
            return None

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by external_id: {e}") from e

    def get_content_by_priority(
        self,
        priority: str,
        limit: int = 50,
        include_archived: bool = False,
        source_filter: str | None = None,
        since: datetime | None = None,
        kind_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get unread content by priority level.

        Args:
            priority: Priority level ('high', 'medium', 'low')
            limit: Maximum number of items to return
            include_archived: Include archived content if True
            source_filter: Filter by source name (case-insensitive substring match)
            since: Only content fetched after this instant
            kind_filter: Optional list of kind values (SC-2). Applied in the SQL
                WHERE clause, before `LIMIT`, so an item of the requested kind older
                than the newest `limit` items of another kind is still found.

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

            if since is not None:
                query += " AND datetime(c.fetched_at) > datetime(?)"
                params.append(since.isoformat())

            query += self._kind_filter_sql(kind_filter, params)

            query += " ORDER BY c.published_at DESC LIMIT ?"
            params.append(limit)

            cursor = self.conn.execute(query, tuple(params))

            return self._map_content_rows(
                cursor.fetchall(), omit=("created_at", "updated_at")
            )

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by priority: {e}") from e

    def get_content_since(
        self,
        since: datetime | None = None,
        include_archived: bool = False,
        source_filter: str | None = None,
        kind_filter: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get content since a specific timestamp, or all content if since is None.

        Args:
            since: Timestamp to filter content (returns content published after this time).
                   If None, returns all content regardless of time.
            include_archived: Include archived content if True
            source_filter: Filter by source name (case-insensitive substring match)
            kind_filter: Optional list of kind values (SC-2), applied in the SQL
                WHERE clause rather than as a Python post-filter.

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
                # fetched_at is stored in four historical string shapes (space or T
                # separator, with or without +00:00); datetime() normalizes both sides,
                # where a raw string compare treats every row from the bound's date
                # as newer than it (#61).
                query += " AND datetime(c.fetched_at) > datetime(?)"
                params.append(since.isoformat())

            # Add archived filter unless explicitly including archived
            if not include_archived:
                query += " AND c.archived_at IS NULL"

            # Add source filter if provided
            if source_filter:
                query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                params.append(source_filter)

            query += self._kind_filter_sql(kind_filter, params)

            query += " ORDER BY c.priority ASC, c.published_at DESC"

            cursor = self.conn.execute(query, tuple(params))

            return self._map_content_rows(
                cursor.fetchall(), omit=("created_at", "updated_at")
            )

        except sqlite3.Error as e:
            since_str = since.isoformat() if since else "beginning"
            raise sqlite3.Error(f"Failed to get content since {since_str}: {e}") from e

    # The analysis keys a list view reads (list shape contract in
    # docs/architecture/boundaries.md). A new list view that reads another key adds
    # it here; the full analysis JSON is never read for view="list".
    LIST_ANALYSIS_KEYS: ClassVar[tuple[str, ...]] = (
        "kind",
        "kind_confidence",
        "title_only",
        "title_only_reason",
        "metrics",
        "metadata",
        "matched_interests",
        "preference_influenced",
    )

    # ORDER BY per sort; priority orders high, medium, low, then NULL.
    _LIST_ORDER_BY: ClassVar[dict[str, str]] = {
        "priority": (
            "CASE c.priority WHEN 'high' THEN 0 WHEN 'medium' THEN 1 "
            "WHEN 'low' THEN 2 ELSE 3 END, c.published_at DESC, c.id"
        ),
        "date": "c.published_at DESC, c.id",
        "unread": "c.read ASC, c.published_at DESC, c.id",
    }

    def get_content_list(
        self,
        limit: int = 50,
        *,
        view: str = "full",
        sort_by: str = "priority",
        since: datetime | None = None,
        include_archived: bool = False,
        source_filter: str | None = None,
        kind_filter: list[str] | None = None,
        priorities: list[str] | None = None,
        unread_only: bool = False,
        interesting: bool = False,
    ) -> list[dict[str, Any]]:
        """One bounded SQL read behind GET /api/entries: filter, sort and LIMIT all
        run in SQLite, so at most `limit` rows are ever read into Python.

        Args:
            limit: Maximum rows returned.
            view: "full" selects every column (content and the whole analysis);
                "list" selects every column except content and analysis, and
                projects analysis down to LIST_ANALYSIS_KEYS inside SQL, so the
                full analysis JSON is never read. A list row has no `content` key,
                its `analysis` holds only the list keys that are present (an empty
                dict for NULL, invalid or non-object analysis JSON), and
                `has_deep_extraction` says whether analysis had `deep_extraction`.
            sort_by: "priority" (high, medium, low, none), "date" or "unread";
                each ties on published_at newest first.
            since: Only rows fetched after this instant.
            include_archived: Include archived rows if True.
            source_filter: Case-insensitive source name substring.
            kind_filter: Kind values, applied in WHERE before LIMIT.
            priorities: Only these priority levels.
            unread_only: Only `read = 0` rows.
            interesting: Only `user_feedback = 'up'` rows.

        Raises:
            ValueError: unknown `view`.
            sqlite3.Error: if the query fails.
        """
        if view not in ("full", "list"):
            raise ValueError(f"Unknown content view: {view!r}")
        order_by = self._LIST_ORDER_BY.get(sort_by, self._LIST_ORDER_BY["priority"])
        try:
            if view == "list":
                # `->` keeps JSON types (true stays true, objects stay objects) where
                # json_extract would turn a boolean into 1/0. json_valid guards a row
                # whose analysis is NULL, empty or not JSON (a classifier failure),
                # the way get_distinct_kinds does.
                pairs = ", ".join(
                    f"'{key}', c.analysis -> '$.{key}'" for key in self.LIST_ANALYSIS_KEYS
                )
                columns = (
                    "c.id, c.source_id, c.external_id, c.title, c.url, c.summary,"
                    " c.priority, c.published_at, c.fetched_at, c.read, c.favorited,"
                    " c.notes, c.interesting_override, c.user_feedback,"
                    f" CASE WHEN json_valid(c.analysis) THEN json_object({pairs})"
                    " END AS analysis_list,"
                    " CASE WHEN json_valid(c.analysis) THEN"
                    " json_extract(c.analysis, '$.deep_extraction') IS NOT NULL"
                    " ELSE 0 END AS has_deep_extraction"
                )
            else:
                columns = "c.*"
            query = (
                f"SELECT {columns}, s.name as source_name, s.type as source_type"
                " FROM content c JOIN sources s ON c.source_id = s.id WHERE 1=1"
            )
            params: list[Any] = []

            if since is not None:
                query += " AND datetime(c.fetched_at) > datetime(?)"
                params.append(since.isoformat())
            if not include_archived:
                query += " AND c.archived_at IS NULL"
            if source_filter:
                query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                params.append(source_filter)
            query += self._kind_filter_sql(kind_filter, params)
            if priorities:
                query += f" AND c.priority IN ({','.join(['?'] * len(priorities))})"
                params.extend(priorities)
            if unread_only:
                query += " AND c.read = 0"
            if interesting:
                query += " AND c.user_feedback = 'up'"

            query += f" ORDER BY {order_by} LIMIT ?"
            params.append(limit)

            rows = self.conn.execute(query, tuple(params)).fetchall()
            omit = ("created_at", "updated_at")
            if view == "full":
                items = self._map_content_rows(rows, omit=omit)
                for item in items:
                    analysis = item.get("analysis")
                    item["has_deep_extraction"] = (
                        isinstance(analysis, dict)
                        and analysis.get("deep_extraction") is not None
                    )
                return items
            return [self._list_row_to_dict(row) for row in rows]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content list: {e}") from e

    @staticmethod
    def _list_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        """Map a `view="list"` row: the joined content fields minus content and the
        full analysis, plus the projected analysis and has_deep_extraction."""
        projected = row["analysis_list"]
        analysis: dict[str, Any] = {}
        if projected:
            analysis = {k: v for k, v in json.loads(projected).items() if v is not None}
        return {
            "id": row["id"],
            "source_id": row["source_id"],
            "external_id": row["external_id"],
            "title": row["title"],
            "url": row["url"],
            "summary": row["summary"],
            "analysis": analysis,
            "priority": row["priority"],
            "published_at": row["published_at"],
            "fetched_at": row["fetched_at"],
            "read": bool(row["read"]),
            "favorited": bool(row["favorited"]),
            "notes": row["notes"],
            "interesting_override": bool(row["interesting_override"]),
            "user_feedback": row["user_feedback"],
            "source_name": row["source_name"],
            "source_type": row["source_type"],
            "has_deep_extraction": bool(row["has_deep_extraction"]),
        }

    def get_distinct_kinds(self, since: datetime | None = None) -> list[str]:
        """Get the sorted, de-duplicated kind values present in non-archived
        content (SC-1, gh #84).

        Mirrors the TUI's GetDistinctKinds (tui/internal/db/queries.go:581-611),
        scoped to non-archived only -- the web UI has no archived view to toggle,
        so unlike the TUI's showArchived flag this always excludes archived rows.
        json_valid guards json_extract against a row whose analysis is empty or
        not JSON at all (a classifier failure or a pre-classification item, per
        INV-002) -- without it, one such row would error the whole query instead
        of just being skipped. A null or missing "kind" key (unclassified) is
        excluded by the IS NOT NULL check, same as the TUI's version.

        Args:
            since: Only consider content fetched after this instant. If None,
                considers all non-archived content regardless of time.

        Returns:
            Sorted list of kind strings (e.g. ["news", "release", "tutorial"]).
            Empty list, not an error, when nothing qualifies.

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            query = """
                SELECT DISTINCT json_extract(analysis, '$.kind') AS kind
                FROM content
                WHERE archived_at IS NULL
                  AND json_valid(analysis)
                  AND json_extract(analysis, '$.kind') IS NOT NULL
                  AND json_extract(analysis, '$.kind') != ''
            """
            params: list[Any] = []
            if since is not None:
                query += " AND datetime(fetched_at) > datetime(?)"
                params.append(since.isoformat())
            query += " ORDER BY kind"

            cursor = self.conn.execute(query, tuple(params))
            return [row["kind"] for row in cursor.fetchall()]

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get distinct kinds: {e}") from e

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
                SET read = 1, updated_at = ?
                WHERE id = ?
                """,
                (utc_now_iso(), content_id),
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
            now = utc_now_iso()
            if success:
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET last_fetched_at = ?,
                        error_count = 0,
                        last_error = NULL,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, source_id),
                )
            else:
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET error_count = error_count + 1,
                        last_error = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (error_message, now, source_id),
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
            now = utc_now_iso()
            if "name" in update_data and "url" in update_data:
                # Update both name and URL
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (update_data["name"], update_data["url"], now, source_id),
                )
            elif "name" in update_data:
                # Update only name
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET name = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (update_data["name"], now, source_id),
                )
            elif "url" in update_data:
                # Update only URL
                cursor = self.conn.execute(
                    """
                    UPDATE sources
                    SET url = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (update_data["url"], now, source_id),
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

            sources = [self._source_row_to_dict(row) for row in cursor.fetchall()]

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
        return self._execute_source_update(
            "UPDATE sources SET active = 0, updated_at = ? WHERE id = ?",
            (utc_now_iso(), source_id),
            "pause source",
        )

    def _execute_source_update(
        self, sql: str, params: tuple[Any, ...], action: str
    ) -> bool:
        """Run one sources UPDATE, commit, and report whether a row matched."""
        try:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            raise sqlite3.Error(f"Failed to {action}: {e}") from e

    def resume_source(self, source_id: str) -> bool:
        """Resume a paused content source (set active and reset errors).

        Args:
            source_id: UUID of the source to resume

        Returns:
            True if source was resumed, False if not found

        Raises:
            sqlite3.Error: If database operation fails
        """
        return self._execute_source_update(
            "UPDATE sources SET active = 1, error_count = 0, last_error = NULL,"
            " updated_at = ? WHERE id = ?",
            (utc_now_iso(), source_id),
            "resume source",
        )

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
            params: list[Any] = []

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
            query = "UPDATE content SET " + ", ".join(updates) + " WHERE id = ?"
            cursor = self.conn.execute(query, params)

            self.conn.commit()
            duration_ms = int((time.time() - start_time) * 1000)
            row_count = cursor.rowcount
            self._log_update_result(
                "update_content_status", duration_ms, row_count=row_count
            )
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            self._log_update_result("update_content_status", duration_ms, error=str(e))
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
            self._log_update_result(
                "flag_interesting", duration_ms, row_count=row_count
            )
            return cursor.rowcount > 0

        except sqlite3.Error as e:
            self.conn.rollback()
            duration_ms = int((time.time() - start_time) * 1000)
            self._log_update_result("flag_interesting", duration_ms, error=str(e))
            raise sqlite3.Error(f"Failed to flag content as interesting: {e}") from e

    def get_flagged_items(
        self, limit: int = 50, kind_filter: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get content items flagged for context analysis (upvoted items).

        Returns items with user_feedback='up' for context analysis.
        Works on all content regardless of priority (not just unprioritized).

        Note: Previously used interesting_override, now uses user_feedback='up'.
        The migration in Makefile converts interesting_override=1 to user_feedback='up'.

        Args:
            limit: Maximum number of items to return (default 50)
            kind_filter: Optional list of kind values (SC-2), applied in the SQL
                WHERE clause before `LIMIT`, so a flagged item of the requested
                kind older than the newest `limit` flagged items is still found.

        Returns:
            List of content dictionaries with source information

        Raises:
            sqlite3.Error: If database operation fails
        """
        start_time = time.time()
        try:
            query = """
                SELECT c.*, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.user_feedback = 'up'
                  AND c.archived_at IS NULL
            """
            params: list[Any] = []
            query += self._kind_filter_sql(kind_filter, params)
            query += " ORDER BY c.fetched_at DESC LIMIT ?"
            params.append(limit)

            cursor = self.conn.execute(query, tuple(params))
            rows = cursor.fetchall()
            duration_ms = int((time.time() - start_time) * 1000)

            # Convert to list of dicts with JSON parsing
            results = []
            for row in rows:
                content_dict = dict(row)
                content_dict["analysis"] = self._parse_analysis_json(
                    content_dict.get("analysis")
                )
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
            rows = self._query_joined_content("WHERE c.id = ?", (content_id,))
            return rows[0] if rows else None

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
            # Full canonical set, including user_feedback -- every other
            # joined-row read method here returns it; this one used to
            # silently drop it (a latent bug, not a narrower contract).
            rows = self._query_joined_content(
                "WHERE c.source_id = ? ORDER BY c.fetched_at DESC LIMIT 1",
                (source_id,),
            )
            return rows[0] if rows else None

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
            query = "SELECT COUNT(*) FROM content WHERE " + self.PRUNE_EXCLUSION_WHERE
            params: list[Any] = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(UTC) - timedelta(days=days)
                query += " AND datetime(published_at) < datetime(?)"
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
            query = "DELETE FROM content WHERE " + self.PRUNE_EXCLUSION_WHERE
            params: list[Any] = []

            if days is not None:
                # Calculate cutoff datetime
                cutoff = datetime.now(UTC) - timedelta(days=days)
                query += " AND datetime(published_at) < datetime(?)"
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
                VALUES (?, ?, ?, ?)
                """,
                (content_id, embedding_blob, model, utc_now_iso()),
            )

            # vec0 ignores OR REPLACE and raises on a duplicate key, so an existing
            # vector is deleted first; otherwise a re-analysed item stays searchable
            # only by its old vector.
            embedding_json = json.dumps(embedding)
            self.conn.execute(
                "DELETE FROM vec_content WHERE content_id = ?", (content_id,)
            )
            self.conn.execute(
                "INSERT INTO vec_content (content_id, embedding) VALUES (?, ?)",
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
        kind_filter: list[str] | None = None,
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
            kind_filter: Optional list of kind values (SC-1). Constrains the KNN
                candidate query itself via a `content_id IN (subquery)` restriction,
                so a filter on anything outside the unfiltered top-100 nearest
                neighbours still finds its matches -- filtering the top 100
                afterward (the original defect) would silently drop them instead.

        Returns:
            List of content dicts with relevance_score field

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            # First get top candidates by similarity from vec_content
            embedding_json = json.dumps(query_embedding)

            # SC-1: kind/source filters constrain the KNN candidate query itself, not
            # the top-100 pool after the fact. The candidate subquery is only added
            # when a filter is actually given, so the unfiltered path (today's
            # behaviour) is untouched.
            knn_query = """
                SELECT
                    content_id,
                    distance
                FROM vec_content
                WHERE embedding MATCH ?
            """
            knn_params: list[Any] = [embedding_json]

            if source_filter or kind_filter:
                candidate_query = (
                    "SELECT c.id FROM content c "
                    "LEFT JOIN sources s ON c.source_id = s.id WHERE 1=1"
                )
                if source_filter:
                    candidate_query += " AND LOWER(s.name) LIKE '%' || LOWER(?) || '%'"
                    knn_params.append(source_filter)
                candidate_query += self._kind_filter_sql(kind_filter, knn_params)
                knn_query += " AND content_id IN (" + candidate_query + ")"

            knn_query += " ORDER BY distance LIMIT 100"

            # Get top 100 candidates by similarity (we'll re-rank)
            cursor = self.conn.execute(knn_query, tuple(knn_params))

            candidates = cursor.fetchall()
            if not candidates:
                return []

            # Get content details for candidates. The kind/source filters already
            # narrowed the candidate set above, so this query needs no filter of
            # its own -- every content_id here already matched.
            content_ids = [row["content_id"] for row in candidates]

            # Build safe IN clause with parameterized placeholders
            # Note: placeholders is just "?,?,?" string, not user input
            placeholders = ",".join(["?"] * len(content_ids))
            where_sql = "WHERE c.id IN (" + placeholders + ")"
            params: list[Any] = list(content_ids)

            rows = self._query_joined_content(
                where_sql, params, omit=("interesting_override",)
            )

            # Build dict of content by id
            content_by_id = {row["id"]: row for row in rows}

            # Calculate weighted scores and re-rank
            scored: list[tuple[float, dict[str, Any]]] = []
            results: list[dict[str, Any]] = []
            for candidate in candidates:
                content_id = candidate["content_id"]
                if content_id not in content_by_id:
                    continue

                # Make a copy to avoid modifying original dict
                content = content_by_id[content_id].copy()

                # Convert L2 distance from vec_content to cosine similarity
                # (embeddings are unit-normalized by all-MiniLM-L6-v2's Normalize layer,
                # so cosine_sim = 1 - L2_dist^2 / 2 holds)
                similarity = 1.0 - (float(candidate["distance"]) ** 2 / 2)

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
                    scored.append((relevance_score, content))

            # Rank on the raw score; the 3-decimal rounding is only what is
            # reported, so two items it ties are still ordered by true relevance
            # (gh #14).
            scored.sort(key=itemgetter(0), reverse=True)
            for relevance_score, content in scored:
                content["relevance_score"] = round(relevance_score, 3)
                results.append(content)
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
            return self._query_joined_content(
                """WHERE c.id NOT IN (SELECT content_id FROM embeddings)
                   ORDER BY c.fetched_at DESC LIMIT ?""",
                (limit,),
                omit=("interesting_override", "user_feedback"),
            )

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
            return self._query_joined_content(
                """WHERE c.priority IS NULL
                     AND c.summary IS NULL
                     AND c.analysis IS NULL
                     AND c.archived_at IS NULL
                   ORDER BY c.fetched_at DESC LIMIT ?""",
                (limit,),
                omit=("interesting_override", "user_feedback"),
            )

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content without analysis: {e}") from e

    def get_content_needing_kind(
        self, limit: int = 100, since_days: int | None = None
    ) -> list[dict[str, Any]]:
        """Get already-analysed content items with no content kind yet (gh #83).

        A qualifying item carries a summary and an analysis JSON with no
        kind_confidence key at all -- json_type() (not json_extract(), which cannot
        distinguish an absent key from a key whose value is JSON null) is what tells
        "never classified" apart from "classified unclassified": a below-threshold or
        unparseable classifier answer still stores kind_confidence=null, and that item
        must never be reselected either. Newest fetched_at first, bounded to at most
        `limit`, and to items fetched within the last `since_days` days when given.

        Args:
            limit: Maximum number of items to return
            since_days: Optional -- only items fetched within the last N days

        Returns:
            List of content dicts needing kind classification

        Raises:
            sqlite3.Error: If database operation fails
        """
        try:
            where_sql = """WHERE c.archived_at IS NULL
                             AND c.summary IS NOT NULL
                             AND c.summary != ''
                             AND json_type(c.analysis, '$.kind_confidence') IS NULL"""
            params: list[Any] = []
            if since_days is not None:
                where_sql += " AND datetime(c.fetched_at) >= datetime('now', ?)"
                params.append(f"-{since_days} days")
            where_sql += " ORDER BY c.fetched_at DESC LIMIT ?"
            params.append(limit)

            return self._query_joined_content(
                where_sql, params, omit=("interesting_override", "user_feedback")
            )

        except sqlite3.Error as e:
            raise sqlite3.Error(
                f"Failed to get content needing kind classification: {e}"
            ) from e

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
            # Build parameters: archived_at first, then the datetime modifiers
            params: list[Any] = [utc_now_iso()]

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
                SET archived_at = ?
                WHERE archived_at IS NULL
                  AND favorited = 0
                  AND notes IS NULL
                  AND (
                    -- HIGH: Only read + N days (or never if high_read is None)
                    (priority = 'high' AND read = 1 AND datetime(fetched_at) < datetime('now', ?))
                    OR
                    -- MEDIUM: Unread N days OR read N days
                    (priority = 'medium' AND (
                      (read = 0 AND datetime(fetched_at) < datetime('now', ?))
                      OR (read = 1 AND datetime(fetched_at) < datetime('now', ?))
                    ))
                    OR
                    -- LOW: Unread N days OR read N days
                    (priority = 'low' AND (
                      (read = 0 AND datetime(fetched_at) < datetime('now', ?))
                      OR (read = 1 AND datetime(fetched_at) < datetime('now', ?))
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

    def get_feedback_statistics(self, since_days: int | None = None) -> dict[str, Any]:
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
            time_params: list[str] = []
            if since_days:
                time_params.append(f"-{since_days} days")

            # Overall totals
            totals_query = """
                SELECT
                    SUM(CASE WHEN user_feedback = 'up' THEN 1 ELSE 0 END) as upvotes,
                    SUM(CASE WHEN user_feedback = 'down' THEN 1 ELSE 0 END) as downvotes,
                    COUNT(CASE WHEN user_feedback IS NOT NULL THEN 1 END) as total_votes
                FROM content c
                WHERE user_feedback IS NOT NULL
            """
            if since_days:
                totals_query += " AND c.updated_at >= datetime('now', ?)"
            totals_cursor = self.conn.execute(totals_query, time_params)
            totals_row = totals_cursor.fetchone()

            # Per-source breakdown
            source_query = """
                SELECT
                    s.name,
                    s.id,
                    SUM(CASE WHEN c.user_feedback = 'up' THEN 1 ELSE 0 END) as upvotes,
                    SUM(CASE WHEN c.user_feedback = 'down' THEN 1 ELSE 0 END) as downvotes,
                    COUNT(CASE WHEN c.user_feedback IS NOT NULL THEN 1 END) as total
                FROM content c
                JOIN sources s ON c.source_id = s.id
                WHERE c.user_feedback IS NOT NULL
            """
            if since_days:
                source_query += " AND c.updated_at >= datetime('now', ?)"
            source_query += """
                GROUP BY s.id, s.name
                HAVING total > 0
                ORDER BY total DESC
            """
            source_cursor = self.conn.execute(source_query, time_params)

            by_source = []
            for row in source_cursor.fetchall():
                total = row[4]
                upvotes = row[2]
                downvotes = row[3]
                ratio = upvotes / total if total > 0 else 0.0
                by_source.append(
                    {
                        "source_name": row[0],
                        "source_id": row[1],
                        "upvotes": upvotes,
                        "downvotes": downvotes,
                        "total": total,
                        "upvote_ratio": round(ratio, 2),
                    }
                )

            # Extract topics from upvoted content (from analysis.matched_interests)
            upvoted_query = """
                SELECT c.analysis, c.title
                FROM content c
                WHERE c.user_feedback = 'up'
            """
            if since_days:
                upvoted_query += " AND c.updated_at >= datetime('now', ?)"
            upvoted_query += " AND c.analysis IS NOT NULL"
            upvoted_cursor = self.conn.execute(upvoted_query, time_params)

            topics_upvoted: dict[str, int] = {}
            for row in upvoted_cursor.fetchall():
                try:
                    analysis = json.loads(row[0]) if row[0] else {}
                    interests = analysis.get("matched_interests", [])
                    for interest in interests:
                        if interest:
                            topics_upvoted[interest] = (
                                topics_upvoted.get(interest, 0) + 1
                            )
                except (json.JSONDecodeError, TypeError):
                    pass

            # Extract topics from downvoted content
            downvoted_query = """
                SELECT c.analysis, c.title
                FROM content c
                WHERE c.user_feedback = 'down'
            """
            if since_days:
                downvoted_query += " AND c.updated_at >= datetime('now', ?)"
            downvoted_query += " AND c.analysis IS NOT NULL"
            downvoted_cursor = self.conn.execute(downvoted_query, time_params)

            topics_downvoted: dict[str, int] = {}
            for row in downvoted_cursor.fetchall():
                try:
                    analysis = json.loads(row[0]) if row[0] else {}
                    interests = analysis.get("matched_interests", [])
                    for interest in interests:
                        if interest:
                            topics_downvoted[interest] = (
                                topics_downvoted.get(interest, 0) + 1
                            )
                except (json.JSONDecodeError, TypeError):
                    pass

            # Sort topics by frequency
            sorted_upvoted = sorted(
                topics_upvoted.items(), key=lambda x: x[1], reverse=True
            )
            sorted_downvoted = sorted(
                topics_downvoted.items(), key=lambda x: x[1], reverse=True
            )

            # Build LLM context summary (for 003)
            llm_context_parts = []
            if sorted_upvoted:
                top_liked = [t[0] for t in sorted_upvoted[:5]]
                llm_context_parts.append(f"User prefers: {', '.join(top_liked)}")
            if sorted_downvoted:
                top_disliked = [t[0] for t in sorted_downvoted[:5]]
                llm_context_parts.append(f"User dislikes: {', '.join(top_disliked)}")

            # Add source preferences
            trusted_sources = [
                s["source_name"]
                for s in by_source
                if s["upvote_ratio"] >= 0.7 and s["total"] >= 2
            ]
            untrusted_sources = [
                s["source_name"]
                for s in by_source
                if s["upvote_ratio"] <= 0.3 and s["total"] >= 2
            ]
            if trusted_sources:
                llm_context_parts.append(
                    f"Trusted sources: {', '.join(trusted_sources[:3])}"
                )
            if untrusted_sources:
                llm_context_parts.append(
                    f"Less trusted sources: {', '.join(untrusted_sources[:3])}"
                )

            return {
                "totals": {
                    "upvotes": totals_row[0] or 0,
                    "downvotes": totals_row[1] or 0,
                    "total_votes": totals_row[2] or 0,
                },
                "by_source": by_source,
                "topics_upvoted": [
                    {"topic": t[0], "count": t[1]} for t in sorted_upvoted
                ],
                "topics_downvoted": [
                    {"topic": t[0], "count": t[1]} for t in sorted_downvoted
                ],
                "for_llm_context": " | ".join(llm_context_parts)
                if llm_context_parts
                else None,
                "time_period": f"last {since_days} days" if since_days else "all time",
            }

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get feedback statistics: {e}") from e

    def get_content_by_feedback(
        self, feedback: str, since_days: int | None = None
    ) -> list[dict[str, Any]]:
        """Get content items with a specific feedback vote.

        Args:
            feedback: Feedback value to filter by ("up" or "down")
            since_days: Optional limit to items updated within N days

        Returns:
            List of content items with the specified feedback

        Raises:
            ValueError: If feedback value is invalid
            sqlite3.Error: If database operation fails
        """
        if feedback not in ("up", "down"):
            raise ValueError(f"Invalid feedback value: {feedback}")

        try:
            query = """
                SELECT 
                    c.id, c.title, c.url, c.summary, c.content, c.priority,
                    c.analysis, c.published_at, c.read, c.favorited,
                    c.user_feedback, s.name as source_name, s.type as source_type
                FROM content c
                LEFT JOIN sources s ON c.source_id = s.id
                WHERE c.user_feedback = ?
            """
            params: list = [feedback]

            if since_days:
                query += " AND c.updated_at >= datetime('now', ?)"
                params.append(f"-{since_days} days")

            query += " ORDER BY c.updated_at DESC"

            cursor = self.conn.execute(query, params)

            items = []
            for row in cursor.fetchall():
                items.append(
                    {
                        "id": row[0],
                        "title": row[1],
                        "url": row[2],
                        "summary": row[3],
                        "content": row[4],
                        "priority": row[5],
                        "analysis": row[6],
                        "published_at": row[7],
                        "read": bool(row[8]),
                        "favorited": bool(row[9]),
                        "user_feedback": row[10],
                        "source_name": row[11],
                        "source_type": row[12],
                    }
                )

            return items

        except sqlite3.Error as e:
            raise sqlite3.Error(f"Failed to get content by feedback: {e}") from e
