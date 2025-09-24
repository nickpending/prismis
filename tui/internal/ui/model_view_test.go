package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/db"
)

func TestViewSwitching(t *testing.T) {
	tests := []struct {
		name         string
		initialModel Model
		msg          tea.Msg
		expectedView string
	}{
		{
			name: "Enter key switches to reader view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1", Content: "Content 1"},
					{Title: "Item 2", Content: "Content 2"},
				},
				cursor:   0,
				view:     "list",
				viewport: viewport.New(80, 20),
			},
			msg:          tea.KeyMsg{Type: tea.KeyEnter},
			expectedView: "reader",
		},
		{
			name: "Enter key does nothing with empty items",
			initialModel: Model{
				items:    []db.ContentItem{},
				cursor:   0,
				view:     "list",
				viewport: viewport.New(80, 20),
			},
			msg:          tea.KeyMsg{Type: tea.KeyEnter},
			expectedView: "list", // Should stay in list view
		},
		{
			name: "Escape key returns to list view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1", Content: "Content 1"},
				},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			msg:          tea.KeyMsg{Type: tea.KeyEscape},
			expectedView: "list",
		},
		{
			name: "Escape key does nothing in list view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1", Content: "Content 1"},
				},
				cursor:   0,
				view:     "list",
				viewport: viewport.New(80, 20),
			},
			msg:          tea.KeyMsg{Type: tea.KeyEscape},
			expectedView: "list",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			updatedModel, _ := tt.initialModel.Update(tt.msg)
			m := updatedModel.(Model)

			if m.view != tt.expectedView {
				t.Errorf("Expected view '%s', got '%s'", tt.expectedView, m.view)
			}
		})
	}
}

func TestNavigationInDifferentViews(t *testing.T) {
	tests := []struct {
		name           string
		initialModel   Model
		msg            tea.Msg
		expectedCursor int
		description    string
	}{
		{
			name: "j/k navigation works in list view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor:   0,
				view:     "list",
				viewport: viewport.New(80, 20),
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 1,
			description:    "j should move cursor down in list view",
		},
		{
			name: "j/k navigation disabled in reader view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 0, // Should not change
			description:    "j should not change cursor in reader view",
		},
		{
			name: "Priority switching disabled in reader view",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
				},
				cursor:   0,
				view:     "reader",
				priority: "all",
				viewport: viewport.New(80, 20),
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'1'}},
			expectedCursor: 0,
			description:    "Priority switching should be disabled in reader view",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			updatedModel, _ := tt.initialModel.Update(tt.msg)
			m := updatedModel.(Model)

			if m.cursor != tt.expectedCursor {
				t.Errorf("%s: Expected cursor %d, got %d",
					tt.description, tt.expectedCursor, m.cursor)
			}
		})
	}
}

func TestReaderViewRendering(t *testing.T) {
	// Test that View() method correctly switches between list and reader
	tests := []struct {
		name     string
		model    Model
		wantText []string
	}{
		{
			name: "View renders list when view='list'",
			model: Model{
				items: []db.ContentItem{
					{Title: "List Item", Priority: "high"},
				},
				cursor:   0,
				view:     "list",
				priority: "all",
				loading:  false,
				width:    80,
				height:   20,
				viewport: viewport.New(80, 20),
			},
			wantText: []string{"PRISMIS", "List Item", "Press ? for help"},
		},
		{
			name: "View renders reader when view='reader'",
			model: Model{
				items: []db.ContentItem{
					{
						Title:   "Reader Item",
						URL:     "https://example.com",
						Content: "Test content",
						Summary: "Test summary",
					},
				},
				cursor:   0,
				view:     "reader",
				loading:  false,
				width:    80,
				height:   20,
				viewport: viewport.New(80, 20),
			},
			wantText: []string{"Reader Item", "ARTICLE 1 of 1", "Press ? for help"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Initialize viewport content for reader view
			if tt.model.view == "reader" && len(tt.model.items) > 0 {
				item := tt.model.items[tt.model.cursor]
				var contentBuilder strings.Builder

				if item.Summary != "" {
					contentBuilder.WriteString("Summary:\n")
					contentBuilder.WriteString(item.Summary)
					contentBuilder.WriteString("\n\n")
				}

				content := item.Content
				if content == "" {
					content = "No content available."
				}
				contentBuilder.WriteString(content)

				tt.model.viewport.SetContent(contentBuilder.String())
			}

			result := tt.model.View()

			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("View() in %s mode expected to contain '%s', got:\n%s",
						tt.model.view, want, result)
				}
			}
		})
	}
}
