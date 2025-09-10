package db

import (
	"database/sql"
	"testing"

	_ "github.com/mattn/go-sqlite3"
)

// TestUnprioritizedFiltering tests that unprioritized items are filtered correctly
func TestUnprioritizedFiltering(t *testing.T) {
	// Create a test database in memory
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("Failed to open test database: %v", err)
	}
	defer db.Close()

	// Create schema
	schema := `
	CREATE TABLE sources (
		id TEXT PRIMARY KEY,
		name TEXT,
		type TEXT,
		active INTEGER DEFAULT 1,
		last_fetched_at TEXT,
		error_count INTEGER DEFAULT 0
	);

	CREATE TABLE content (
		id TEXT PRIMARY KEY,
		source_id TEXT REFERENCES sources(id),
		title TEXT NOT NULL,
		url TEXT NOT NULL,
		summary TEXT,
		priority TEXT,
		content TEXT,
		analysis TEXT,
		published_at TEXT,
		read INTEGER DEFAULT 0
	);
	`

	if _, err := db.Exec(schema); err != nil {
		t.Fatalf("Failed to create schema: %v", err)
	}

	// Insert test data
	// Add a source
	_, err = db.Exec(`INSERT INTO sources (id, name, type) VALUES ('test-source', 'Test Source', 'rss')`)
	if err != nil {
		t.Fatalf("Failed to insert source: %v", err)
	}

	// Add content with various priorities
	testData := []struct {
		id       string
		title    string
		priority sql.NullString
	}{
		{"1", "High Priority Item", sql.NullString{String: "high", Valid: true}},
		{"2", "Medium Priority Item", sql.NullString{String: "medium", Valid: true}},
		{"3", "Low Priority Item", sql.NullString{String: "low", Valid: true}},
		{"4", "Null Priority Item", sql.NullString{Valid: false}},
		{"5", "Empty Priority Item", sql.NullString{String: "", Valid: true}},
	}

	for _, item := range testData {
		_, err := db.Exec(
			`INSERT INTO content (id, source_id, title, url, priority, read) VALUES (?, 'test-source', ?, 'http://example.com', ?, 0)`,
			item.id, item.title, item.priority,
		)
		if err != nil {
			t.Fatalf("Failed to insert content %s: %v", item.id, err)
		}
	}

	// Test 1: With showUnprioritized = false, should only get prioritized items
	rows, err := db.Query(`
		SELECT c.id, c.title FROM content c
		JOIN sources s ON c.source_id = s.id
		WHERE c.read = 0
		AND c.priority IS NOT NULL AND c.priority != ''
		ORDER BY c.published_at DESC
	`)
	if err != nil {
		t.Fatalf("Failed to query with filter: %v", err)
	}
	defer rows.Close()

	var filteredCount int
	for rows.Next() {
		var id, title string
		if err := rows.Scan(&id, &title); err != nil {
			t.Fatalf("Failed to scan row: %v", err)
		}
		filteredCount++

		// Should not see items 4 or 5
		if id == "4" || id == "5" {
			t.Errorf("Unprioritized item %s should have been filtered out", id)
		}
	}

	if filteredCount != 3 {
		t.Errorf("Expected 3 prioritized items, got %d", filteredCount)
	}

	// Test 2: With showUnprioritized = true, should get all items
	rows2, err := db.Query(`
		SELECT c.id FROM content c
		JOIN sources s ON c.source_id = s.id
		WHERE c.read = 0
		ORDER BY c.published_at DESC
	`)
	if err != nil {
		t.Fatalf("Failed to query without filter: %v", err)
	}
	defer rows2.Close()

	var unfilteredCount int
	for rows2.Next() {
		var id string
		if err := rows2.Scan(&id); err != nil {
			t.Fatalf("Failed to scan row: %v", err)
		}
		unfilteredCount++
	}

	if unfilteredCount != 5 {
		t.Errorf("Expected 5 total items, got %d", unfilteredCount)
	}

	// Test 3: Count unprioritized items
	var unprioritizedCount int
	err = db.QueryRow(`
		SELECT COUNT(*) FROM content 
		WHERE read = 0 
		AND (priority IS NULL OR priority = '')
	`).Scan(&unprioritizedCount)
	if err != nil {
		t.Fatalf("Failed to count unprioritized: %v", err)
	}

	if unprioritizedCount != 2 {
		t.Errorf("Expected 2 unprioritized items, got %d", unprioritizedCount)
	}
}
