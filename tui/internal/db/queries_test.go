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
		read BOOLEAN DEFAULT 0
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
		id       string
		title    string
		priority string
		read     bool
	}{
		{"1", "High Priority Item 1", "high", false},
		{"2", "High Priority Item 2", "high", false},
		{"3", "Medium Priority Item", "medium", false},
		{"4", "Low Priority Item", "low", false},
		{"5", "No Priority Item", "", false},
		{"6", "Read High Item", "high", true},
	}

	for _, item := range testData {
		_, err := db.Exec(
			`INSERT INTO content (id, source_id, title, url, summary, priority, published_at, read) 
			 VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
			item.id,
			"test-source-1",
			item.title,
			"http://example.com/"+item.id,
			"Summary for "+item.title,
			item.priority,
			time.Now().Format(time.RFC3339),
			item.read,
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
		read BOOLEAN DEFAULT 0
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
