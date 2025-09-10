package ui

import (
	"fmt"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis-local/internal/db"
)

func TestNewModel(t *testing.T) {
	m := NewModel()

	if m.priority != "all" {
		t.Errorf("Expected initial priority to be 'all', got '%s'", m.priority)
	}

	if m.view != "list" {
		t.Errorf("Expected initial view to be 'list', got '%s'", m.view)
	}

	if m.cursor != 0 {
		t.Errorf("Expected initial cursor to be 0, got %d", m.cursor)
	}

	if !m.loading {
		t.Error("Expected initial loading state to be true")
	}

	if len(m.items) != 0 {
		t.Errorf("Expected empty items initially, got %d items", len(m.items))
	}
}

func TestModelUpdate(t *testing.T) {
	tests := []struct {
		name             string
		initialModel     Model
		msg              tea.Msg
		expectedCursor   int
		expectedQuit     bool
		expectedPriority string
		expectedLoading  bool
	}{
		{
			name: "Navigate down with j",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 1,
		},
		{
			name: "Navigate down with down arrow",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyDown},
			expectedCursor: 1,
		},
		{
			name: "Navigate up with k",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 1,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'k'}},
			expectedCursor: 0,
		},
		{
			name: "Don't navigate below 0",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'k'}},
			expectedCursor: 0,
		},
		{
			name: "Don't navigate past last item",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 1,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 1,
		},
		{
			name: "Jump to top with g",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 2,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'g'}},
			expectedCursor: 0,
		},
		{
			name: "Jump to bottom with G",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'G'}},
			expectedCursor: 2,
		},
		{
			name:         "Quit with q",
			initialModel: Model{},
			msg:          tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'q'}},
			expectedQuit: true,
		},
		{
			name:         "Quit with ctrl+c",
			initialModel: Model{},
			msg:          tea.KeyMsg{Type: tea.KeyCtrlC},
			expectedQuit: true,
		},
		{
			name: "Switch to high priority",
			initialModel: Model{
				priority: "all",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'1'}},
			expectedPriority: "high",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to medium priority",
			initialModel: Model{
				priority: "all",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'2'}},
			expectedPriority: "medium",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to low priority",
			initialModel: Model{
				priority: "high",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'3'}},
			expectedPriority: "low",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to all items",
			initialModel: Model{
				priority: "high",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'a'}},
			expectedPriority: "all",
			expectedLoading:  true,
			expectedCursor:   0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			updatedModel, cmd := tt.initialModel.Update(tt.msg)
			m := updatedModel.(Model)

			// Check if quit command was returned
			if tt.expectedQuit {
				if cmd == nil {
					t.Error("Expected quit command, got nil")
				}
				// Can't easily check if it's tea.Quit without exposing internals
				return
			}

			// Check cursor position
			if m.cursor != tt.expectedCursor {
				t.Errorf("Expected cursor %d, got %d", tt.expectedCursor, m.cursor)
			}

			// Check priority if it was expected to change
			if tt.expectedPriority != "" && m.priority != tt.expectedPriority {
				t.Errorf("Expected priority '%s', got '%s'", tt.expectedPriority, m.priority)
			}

			// Check loading state if priority changed
			if tt.expectedPriority != "" && m.loading != tt.expectedLoading {
				t.Errorf("Expected loading %v, got %v", tt.expectedLoading, m.loading)
			}
		})
	}
}

func TestModelUpdateItemsLoaded(t *testing.T) {
	m := Model{
		loading: true,
		cursor:  5, // Out of bounds cursor
	}

	testItems := []db.ContentItem{
		{Title: "Item 1", Priority: "high"},
		{Title: "Item 2", Priority: "medium"},
	}

	msg := itemsLoadedMsg{
		items: testItems,
		err:   nil,
	}

	updatedModel, _ := m.Update(msg)
	updated := updatedModel.(Model)

	if updated.loading {
		t.Error("Expected loading to be false after items loaded")
	}

	if len(updated.items) != 2 {
		t.Errorf("Expected 2 items, got %d", len(updated.items))
	}

	if updated.cursor != 0 {
		t.Error("Expected cursor to reset to 0 when out of bounds")
	}

	if updated.err != nil {
		t.Errorf("Expected no error, got %v", updated.err)
	}
}

func TestModelView(t *testing.T) {
	tests := []struct {
		name     string
		model    Model
		contains []string
	}{
		{
			name: "Loading state",
			model: Model{
				loading:  true,
				priority: "high",
			},
			contains: []string{"Loading high priority items", "Press 'q' to quit"},
		},
		{
			name: "Error state",
			model: Model{
				loading: false,
				err:     fmt.Errorf("Database error"),
			},
			contains: []string{"Error:", "Press 'q' to quit"},
		},
		{
			name: "Empty items",
			model: Model{
				loading:  false,
				priority: "high",
				items:    []db.ContentItem{},
			},
			contains: []string{"No high priority items found", "1 - High priority", "Press q to quit"},
		},
		{
			name: "Items with cursor",
			model: Model{
				loading:  false,
				priority: "all",
				cursor:   1,
				items: []db.ContentItem{
					{Title: "First Item", Priority: "high"},
					{Title: "Second Item", Priority: "medium"},
					{Title: "Third Item", Priority: "low"},
				},
			},
			contains: []string{
				"Prismis TUI",
				"ALL (3 items)",
				"HIGH  First Item",
				"▸  MED  Second Item",
				"LOW  Third Item",
				"Commands:",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			view := tt.model.View()
			for _, expected := range tt.contains {
				if !contains(view, expected) {
					t.Errorf("Expected view to contain '%s', but it didn't.\nView: %s", expected, view)
				}
			}
		})
	}
}

// Helper function to check if string contains substring
func contains(s, substr string) bool {
	return len(substr) > 0 && len(s) >= len(substr) &&
		(s == substr || len(s) > len(substr) && containsHelper(s, substr))
}

func containsHelper(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
