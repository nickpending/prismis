package ui

import (
	"testing"
	"time"

	"github.com/nickpending/prismis-local/internal/db"
)

// TestSortItemsByDate verifies the date sorting functionality
func TestSortItemsByDate(t *testing.T) {
	// Create test items with different dates
	items := []db.ContentItem{
		{
			ID:        "1",
			Title:     "Middle Item",
			Published: time.Date(2024, 1, 15, 0, 0, 0, 0, time.UTC),
		},
		{
			ID:        "2",
			Title:     "Newest Item",
			Published: time.Date(2024, 1, 20, 0, 0, 0, 0, time.UTC),
		},
		{
			ID:        "3",
			Title:     "Oldest Item",
			Published: time.Date(2024, 1, 10, 0, 0, 0, 0, time.UTC),
		},
	}

	// Test newest first sorting
	itemsCopy := make([]db.ContentItem, len(items))
	copy(itemsCopy, items)
	sortItemsByDate(itemsCopy, true)

	if itemsCopy[0].ID != "2" {
		t.Errorf("Expected newest item first, got %s", itemsCopy[0].ID)
	}
	if itemsCopy[2].ID != "3" {
		t.Errorf("Expected oldest item last, got %s", itemsCopy[2].ID)
	}

	// Test oldest first sorting
	copy(itemsCopy, items)
	sortItemsByDate(itemsCopy, false)

	if itemsCopy[0].ID != "3" {
		t.Errorf("Expected oldest item first, got %s", itemsCopy[0].ID)
	}
	if itemsCopy[2].ID != "2" {
		t.Errorf("Expected newest item last, got %s", itemsCopy[2].ID)
	}
}

// TestFilterTypeCycling verifies the source type filter cycling
func TestFilterTypeCycling(t *testing.T) {
	filterTypes := []string{"all", "rss", "reddit", "youtube"}

	tests := []struct {
		current  string
		expected string
	}{
		{"all", "rss"},
		{"rss", "reddit"},
		{"reddit", "youtube"},
		{"youtube", "all"},
	}

	for _, tt := range tests {
		currentIdx := 0
		for i, ft := range filterTypes {
			if ft == tt.current {
				currentIdx = i
				break
			}
		}
		next := filterTypes[(currentIdx+1)%len(filterTypes)]
		if next != tt.expected {
			t.Errorf("Cycling from %s: expected %s, got %s", tt.current, tt.expected, next)
		}
	}
}

// TestViewStateStringBuilder verifies the header state string
func TestViewStateStringBuilder(t *testing.T) {
	m := Model{
		showAll:           false,
		sortNewest:        true,
		filterType:        "rss",
		hiddenCount:       5,
		showUnprioritized: false,
	}

	result := buildViewStateString(m)
	expected := "View: UNREAD | Sort: NEWEST | Filter: RSS | Hidden: 5"
	if result != expected {
		t.Errorf("Expected '%s', got '%s'", expected, result)
	}

	// Test with all items showing
	m.showAll = true
	m.sortNewest = false
	m.filterType = "all"
	m.showUnprioritized = true // Hidden count shouldn't show

	result = buildViewStateString(m)
	expected = "View: ALL | Sort: OLDEST | Filter: ALL"
	if result != expected {
		t.Errorf("Expected '%s', got '%s'", expected, result)
	}
}
