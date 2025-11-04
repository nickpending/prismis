package db

import (
	"database/sql"
	"fmt"
	"os"
	"path/filepath"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// ContentItem represents a content item from the database
type ContentItem struct {
	ID         string
	Title      string
	URL        string
	Summary    string
	Priority   string
	Content    string
	Analysis   string // JSON field containing reading_summary, alpha_insights, patterns, entities
	Published  time.Time
	Read       bool
	Favorited  bool   // Whether item is favorited
	SourceType string // "rss", "reddit", "youtube", "file"
	SourceName string // Source name (e.g., "SimonW Blog", "r/rust", "3Blue1Brown")
	SourceID   string // Source UUID for updates
}

// queryContent is a unified helper function for querying content with filters
func queryContent(priorityFilter string, readFilter *bool) ([]ContentItem, error) {
	return queryContentWithFilter(priorityFilter, readFilter, true)
}

// queryContentWithFilter is the extended version that handles unprioritized filtering
func queryContentWithFilter(priorityFilter string, readFilter *bool, showUnprioritized bool) ([]ContentItem, error) {
	// Use singleton connection pool for efficiency
	db, err := GetDB()
	if err != nil {
		return nil, fmt.Errorf("failed to get database connection: %w", err)
	}
	// Note: Don't close the pool connection - it's managed globally

	// Build query with proper JOIN to get source info
	query := `SELECT c.id, c.title, c.url, c.summary, c.priority, c.content, c.analysis, 
	                 c.published_at, c.read, c.favorited, s.type, s.name, c.source_id
	          FROM content c
	          JOIN sources s ON c.source_id = s.id
	          WHERE 1=1`

	var args []interface{}

	// Add read filter if specified
	if readFilter != nil {
		if *readFilter {
			query += " AND c.read = 1"
		} else {
			query += " AND c.read = 0"
		}
	}

	// Add priority filter if specified and not "all"
	if priorityFilter != "" && priorityFilter != "all" {
		query += " AND c.priority = ?"
		args = append(args, priorityFilter)
	}

	// Filter out unprioritized content if requested
	if !showUnprioritized {
		query += " AND c.priority IS NOT NULL AND c.priority != ''"
	}

	query += " ORDER BY c.published_at DESC"

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, fmt.Errorf("failed to query content: %w", err)
	}
	defer rows.Close()

	var items []ContentItem
	for rows.Next() {
		var item ContentItem
		var publishedStr sql.NullString
		var priority sql.NullString
		var summary sql.NullString
		var content sql.NullString
		var analysis sql.NullString
		var sourceType sql.NullString
		var sourceName sql.NullString

		err := rows.Scan(
			&item.ID,
			&item.Title,
			&item.URL,
			&summary,
			&priority,
			&content,
			&analysis,
			&publishedStr,
			&item.Read,
			&item.Favorited,
			&sourceType,
			&sourceName,
			&item.SourceID,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan row: %w", err)
		}

		// Handle nullable fields
		if priority.Valid {
			item.Priority = priority.String
		}
		if summary.Valid {
			item.Summary = summary.String
		}
		if content.Valid {
			item.Content = content.String
		}
		if analysis.Valid {
			item.Analysis = analysis.String
		}
		if sourceType.Valid {
			item.SourceType = sourceType.String
		}
		if sourceName.Valid {
			item.SourceName = sourceName.String
		}

		// Parse published timestamp
		if publishedStr.Valid {
			if parsed, err := time.Parse(time.RFC3339, publishedStr.String); err == nil {
				item.Published = parsed
			}
		}

		items = append(items, item)
	}

	if err = rows.Err(); err != nil {
		return nil, fmt.Errorf("error iterating rows: %w", err)
	}

	return items, nil
}

// GetContentByPriority fetches content items filtered by priority
// When showUnprioritized is false, items with NULL or empty priority are filtered out
// Returns items, count of hidden unprioritized items, and error
func GetContentByPriority(priority string, showUnprioritized bool) ([]ContentItem, int, error) {
	unread := false
	items, err := queryContentWithFilter(priority, &unread, showUnprioritized)
	if err != nil {
		return nil, 0, err
	}

	// Get count of hidden unprioritized items if filtering is active
	var hiddenCount int
	if !showUnprioritized {
		hiddenCount, err = getUnprioritizedCount()
		if err != nil {
			// Don't fail the whole operation if we can't get the count
			hiddenCount = 0
		}
	}

	return items, hiddenCount, nil
}

// GetContentWithFilters fetches content with all filter options applied
func GetContentWithFilters(priority string, showUnprioritized bool, showAll bool, showArchived bool, filterType string, sortNewest bool) ([]ContentItem, int, error) {
	// Use singleton connection pool for efficiency
	db, err := GetDB()
	if err != nil {
		return nil, 0, fmt.Errorf("failed to get database connection: %w", err)
	}

	// Build query with proper JOIN to get source info
	query := `SELECT c.id, c.title, c.url, c.summary, c.priority, c.content, c.analysis,
	                 c.published_at, c.read, c.favorited, s.type, s.name, c.source_id
	          FROM content c
	          JOIN sources s ON c.source_id = s.id
	          WHERE 1=1`

	var args []interface{}

	// Add archived filter (default excludes archived)
	if showArchived {
		// Show only archived items
		query += " AND c.archived_at IS NOT NULL"
	} else {
		// Default: exclude archived items
		query += " AND c.archived_at IS NULL"
	}

	// Add read filter based on showAll flag (but skip for favorites)
	if !showAll && priority != "favorites" {
		// Show unread only (except for favorites which should always show)
		query += " AND c.read = 0"
	}

	// Add priority filter if specified and not "all"
	if priority == "favorites" {
		// Special case for favorites - show only favorited items (regardless of read status)
		query += " AND c.favorited = 1"
	} else if priority == "unprioritized" {
		// Special case for unprioritized - show only items with NULL or empty priority
		query += " AND (c.priority IS NULL OR c.priority = '')"
	} else if priority != "" && priority != "all" {
		query += " AND c.priority = ?"
		args = append(args, priority)
	}

	// Filter out unprioritized content if requested
	if !showUnprioritized {
		query += " AND c.priority IS NOT NULL AND c.priority != ''"
	}

	// Add source type filter if not "all"
	if filterType != "" && filterType != "all" {
		query += " AND s.type = ?"
		args = append(args, filterType)
	}

	// Add sort order
	if sortNewest {
		query += " ORDER BY c.published_at DESC"
	} else {
		query += " ORDER BY c.published_at ASC"
	}

	rows, err := db.Query(query, args...)
	if err != nil {
		return nil, 0, fmt.Errorf("failed to query content: %w", err)
	}
	defer rows.Close()

	var items []ContentItem
	for rows.Next() {
		var item ContentItem
		var publishedStr sql.NullString
		var priority sql.NullString
		var summary sql.NullString
		var content sql.NullString
		var analysis sql.NullString
		var sourceType sql.NullString
		var sourceName sql.NullString

		err := rows.Scan(
			&item.ID,
			&item.Title,
			&item.URL,
			&summary,
			&priority,
			&content,
			&analysis,
			&publishedStr,
			&item.Read,
			&item.Favorited,
			&sourceType,
			&sourceName,
			&item.SourceID,
		)
		if err != nil {
			return nil, 0, fmt.Errorf("failed to scan row: %w", err)
		}

		// Handle nullable fields
		if priority.Valid {
			item.Priority = priority.String
		}
		if summary.Valid {
			item.Summary = summary.String
		}
		if content.Valid {
			item.Content = content.String
		}
		if analysis.Valid {
			item.Analysis = analysis.String
		}
		if sourceType.Valid {
			item.SourceType = sourceType.String
		}
		if sourceName.Valid {
			item.SourceName = sourceName.String
		}

		// Parse published timestamp
		if publishedStr.Valid {
			if parsed, err := time.Parse(time.RFC3339, publishedStr.String); err == nil {
				item.Published = parsed
			}
		}

		items = append(items, item)
	}

	if err = rows.Err(); err != nil {
		return nil, 0, fmt.Errorf("error iterating rows: %w", err)
	}

	// Get count of hidden unprioritized items if filtering is active
	var hiddenCount int
	if !showUnprioritized {
		hiddenCount, err = getUnprioritizedCount()
		if err != nil {
			// Don't fail the whole operation if we can't get the count
			hiddenCount = 0
		}
	}

	return items, hiddenCount, nil
}

// dbPathFunc is a variable holding the function to get DB path (for testing)
var dbPathFunc = getDefaultDBPath

// getDefaultDBPath returns the default path to the SQLite database
func getDefaultDBPath() (string, error) {
	// Use XDG_DATA_HOME for database storage (XDG Base Directory spec)
	xdgDataHome := os.Getenv("XDG_DATA_HOME")
	if xdgDataHome == "" {
		homeDir, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("failed to get home directory: %w", err)
		}
		xdgDataHome = filepath.Join(homeDir, ".local", "share")
	}

	dbPath := filepath.Join(xdgDataHome, "prismis", "prismis.db")
	return dbPath, nil
}

// Source represents a content source
type Source struct {
	ID          string
	URL         string
	Name        string
	Type        string // "rss", "reddit", "youtube", "file"
	Active      bool
	UnreadCount int
	LastFetched *time.Time // When this source was last fetched
	ErrorCount  int        // Number of errors
}

// GetSourcesWithCounts fetches all sources with their unread item counts
func GetSourcesWithCounts() ([]Source, error) {
	// Use singleton connection pool for efficiency
	db, err := GetDB()
	if err != nil {
		return nil, fmt.Errorf("failed to get database connection: %w", err)
	}
	// Note: Don't close the pool connection - it's managed globally

	query := `
		SELECT 
			s.id,
			s.url,
			s.name,
			s.type,
			s.active,
			COUNT(CASE WHEN c.read = 0 THEN 1 END) as unread_count,
			s.last_fetched_at,
			s.error_count
		FROM sources s
		LEFT JOIN content c ON s.id = c.source_id
		GROUP BY s.id, s.url, s.name, s.type, s.active, s.last_fetched_at, s.error_count
		ORDER BY s.type, s.name
	`

	rows, err := db.Query(query)
	if err != nil {
		return nil, fmt.Errorf("failed to query sources: %w", err)
	}
	defer rows.Close()

	var sources []Source
	for rows.Next() {
		var source Source
		var name sql.NullString
		var lastFetchedStr sql.NullString
		var errorCount sql.NullInt64

		err := rows.Scan(
			&source.ID,
			&source.URL,
			&name,
			&source.Type,
			&source.Active,
			&source.UnreadCount,
			&lastFetchedStr,
			&errorCount,
		)
		if err != nil {
			return nil, fmt.Errorf("failed to scan row: %w", err)
		}

		if name.Valid {
			source.Name = name.String
		}

		if lastFetchedStr.Valid {
			if parsed, err := time.Parse(time.RFC3339, lastFetchedStr.String); err == nil {
				source.LastFetched = &parsed
			}
		}

		if errorCount.Valid {
			source.ErrorCount = int(errorCount.Int64)
		}

		sources = append(sources, source)
	}

	return sources, nil
}

// getDBPath returns the path to the SQLite database
func getDBPath() (string, error) {
	return dbPathFunc()
}

// GetUnreadContent fetches all unread content items regardless of priority
func GetUnreadContent() ([]ContentItem, error) {
	unread := false
	return queryContent("all", &unread)
}

// GetUnprioritizedContent fetches only items with NULL or empty priority
func GetUnprioritizedContent(showAll bool) ([]ContentItem, int, error) {
	// Use singleton connection pool for efficiency
	db, err := GetDB()
	if err != nil {
		return nil, 0, fmt.Errorf("failed to get database connection: %w", err)
	}

	// Build query for items with NULL or empty priority
	query := `SELECT c.id, c.title, c.url, c.summary, c.priority, c.content, c.analysis, 
	                 c.published_at, c.read, c.favorited, s.type, s.name, c.source_id
	          FROM content c
	          JOIN sources s ON c.source_id = s.id
	          WHERE (c.priority IS NULL OR c.priority = '')`

	// Add read filter based on showAll flag
	if !showAll {
		// Show unread only
		query += " AND c.read = 0"
	}

	query += " ORDER BY c.published_at DESC"

	rows, err := db.Query(query)
	if err != nil {
		return nil, 0, fmt.Errorf("failed to query unprioritized content: %w", err)
	}
	defer rows.Close()

	var items []ContentItem
	for rows.Next() {
		var item ContentItem
		var publishedStr sql.NullString
		var priority sql.NullString
		var summary sql.NullString
		var content sql.NullString
		var analysis sql.NullString
		var sourceType sql.NullString
		var sourceName sql.NullString

		err := rows.Scan(
			&item.ID,
			&item.Title,
			&item.URL,
			&summary,
			&priority,
			&content,
			&analysis,
			&publishedStr,
			&item.Read,
			&item.Favorited,
			&sourceType,
			&sourceName,
			&item.SourceID,
		)
		if err != nil {
			return nil, 0, fmt.Errorf("failed to scan row: %w", err)
		}

		// Handle nullable fields
		if priority.Valid {
			item.Priority = priority.String
		}
		if summary.Valid {
			item.Summary = summary.String
		}
		if content.Valid {
			item.Content = content.String
		}
		if analysis.Valid {
			item.Analysis = analysis.String
		}
		if sourceType.Valid {
			item.SourceType = sourceType.String
		}
		if sourceName.Valid {
			item.SourceName = sourceName.String
		}

		// Parse published timestamp
		if publishedStr.Valid {
			if parsed, err := time.Parse(time.RFC3339, publishedStr.String); err == nil {
				item.Published = parsed
			}
		}

		items = append(items, item)
	}

	if err = rows.Err(); err != nil {
		return nil, 0, fmt.Errorf("error iterating rows: %w", err)
	}

	// No hidden count for unprioritized view
	return items, 0, nil
}

// getUnprioritizedCount returns the count of unread items with NULL or empty priority
func getUnprioritizedCount() (int, error) {
	db, err := GetDB()
	if err != nil {
		return 0, fmt.Errorf("failed to get database connection: %w", err)
	}

	var count int
	err = db.QueryRow(`
		SELECT COUNT(*) FROM content
		WHERE read = 0
		AND (priority IS NULL OR priority = '')
	`).Scan(&count)

	if err != nil {
		return 0, fmt.Errorf("failed to count unprioritized items: %w", err)
	}

	return count, nil
}

// GetArchivedCount returns the count of archived items
func GetArchivedCount() (int, error) {
	db, err := GetDB()
	if err != nil {
		return 0, fmt.Errorf("failed to get database connection: %w", err)
	}

	var count int
	err = db.QueryRow(`
		SELECT COUNT(*) FROM content
		WHERE archived_at IS NOT NULL
	`).Scan(&count)

	if err != nil {
		return 0, fmt.Errorf("failed to count archived items: %w", err)
	}

	return count, nil
}

// GetFavoritesCount returns the count of favorited items
func GetFavoritesCount() (int, error) {
	db, err := GetDB()
	if err != nil {
		return 0, fmt.Errorf("failed to get database connection: %w", err)
	}

	var count int
	err = db.QueryRow(`
		SELECT COUNT(*) FROM content
		WHERE favorited = 1
	`).Scan(&count)

	if err != nil {
		return 0, fmt.Errorf("failed to count favorited items: %w", err)
	}

	return count, nil
}

// MarkAsRead marks a content item as read in the database
func MarkAsRead(contentID string) error {
	db, err := GetDB()
	if err != nil {
		return fmt.Errorf("failed to get database connection: %w", err)
	}

	_, err = db.Exec("UPDATE content SET read = 1 WHERE id = ?", contentID)
	if err != nil {
		return fmt.Errorf("failed to mark as read: %w", err)
	}

	return nil
}
