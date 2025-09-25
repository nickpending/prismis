package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis/internal/db"
)

// TestFeedNavigation tests that cursor movement and item selection work correctly
func TestFeedNavigation(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "First", Priority: "high"},
		{ID: "2", Title: "Second", Priority: "medium"},
		{ID: "3", Title: "Third", Priority: "low"},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "list",
		priority: "all",
		loading:  false, // Must be false to render content
		width:    100,   // Must be non-zero
		height:   30,    // Must be non-zero
		viewport: viewport.New(100, 30),
	}

	// Initial render should show first item selected
	output := model.View()
	// Debug: print what we actually get
	if testing.Verbose() {
		t.Logf("Output: %s", output)
	}
	if !strings.Contains(output, "First") {
		t.Errorf("First item not visible in initial view. Got: %s", output)
	}

	// Move cursor down
	model.cursor = 1
	output = model.View()
	if !strings.Contains(output, "Second") {
		t.Error("Second item should be visible after cursor move")
	}

	// Verify all items are shown in 'all' priority
	if !strings.Contains(output, "First") || !strings.Contains(output, "Second") || !strings.Contains(output, "Third") {
		t.Error("All items should be visible in 'all' priority view")
	}
}

// TestFeedPriorityDisplay tests that different priorities display correctly
func TestFeedPriorityDisplay(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "High Priority", Priority: "high"},
		{ID: "2", Title: "Medium Priority", Priority: "medium"},
		{ID: "3", Title: "Low Priority", Priority: "low"},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "list",
		priority: "all",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// All items should be visible when priority is "all"
	if !strings.Contains(output, "High Priority") {
		t.Error("High priority item should be visible")
	}
	if !strings.Contains(output, "Medium Priority") {
		t.Error("Medium priority item should be visible")
	}
	if !strings.Contains(output, "Low Priority") {
		t.Error("Low priority item should be visible")
	}
}

// TestFeedEmptyStates tests that empty states render correctly
func TestFeedEmptyStates(t *testing.T) {
	model := Model{
		items:    []db.ContentItem{},
		view:     "list",
		priority: "all",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()
	// Just verify it doesn't panic and returns something
	if output == "" {
		t.Error("Empty state should render something")
	}

	// Loading state
	model.loading = true
	output = model.View()
	if !strings.Contains(output, "Loading") {
		t.Error("Loading state should show loading message")
	}
}
