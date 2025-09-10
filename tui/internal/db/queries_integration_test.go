//go:build integration
// +build integration

package db

import (
	"os"
	"testing"
)

func TestGetContentByPriorityRealDB(t *testing.T) {
	// Skip if database doesn't exist
	homeDir, err := os.UserHomeDir()
	if err != nil {
		t.Skip("Cannot determine home directory")
	}

	dbPath := homeDir + "/.config/prismis/prismis.db"
	if _, err := os.Stat(dbPath); os.IsNotExist(err) {
		t.Skip("Real database doesn't exist at ~/.config/prismis/prismis.db")
	}

	// Test with real database
	tests := []struct {
		name     string
		priority string
	}{
		{
			name:     "Get all items from real DB",
			priority: "all",
		},
		{
			name:     "Get high priority from real DB",
			priority: "high",
		},
		{
			name:     "Get medium priority from real DB",
			priority: "medium",
		},
		{
			name:     "Get low priority from real DB",
			priority: "low",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			items, err := GetContentByPriority(tt.priority)
			if err != nil {
				t.Fatalf("Failed to get content: %v", err)
			}

			t.Logf("Found %d items for priority '%s'", len(items), tt.priority)

			// Verify items have expected fields populated
			for _, item := range items {
				if item.ID == "" {
					t.Error("Item has empty ID")
				}
				if item.Title == "" {
					t.Error("Item has empty title")
				}
				// URL might be empty for test data, just log it
				if item.URL == "" {
					t.Logf("Item '%s' has empty URL (might be test data)", item.Title)
				}

				// If specific priority requested, verify it matches
				if tt.priority != "all" && item.Priority != "" && item.Priority != tt.priority {
					t.Errorf("Expected priority %s, got %s", tt.priority, item.Priority)
				}

				// All items should be unread
				if item.Read {
					t.Error("Got read item when expecting only unread items")
				}
			}
		})
	}
}
