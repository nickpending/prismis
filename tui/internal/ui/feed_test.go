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
		priority: "low",
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
		priority: "low",
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
		priority: "low",
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
		priority: "low",
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

// TestFeedTitleOnlyDisplay_MarkerShown verifies the feed row shows the lower-case
// "title only" marker (SC-6) for an item whose analysis carries title_only: true.
func TestFeedTitleOnlyDisplay_MarkerShown(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Not Readable Item", Priority: "high", Analysis: `{"title_only":true}`},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "list",
		priority: "low",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()
	if !strings.Contains(output, "title only") {
		t.Errorf("Expected feed row to show 'title only' marker. Got: %s", output)
	}
}

// TestFeedTitleOnlyDisplay_NoMarkerWhenReadable verifies an item without
// title_only (absent, or explicitly false) shows no marker at all.
func TestFeedTitleOnlyDisplay_NoMarkerWhenReadable(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Readable Item", Priority: "high", Analysis: `{"title_only":false}`},
		{ID: "2", Title: "No Flag Item", Priority: "medium", Analysis: `{"kind":"news"}`},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "list",
		priority: "low",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()
	if strings.Contains(output, "title only") {
		t.Errorf("Readable items must show no 'title only' marker. Got: %s", output)
	}
}

// TestReaderTitleOnlyDisplay verifies the reader header shows the "title only"
// marker (SC-6) for an item whose analysis carries title_only: true.
func TestReaderTitleOnlyDisplay(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Reader Article", Content: "Body.", Analysis: `{"title_only":true}`},
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
	if !strings.Contains(output, "title only") {
		t.Errorf("Expected reader header to show 'title only' marker. Got: %s", output)
	}
}

// TestReaderTitleOnlyDisplay_NoMarkerWhenReadable verifies the reader header shows
// no "title only" marker for an item whose analysis doesn't carry title_only: true.
func TestReaderTitleOnlyDisplay_NoMarkerWhenReadable(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Reader Article", Content: "Body.", Analysis: `{"title_only":false,"kind":"tutorial"}`},
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
	if strings.Contains(output, "title only") {
		t.Errorf("Readable items must show no 'title only' marker in the reader. Got: %s", output)
	}
}

// TestFeedNoEntityTags verifies the feed row shows no tag list for an item whose
// stored analysis still carries an entities array (older items analysed before the
// entities field was dropped from the summarizer). The row must render the title
// and priority normally and must not surface any of the stored entity strings.
func TestFeedNoEntityTags(t *testing.T) {
	items := []db.ContentItem{
		{
			ID:       "1",
			Title:    "Legacy Analysed Item",
			Priority: "high",
			Analysis: `{"entities":["cve-2026-88772","citrix-netscaler","reuters"],"kind":"vulnerability"}`,
		},
	}

	model := Model{
		items:    items,
		cursor:   0,
		view:     "list",
		priority: "low",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	if !strings.Contains(output, "Legacy Analysed Item") {
		t.Errorf("Feed row should still render the title. Got: %s", output)
	}
	for _, entity := range []string{"cve-2026-88772", "citrix-netscaler", "reuters"} {
		if strings.Contains(output, entity) {
			t.Errorf("Feed row must not show stored entity %q as a tag. Got: %s", entity, output)
		}
	}
}

// TestReaderNoEntityTags verifies the reader shows no tag list for an item whose
// stored analysis still carries an entities array. The reader must render the
// title and metadata normally and must not surface any of the stored entity strings.
func TestReaderNoEntityTags(t *testing.T) {
	items := []db.ContentItem{
		{
			ID:       "1",
			Title:    "Legacy Reader Item",
			Content:  "Body.",
			Analysis: `{"entities":["cve-2026-88772","citrix-netscaler","reuters"],"kind":"vulnerability"}`,
		},
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

	if !strings.Contains(output, "Legacy Reader Item") {
		t.Errorf("Reader should still render the title. Got: %s", output)
	}
	for _, entity := range []string{"cve-2026-88772", "citrix-netscaler", "reuters"} {
		if strings.Contains(output, entity) {
			t.Errorf("Reader must not show stored entity %q as a tag. Got: %s", entity, output)
		}
	}
}

// TestFeedEmptyStates tests that empty states render correctly
func TestFeedEmptyStates(t *testing.T) {
	model := Model{
		items:    []db.ContentItem{},
		view:     "list",
		priority: "low",
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
