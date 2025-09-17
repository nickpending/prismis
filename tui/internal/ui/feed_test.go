package ui

import (
	"errors"
	"strings"
	"testing"

	"github.com/nickpending/prismis/internal/db"
)

func TestRenderItem(t *testing.T) {
	tests := []struct {
		name         string
		item         db.ContentItem
		selected     bool
		wantCursor   bool
		wantPriority bool
		wantTitle    string
	}{
		{
			name: "Selected item with high priority",
			item: db.ContentItem{
				Title:    "Test Item",
				Priority: "high",
			},
			selected:     true,
			wantCursor:   true,
			wantPriority: true,
			wantTitle:    "Test Item",
		},
		{
			name: "Unselected item with medium priority",
			item: db.ContentItem{
				Title:    "Another Item",
				Priority: "medium",
			},
			selected:     false,
			wantCursor:   false,
			wantPriority: true,
			wantTitle:    "Another Item",
		},
		{
			name: "Item with no priority",
			item: db.ContentItem{
				Title:    "No Priority Item",
				Priority: "",
			},
			selected:     false,
			wantCursor:   false,
			wantPriority: false,
			wantTitle:    "No Priority Item",
		},
		{
			name: "Item with very long title gets truncated",
			item: db.ContentItem{
				Title:    "This is a very long title that should be truncated because it exceeds the maximum width allowed for display in the terminal user interface",
				Priority: "low",
			},
			selected:     false,
			wantCursor:   false,
			wantPriority: true,
			wantTitle:    "...", // Will contain ellipsis
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderItem(tt.item, tt.selected)

			// Check cursor presence
			if tt.wantCursor && !strings.Contains(result, "▸") {
				t.Errorf("Expected cursor '▸' in output, got: %s", result)
			}
			if !tt.wantCursor && strings.Contains(result, "▸") {
				t.Errorf("Did not expect cursor in output, got: %s", result)
			}

			// Check priority badge presence
			if tt.wantPriority {
				if tt.item.Priority == "high" && !strings.Contains(result, "HIGH") {
					t.Errorf("Expected HIGH badge, got: %s", result)
				}
				if tt.item.Priority == "medium" && !strings.Contains(result, "MED") {
					t.Errorf("Expected MED badge, got: %s", result)
				}
				if tt.item.Priority == "low" && !strings.Contains(result, "LOW") {
					t.Errorf("Expected LOW badge, got: %s", result)
				}
			}

			// Check title or ellipsis
			if tt.wantTitle == "..." {
				if !strings.Contains(result, "...") {
					t.Errorf("Expected ellipsis for long title, got: %s", result)
				}
			} else if !strings.Contains(result, tt.wantTitle) {
				t.Errorf("Expected title '%s' in output, got: %s", tt.wantTitle, result)
			}
		})
	}
}

func TestRenderPriorityBadge(t *testing.T) {
	tests := []struct {
		name     string
		priority string
		want     string
	}{
		{
			name:     "High priority badge",
			priority: "high",
			want:     "HIGH",
		},
		{
			name:     "Medium priority badge",
			priority: "medium",
			want:     "MED",
		},
		{
			name:     "Low priority badge",
			priority: "low",
			want:     "LOW",
		},
		{
			name:     "Unknown priority badge",
			priority: "unknown",
			want:     "[unknown]",
		},
		{
			name:     "Empty priority badge",
			priority: "",
			want:     "[]",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderPriorityBadge(tt.priority)
			if !strings.Contains(result, tt.want) {
				t.Errorf("renderPriorityBadge(%s) expected to contain '%s', got: %s",
					tt.priority, tt.want, result)
			}
		})
	}
}

func TestRenderHeader(t *testing.T) {
	tests := []struct {
		name     string
		priority string
		count    int
		wantText []string
	}{
		{
			name:     "High priority with items",
			priority: "high",
			count:    5,
			wantText: []string{"Prismis TUI", "HIGH", "(5 items)"},
		},
		{
			name:     "All items",
			priority: "all",
			count:    10,
			wantText: []string{"Prismis TUI", "ALL", "(10 items)"},
		},
		{
			name:     "Medium priority with single item",
			priority: "medium",
			count:    1,
			wantText: []string{"Prismis TUI", "MEDIUM", "(1 items)"},
		},
		{
			name:     "Low priority with no items",
			priority: "low",
			count:    0,
			wantText: []string{"Prismis TUI", "LOW", "(0 items)"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderHeader(tt.priority, tt.count)

			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("renderHeader(%s, %d) expected to contain '%s', got: %s",
						tt.priority, tt.count, want, result)
				}
			}
		})
	}
}

func TestRenderEmptyState(t *testing.T) {
	tests := []struct {
		name     string
		priority string
		wantText []string
	}{
		{
			name:     "Empty high priority",
			priority: "high",
			wantText: []string{"No high priority items found", "1 - High priority", "q to quit"},
		},
		{
			name:     "Empty all items",
			priority: "all",
			wantText: []string{"No all priority items found", "a - All items", "q to quit"},
		},
		{
			name:     "Empty medium priority",
			priority: "medium",
			wantText: []string{"No medium priority items found", "2 - Medium priority"},
		},
		{
			name:     "Empty low priority",
			priority: "low",
			wantText: []string{"No low priority items found", "3 - Low priority"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderEmptyState(tt.priority)

			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("renderEmptyState(%s) expected to contain '%s', got: %s",
						tt.priority, want, result)
				}
			}
		})
	}
}

func TestRenderLoading(t *testing.T) {
	tests := []struct {
		name     string
		priority string
		wantText []string
	}{
		{
			name:     "Loading high priority",
			priority: "high",
			wantText: []string{"⏳", "Loading high priority items", "Press 'q' to quit"},
		},
		{
			name:     "Loading all items",
			priority: "all",
			wantText: []string{"⏳", "Loading all priority items", "Press 'q' to quit"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderLoading(tt.priority)

			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("renderLoading(%s) expected to contain '%s', got: %s",
						tt.priority, want, result)
				}
			}
		})
	}
}

func TestRenderError(t *testing.T) {
	tests := []struct {
		name     string
		err      error
		wantText []string
	}{
		{
			name:     "Database error",
			err:      errors.New("database connection failed"),
			wantText: []string{"Error:", "database connection failed", "Press 'q' to quit"},
		},
		{
			name:     "Network error",
			err:      errors.New("network timeout"),
			wantText: []string{"Error:", "network timeout", "Press 'q' to quit"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := renderError(tt.err)

			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("renderError(%v) expected to contain '%s', got: %s",
						tt.err, want, result)
				}
			}
		})
	}
}

func TestRenderCommands(t *testing.T) {
	result := renderCommands()

	expectedCommands := []string{
		"j/k/↑/↓ Navigate",
		"g/G Top/Bottom",
		"1/2/3/a Priority",
		"q Quit",
		"Commands:",
	}

	for _, cmd := range expectedCommands {
		if !strings.Contains(result, cmd) {
			t.Errorf("renderCommands() expected to contain '%s', got: %s", cmd, result)
		}
	}
}

// Integration test for complete RenderList functionality
func TestRenderList(t *testing.T) {
	tests := []struct {
		name     string
		model    Model
		wantText []string
		notWant  []string
	}{
		{
			name: "Loading state",
			model: Model{
				loading:  true,
				priority: "high",
			},
			wantText: []string{"⏳", "Loading high priority items", "Press 'q' to quit"},
			notWant:  []string{"Prismis TUI", "Commands:"},
		},
		{
			name: "Error state",
			model: Model{
				loading: false,
				err:     errors.New("connection failed"),
			},
			wantText: []string{"Error:", "connection failed", "Press 'q' to quit"},
			notWant:  []string{"Prismis TUI", "Commands:"},
		},
		{
			name: "Empty state",
			model: Model{
				loading:  false,
				priority: "medium",
				items:    []db.ContentItem{},
			},
			wantText: []string{"No medium priority items found", "2 - Medium priority"},
			notWant:  []string{"▸"},
		},
		{
			name: "List with items",
			model: Model{
				loading:  false,
				priority: "all",
				cursor:   1,
				items: []db.ContentItem{
					{
						ID:       "1",
						Title:    "First Item",
						Priority: "high",
					},
					{
						ID:       "2",
						Title:    "Second Item",
						Priority: "medium",
					},
					{
						ID:       "3",
						Title:    "Third Item",
						Priority: "low",
					},
				},
			},
			wantText: []string{
				"Prismis TUI",
				"ALL",
				"(3 items)",
				"First Item",
				"Second Item",
				"Third Item",
				"HIGH",
				"MED",
				"LOW",
				"Commands:",
				"▸", // Cursor should be present
			},
			notWant: []string{"Loading", "Error"},
		},
		{
			name: "Single item with cursor",
			model: Model{
				loading:  false,
				priority: "high",
				cursor:   0,
				items: []db.ContentItem{
					{
						ID:       "1",
						Title:    "Only Item",
						Priority: "high",
					},
				},
			},
			wantText: []string{
				"Prismis TUI",
				"HIGH",
				"(1 items)",
				"Only Item",
				"▸",
				"Commands:",
			},
			notWant: []string{"Loading", "Error", "No high priority"},
		},
		{
			name: "Items with long title",
			model: Model{
				loading:  false,
				priority: "all",
				cursor:   0,
				items: []db.ContentItem{
					{
						ID:       "1",
						Title:    "This is an extremely long title that will definitely exceed the maximum width and should be truncated with ellipsis",
						Priority: "high",
					},
				},
			},
			wantText: []string{
				"Prismis TUI",
				"...", // Truncated title
				"Commands:",
			},
			notWant: []string{"and should be truncated with ellipsis"}, // This part should be cut off
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := RenderList(tt.model)

			// Check for expected text
			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("RenderList() expected to contain '%s', got:\n%s",
						want, result)
				}
			}

			// Check for text that should NOT be present
			for _, notWant := range tt.notWant {
				if strings.Contains(result, notWant) {
					t.Errorf("RenderList() should NOT contain '%s', got:\n%s",
						notWant, result)
				}
			}

			// Additional validation for list with items
			if len(tt.model.items) > 0 && !tt.model.loading && tt.model.err == nil {
				// Should have header, items, and footer
				lines := strings.Split(result, "\n")
				if len(lines) < 4 {
					t.Errorf("Expected at least 4 lines for non-empty list, got %d", len(lines))
				}

				// Verify we have the right number of item lines
				// Items should have cursor or priority badges
				itemLines := 0
				for _, line := range lines {
					// Count lines that look like items (have cursor or priority badges)
					if strings.Contains(line, "▸") || strings.Contains(line, "HIGH") ||
						strings.Contains(line, "MED") || strings.Contains(line, "LOW") {
						// Make sure it's not the header line
						if !strings.Contains(line, "Prismis TUI") && !strings.Contains(line, "items)") {
							itemLines++
						}
					}
				}

				if itemLines != len(tt.model.items) {
					t.Errorf("Expected %d item lines, found %d", len(tt.model.items), itemLines)
				}
			}
		})
	}
}
