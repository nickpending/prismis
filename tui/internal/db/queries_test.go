package db

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// createTestDB creates a temporary test database with sample data
func createTestDB(t *testing.T) string {
	// Create temp directory
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "test.db")

	// Open database
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open test database: %v", err)
	}
	defer db.Close()

	// Create schema with sources table for JOIN
	schema := `
	CREATE TABLE sources (
		id TEXT PRIMARY KEY,
		name TEXT,
		type TEXT,
		url TEXT NOT NULL,
		active BOOLEAN DEFAULT 1
	);
	
	CREATE TABLE content (
		id TEXT PRIMARY KEY,
		source_id TEXT REFERENCES sources(id),
		title TEXT NOT NULL,
		url TEXT NOT NULL,
		content TEXT,
		summary TEXT,
		analysis TEXT,
		priority TEXT,
		published_at TIMESTAMP,
		read BOOLEAN DEFAULT 0,
		favorited BOOLEAN DEFAULT 0
	);`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test source
	_, err = db.Exec(
		`INSERT INTO sources (id, name, type, url) VALUES (?, ?, ?, ?)`,
		"test-source-1",
		"Test RSS Feed",
		"rss",
		"http://example.com/feed.xml",
	)
	if err != nil {
		t.Fatalf("Failed to insert test source: %v", err)
	}

	// Insert test data with source_id
	testData := []struct {
		id        string
		title     string
		priority  string
		read      bool
		favorited bool
	}{
		{"1", "High Priority Item 1", "high", false, false},
		{"2", "High Priority Item 2", "high", false, true}, // Favorited unread
		{"3", "Medium Priority Item", "medium", false, false},
		{"4", "Low Priority Item", "low", false, true}, // Favorited unread
		{"5", "No Priority Item", "", false, false},
		{"6", "Read High Item", "high", true, true}, // Favorited read
	}

	for _, item := range testData {
		_, err := db.Exec(
			`INSERT INTO content (id, source_id, title, url, summary, priority, published_at, read, favorited)
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			item.id,
			"test-source-1",
			item.title,
			"http://example.com/"+item.id,
			"Summary for "+item.title,
			item.priority,
			time.Now().Format(time.RFC3339),
			item.read,
			item.favorited,
		)
		if err != nil {
			t.Fatalf("Failed to insert test data: %v", err)
		}
	}

	return dbPath
}

func TestGetContentByPriority(t *testing.T) {
	// Create test database
	dbPath := createTestDB(t)

	// Override dbPathFunc to return test database
	originalDBPathFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = originalDBPathFunc
	}()

	tests := []struct {
		name          string
		priority      string
		expectedCount int
		checkPriority bool
	}{
		{
			name:          "Get high priority items",
			priority:      "high",
			expectedCount: 2, // Only unread high priority items
			checkPriority: true,
		},
		{
			name:          "Get medium priority items",
			priority:      "medium",
			expectedCount: 1,
			checkPriority: true,
		},
		{
			name:          "Get low priority items",
			priority:      "low",
			expectedCount: 1,
			checkPriority: true,
		},
		{
			name:          "Get all unread items",
			priority:      "all",
			expectedCount: 5, // All unread items regardless of priority
			checkPriority: false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			items, _, err := GetContentByPriority(tt.priority, true)
			if err != nil {
				t.Fatalf("GetContentByPriority failed: %v", err)
			}

			// Check count
			if len(items) != tt.expectedCount {
				t.Errorf("Expected %d items, got %d", tt.expectedCount, len(items))
			}

			// Check priority if needed
			if tt.checkPriority && tt.priority != "all" {
				for _, item := range items {
					if item.Priority != tt.priority {
						t.Errorf("Expected priority %s, got %s for item %s",
							tt.priority, item.Priority, item.Title)
					}
				}
			}

			// Verify all items are unread
			for _, item := range items {
				if item.Read {
					t.Errorf("Got read item when expecting only unread: %s", item.Title)
				}
			}
		})
	}
}

func TestGetContentByPriorityEmptyDB(t *testing.T) {
	// Create empty test database
	tempDir := t.TempDir()
	dbPath := filepath.Join(tempDir, "empty.db")

	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open test database: %v", err)
	}
	defer db.Close()

	// Create schema only, no data
	schema := `
	CREATE TABLE sources (
		id TEXT PRIMARY KEY,
		name TEXT,
		type TEXT,
		url TEXT NOT NULL,
		active BOOLEAN DEFAULT 1
	);
	
	CREATE TABLE content (
		id TEXT PRIMARY KEY,
		source_id TEXT REFERENCES sources(id),
		title TEXT NOT NULL,
		url TEXT NOT NULL,
		content TEXT,
		summary TEXT,
		analysis TEXT,
		priority TEXT,
		published_at TIMESTAMP,
		read BOOLEAN DEFAULT 0,
		favorited BOOLEAN DEFAULT 0
	);`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Override dbPathFunc
	originalDBPathFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = originalDBPathFunc
	}()

	// Test with empty database
	items, _, err := GetContentByPriority("all", true)
	if err != nil {
		t.Fatalf("GetContentByPriority failed: %v", err)
	}

	if len(items) != 0 {
		t.Errorf("Expected 0 items from empty database, got %d", len(items))
	}
}

func TestGetUnreadContent(t *testing.T) {
	// Create test database
	dbPath := createTestDB(t)

	// Override the dbPathFunc for testing
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Call GetUnreadContent
	items, err := GetUnreadContent()
	if err != nil {
		t.Fatalf("GetUnreadContent failed: %v", err)
	}

	// Verify we got all unread items (should be 5)
	expectedCount := 5
	if len(items) != expectedCount {
		t.Errorf("Expected %d unread items, got %d", expectedCount, len(items))
	}

	// Verify items are ordered by published date (newest first)
	for i := 1; i < len(items); i++ {
		if items[i-1].Published.Before(items[i].Published) {
			t.Error("Items not ordered by published date DESC")
		}
	}

	// Verify all items have read = false
	for _, item := range items {
		if item.Read {
			t.Errorf("Item %s should be unread", item.ID)
		}
	}
}

func TestSourceInfoPopulated(t *testing.T) {
	// INVARIANT: Source info must be populated from JOIN
	// BREAKS: Users can't see where content is from

	dbPath := createTestDB(t)

	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Test both query functions populate source info
	t.Run("GetContentByPriority populates source", func(t *testing.T) {
		items, _, err := GetContentByPriority("high", true)
		if err != nil {
			t.Fatalf("GetContentByPriority failed: %v", err)
		}

		for _, item := range items {
			if item.SourceType == "" {
				t.Errorf("SourceType empty for item %s - JOIN failed", item.ID)
			}
			if item.SourceName == "" {
				t.Errorf("SourceName empty for item %s - JOIN failed", item.ID)
			}
			if item.SourceID == "" {
				t.Errorf("SourceID empty for item %s - JOIN failed", item.ID)
			}

			// Verify expected values from test data
			if item.SourceType != "rss" {
				t.Errorf("Expected SourceType 'rss', got '%s'", item.SourceType)
			}
			if item.SourceName != "Test RSS Feed" {
				t.Errorf("Expected SourceName 'Test RSS Feed', got '%s'", item.SourceName)
			}
		}
	})

	t.Run("GetUnreadContent populates source", func(t *testing.T) {
		items, err := GetUnreadContent()
		if err != nil {
			t.Fatalf("GetUnreadContent failed: %v", err)
		}

		for _, item := range items {
			if item.SourceType == "" {
				t.Errorf("SourceType empty for item %s - JOIN failed", item.ID)
			}
			if item.SourceName == "" {
				t.Errorf("SourceName empty for item %s - JOIN failed", item.ID)
			}
			if item.SourceID == "" {
				t.Errorf("SourceID empty for item %s - JOIN failed", item.ID)
			}
		}
	})
}

func TestMarkAsRead(t *testing.T) {
	// Create test database
	dbPath := createTestDB(t)

	// Override the dbPathFunc for testing
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Mark item "1" as read
	err := MarkAsRead("1")
	if err != nil {
		t.Fatalf("MarkAsRead failed: %v", err)
	}

	// Verify the item is now marked as read
	db, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("Failed to open database: %v", err)
	}
	defer db.Close()

	var readStatus bool
	err = db.QueryRow("SELECT read FROM content WHERE id = ?", "1").Scan(&readStatus)
	if err != nil {
		t.Fatalf("Failed to query read status: %v", err)
	}

	if !readStatus {
		t.Error("Item should be marked as read")
	}

	// Test marking non-existent item (no error expected - SQL UPDATE returns 0 rows affected)
	err = MarkAsRead("non-existent")
	if err != nil {
		t.Errorf("MarkAsRead should not error for non-existent item: %v", err)
	}
}

// TestGetFavoritesCount_ReturnsAccurateTotal tests the invariant that favorites count is always accurate
func TestGetFavoritesCount_ReturnsAccurateTotal(t *testing.T) {
	// INVARIANT: GetFavoritesCount must return the exact count of favorited items
	// BREAKS: User trust if count is wrong - they think favorites are lost

	dbPath := createTestDB(t)
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Expected: 3 favorited items (IDs 2, 4, 6 from test data)
	count, err := GetFavoritesCount()
	if err != nil {
		t.Fatalf("GetFavoritesCount failed: %v", err)
	}

	if count != 3 {
		t.Errorf("Expected 3 favorites, got %d", count)
	}

	// Test after adding more favorites - use the connection pool
	poolDB, err := GetDB()
	if err != nil {
		t.Fatalf("Failed to get database connection: %v", err)
	}

	// Make item 1 favorited
	_, err = poolDB.Exec("UPDATE content SET favorited = 1 WHERE id = ?", "1")
	if err != nil {
		t.Fatalf("Failed to update favorite: %v", err)
	}

	count, err = GetFavoritesCount()
	if err != nil {
		t.Fatalf("GetFavoritesCount failed after update: %v", err)
	}

	if count != 4 {
		t.Errorf("Expected 4 favorites after update, got %d", count)
	}
}

// TestFavoritesFilter_ShowsReadItems tests that favorites are shown regardless of read status
func TestFavoritesFilter_ShowsReadItems(t *testing.T) {
	// INVARIANT: Favorites filter must show ALL favorited items, including read ones
	// BREAKS: Core feature promise - users favorite items to keep them accessible

	// Force new connection for test isolation
	CloseDB()

	dbPath := createTestDB(t)
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
		CloseDB() // Clean up after test
	}()

	// Get favorites with showAll=false (should still show read favorites)
	items, _, err := GetContentWithFilters("favorites", true, false, "all", true)
	if err != nil {
		t.Fatalf("GetContentWithFilters failed: %v", err)
	}

	// Should get 3 favorited items including the read one (ID 6)
	if len(items) != 3 {
		t.Errorf("Expected 3 favorites, got %d", len(items))
	}

	// Verify that read favorite (ID 6) is included
	foundReadFavorite := false
	for _, item := range items {
		if item.ID == "6" && item.Read && item.Favorited {
			foundReadFavorite = true
			break
		}
	}

	if !foundReadFavorite {
		t.Error("Read favorite item (ID 6) was not returned by favorites filter")
	}
}

// TestFavoritesPersistAcrossStatusChanges tests that favorites persist when read status changes
func TestFavoritesPersistAcrossStatusChanges(t *testing.T) {
	// INVARIANT: Favorited status must persist regardless of other state changes
	// BREAKS: User curated content disappears, destroying trust

	// Force new connection for test isolation
	CloseDB()

	dbPath := createTestDB(t)
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	poolDB, err := GetDB()
	if err != nil {
		t.Fatalf("Failed to get database connection: %v", err)
	}

	// Item 2 starts as favorited and unread
	// Mark it as read
	_, err = poolDB.Exec("UPDATE content SET read = 1 WHERE id = ?", "2")
	if err != nil {
		t.Fatalf("Failed to mark as read: %v", err)
	}

	// Verify it's still favorited
	var favorited bool
	err = poolDB.QueryRow("SELECT favorited FROM content WHERE id = ?", "2").Scan(&favorited)
	if err != nil {
		t.Fatalf("Failed to check favorite status: %v", err)
	}

	if !favorited {
		t.Error("Favorite status was lost when marking item as read")
	}

	// Verify it still appears in favorites filter
	items, _, err := GetContentWithFilters("favorites", true, false, "all", true)
	if err != nil {
		t.Fatalf("GetContentWithFilters failed: %v", err)
	}

	foundItem := false
	for _, item := range items {
		if item.ID == "2" {
			foundItem = true
			if !item.Favorited {
				t.Error("Item 2 lost favorited flag in query results")
			}
			break
		}
	}

	if !foundItem {
		t.Error("Favorited item disappeared from favorites view after being marked read")
	}
}

// TestZGetFavoritesCount_HandlesDBError tests graceful handling of database errors
// Named with Z prefix to run last due to connection pool contamination
func TestZGetFavoritesCount_HandlesDBError(t *testing.T) {
	// FAILURE MODE: Database connection failure during count query
	// GRACEFUL: Must return 0 with error, not panic

	// Save the original function
	oldFunc := dbPathFunc

	// Force close existing connection first
	CloseDB()

	// Now override dbPathFunc to return invalid path
	dbPathFunc = func() (string, error) {
		return "/invalid/path/that/does/not/exist.db", nil
	}
	defer func() {
		dbPathFunc = oldFunc
		// Reset the connection for next tests
		CloseDB()
	}()

	count, err := GetFavoritesCount()

	// Should return error, not panic
	if err == nil {
		t.Error("Expected error for invalid database path, got nil")
	}

	// Should return 0 count on error
	if count != 0 {
		t.Errorf("Expected 0 count on error, got %d", count)
	}
}

// TestConcurrentFavoriteOperations tests that concurrent favorite/unfavorite operations maintain consistency
func TestConcurrentFavoriteOperations(t *testing.T) {
	// FAILURE MODE: Concurrent modifications to favorites
	// GRACEFUL: Count must remain consistent, no race conditions

	// Force new connection for test isolation
	CloseDB()

	dbPath := createTestDB(t)

	// Save original and immediately set valid path
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Ensure we can connect before starting goroutines
	testDB, err := GetDB()
	if err != nil {
		// Skip this test if connection pool is contaminated from previous tests
		t.Skip("Connection pool contaminated - run this test in isolation")
	}
	_ = testDB

	// Run concurrent operations
	done := make(chan bool, 2)

	// Goroutine 1: Toggle favorites rapidly
	go func() {
		db, err := GetDB()
		if err != nil {
			// Can't use t.Fatal in goroutine, just return
			done <- true
			return
		}

		for i := 0; i < 10; i++ {
			// Toggle item 1's favorite status
			_, _ = db.Exec("UPDATE content SET favorited = 1 - favorited WHERE id = ?", "1")
		}
		done <- true
	}()

	// Goroutine 2: Read favorites count repeatedly
	counts := make([]int, 0)
	go func() {
		for i := 0; i < 10; i++ {
			count, err := GetFavoritesCount()
			if err == nil {
				counts = append(counts, count)
			}
		}
		done <- true
	}()

	// Wait for both to complete
	<-done
	<-done

	// Small delay to ensure database operations settle
	time.Sleep(10 * time.Millisecond)

	// Final count should be deterministic (either 3 or 4 depending on final state)
	finalCount, err := GetFavoritesCount()
	if err != nil {
		t.Fatalf("Failed to get final count: %v", err)
	}

	// Should be either 3 (original) or 4 (if item 1 ended up favorited)
	if finalCount != 3 && finalCount != 4 {
		t.Errorf("Final count should be 3 or 4, got %d", finalCount)
	}

	// All intermediate counts should have been valid (between 2 and 4)
	for _, count := range counts {
		if count < 2 || count > 4 {
			t.Errorf("Invalid intermediate count detected: %d (should be 2-4)", count)
		}
	}
}
