package ui

import (
	"strings"
	"testing"

	"github.com/nickpending/prismis-local/internal/db"
)

func TestSourceModal_LoadSources_UpdatesContent(t *testing.T) {
	// Create a new source modal
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	// Initial sources
	initialSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
		{ID: "2", Name: "Source 2", Type: "reddit", Active: true},
		{ID: "3", Name: "Source 3", Type: "youtube", Active: true},
	}

	// Load initial sources
	modal.LoadSources(initialSources)

	// Get initial content
	initialContent := modal.content
	if !strings.Contains(initialContent, "Source 1") {
		t.Errorf("Expected content to contain 'Source 1', got: %s", initialContent)
	}
	if !strings.Contains(initialContent, "Source 2") {
		t.Errorf("Expected content to contain 'Source 2', got: %s", initialContent)
	}
	if !strings.Contains(initialContent, "Source 3") {
		t.Errorf("Expected content to contain 'Source 3', got: %s", initialContent)
	}

	// Simulate deletion - remove Source 2
	updatedSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
		{ID: "3", Name: "Source 3", Type: "youtube", Active: true},
	}

	// Load updated sources
	modal.LoadSources(updatedSources)

	// Get updated content
	updatedContent := modal.content

	// Verify content was updated
	if initialContent == updatedContent {
		t.Error("Content should have changed after loading new sources")
	}
	if !strings.Contains(updatedContent, "Source 1") {
		t.Errorf("Expected updated content to contain 'Source 1', got: %s", updatedContent)
	}
	if strings.Contains(updatedContent, "Source 2") {
		t.Errorf("Deleted source 'Source 2' should not appear in content, got: %s", updatedContent)
	}
	if !strings.Contains(updatedContent, "Source 3") {
		t.Errorf("Expected updated content to contain 'Source 3', got: %s", updatedContent)
	}

	// Verify cursor adjustment
	if modal.cursor > len(updatedSources)-1 {
		t.Errorf("Cursor should be within bounds, got cursor=%d for %d sources", modal.cursor, len(updatedSources))
	}
}

func TestSourceModal_LoadSources_EmptyList(t *testing.T) {
	// Create a new source modal
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	// Initial sources
	initialSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
	}

	// Load initial sources
	modal.LoadSources(initialSources)
	if !strings.Contains(modal.content, "Source 1") {
		t.Errorf("Expected content to contain 'Source 1', got: %s", modal.content)
	}

	// Load empty sources (all deleted)
	modal.LoadSources([]db.Source{})

	// Verify content shows "No sources configured"
	if !strings.Contains(modal.content, "No sources configured") {
		t.Errorf("Expected content to show 'No sources configured', got: %s", modal.content)
	}
	if strings.Contains(modal.content, "Source 1") {
		t.Errorf("Source 1 should not appear after loading empty list, got: %s", modal.content)
	}
}

func TestSourceModal_ErrorMessageDisplay(t *testing.T) {
	// Create a new source modal
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"
	modal.errorMsg = "Subreddit r/ai does not exist"

	// Update content
	modal.UpdateContent()

	// Verify error message appears in content
	if !strings.Contains(modal.content, "Subreddit r/ai does not exist") {
		t.Errorf("Expected error message in content, got: %s", modal.content)
	}
}
