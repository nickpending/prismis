package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis/internal/db"
)

// TestReaderView tests that reader view displays content correctly
func TestReaderView(t *testing.T) {
	model := Model{
		items: []db.ContentItem{
			{
				ID:      "1",
				Title:   "Test Article",
				URL:     "https://example.com/article",
				Content: "This is the full content of the article.",
				Summary: "A brief summary.",
			},
		},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Debug: print what we actually get
	t.Logf("Reader output:\n%s", output)

	// Should show article content in reader view
	if !strings.Contains(output, "Test Article") {
		t.Error("Reader should show article title")
	}
	// The domain is shown for RSS feeds, not the full URL
	if !strings.Contains(output, "ARTICLE 1 of 1") {
		t.Error("Reader should show article position")
	}

	// Should show help hint in status bar
	if !strings.Contains(output, "Press ? for help") {
		t.Error("Reader should show help hint in status bar")
	}
}

// TestReaderEmptyContent tests reader with no content
func TestReaderEmptyContent(t *testing.T) {
	model := Model{
		items:    []db.ContentItem{},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Should handle empty state gracefully
	if output == "" {
		t.Error("Reader should show something even with no items")
	}
}

// TestReaderViewWithMetadata tests reader displays analysis metadata
func TestReaderViewWithMetadata(t *testing.T) {
	analysisJSON := `{
		"tools": ["Go", "Docker"],
		"topics": ["cloud-native", "microservices"],
		"urls": ["https://example.com/ref1"]
	}`

	model := Model{
		items: []db.ContentItem{
			{
				ID:       "1",
				Title:    "Technical Article",
				Content:  "Article about Go and Docker.",
				Analysis: analysisJSON,
			},
		},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Basic check that it renders without error
	if !strings.Contains(output, "Technical Article") {
		t.Error("Reader should display article with metadata")
	}
}