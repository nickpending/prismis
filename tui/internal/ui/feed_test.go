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

// TestFeedKindDisplay_ClassifiedItem verifies the feed row shows the item's kind
// (mined from analysis.kind) in lowercase, as stored.
func TestFeedKindDisplay_ClassifiedItem(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Classified Item", Priority: "high", Analysis: `{"kind":"release","kind_confidence":0.91}`},
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
	if !strings.Contains(output, "release") {
		t.Errorf("Expected feed row to show kind 'release'. Got: %s", output)
	}
}

// TestFeedKindDisplay_UnclassifiedItem verifies an unclassified item (no kind in
// analysis) shows no kind label at all.
func TestFeedKindDisplay_UnclassifiedItem(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Unclassified Item", Priority: "high", Analysis: `{"kind":null,"kind_confidence":0.4}`},
		{ID: "2", Title: "No Analysis Item", Priority: "medium", Analysis: ""},
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
	for _, kind := range []string{"release", "experience", "question", "analysis", "news", "incident", "research", "vulnerability", "humor", "tutorial"} {
		if strings.Contains(output, kind) {
			t.Errorf("Unclassified items must show no kind label, but found %q. Got: %s", kind, output)
		}
	}
}

// TestReaderKindDisplay verifies the reader header shows the item's kind.
func TestReaderKindDisplay(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Reader Article", Content: "Body.", Analysis: `{"kind":"tutorial","kind_confidence":0.85}`},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}
	model.updateReaderContent()

	output := model.View()
	if !strings.Contains(output, "tutorial") {
		t.Errorf("Expected reader header to show kind 'tutorial'. Got: %s", output)
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
