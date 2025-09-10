package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis-local/internal/db"
)

func TestRenderReader(t *testing.T) {
	tests := []struct {
		name     string
		model    Model
		wantText []string
		notWant  []string
	}{
		{
			name: "Empty items list",
			model: Model{
				items:    []db.ContentItem{},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			wantText: []string{
				"No content selected",
				"Press Escape to return",
			},
			notWant: []string{"Summary", "http://"},
		},
		{
			name: "Content with all fields",
			model: Model{
				items: []db.ContentItem{
					{
						ID:       "1",
						Title:    "Test Article",
						URL:      "https://example.com/article",
						Priority: "high",
						Summary:  "This is a test summary",
						Content:  "This is the main content of the article.",
					},
				},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			wantText: []string{
				"Test Article",
				"https://example.com/article",
				"HIGH", // Priority badge
				"Summary:",
				"This is a test summary",
				"main content",
				"Esc Back to list",
			},
			notWant: []string{"No content selected"},
		},
		{
			name: "Content without summary",
			model: Model{
				items: []db.ContentItem{
					{
						ID:       "2",
						Title:    "No Summary Article",
						URL:      "https://example.com/no-summary",
						Priority: "medium",
						Content:  "Article content here.",
					},
				},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			wantText: []string{
				"No Summary Article",
				"https://example.com/no-summary",
				"MED", // Priority badge
				"Article content here",
			},
			notWant: []string{"Summary:"},
		},
		{
			name: "Content without content field",
			model: Model{
				items: []db.ContentItem{
					{
						ID:       "3",
						Title:    "Empty Content",
						URL:      "https://example.com/empty",
						Priority: "low",
						Summary:  "Has summary but no content",
						Content:  "",
					},
				},
				cursor:   0,
				view:     "reader",
				viewport: viewport.New(80, 20),
			},
			wantText: []string{
				"Empty Content",
				"LOW", // Priority badge
				"Summary:",
				"Has summary but no content",
				"No content available",
			},
			notWant: []string{},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Initialize viewport content if there are items
			if len(tt.model.items) > 0 && tt.model.cursor < len(tt.model.items) {
				item := tt.model.items[tt.model.cursor]
				var contentBuilder strings.Builder

				// Match the actual formatting from model.go
				if item.Summary != "" {
					contentBuilder.WriteString("Summary:\n")
					contentBuilder.WriteString(item.Summary)
					contentBuilder.WriteString("\n\n")
					contentBuilder.WriteString(strings.Repeat("─", 40))
					contentBuilder.WriteString("\n\n")
				}

				content := item.Content
				if content == "" {
					content = "No content available for this item."
				} else {
					contentBuilder.WriteString("Content:\n\n")
				}
				contentBuilder.WriteString(content)

				tt.model.viewport.SetContent(contentBuilder.String())
			}

			result := RenderReader(tt.model)

			// Check for expected text
			for _, want := range tt.wantText {
				if !strings.Contains(result, want) {
					t.Errorf("RenderReader() expected to contain '%s', got:\n%s",
						want, result)
				}
			}

			// Check for text that should NOT be present
			for _, notWant := range tt.notWant {
				if strings.Contains(result, notWant) {
					t.Errorf("RenderReader() should NOT contain '%s', got:\n%s",
						notWant, result)
				}
			}
		})
	}
}

func TestRenderReaderCommands(t *testing.T) {
	result := renderReaderCommands()

	expectedCommands := []string{
		"j/k/↑/↓ Scroll",
		"Space/b Page down/up",
		"Esc Back to list",
		"q Quit",
		"Commands:",
	}

	for _, cmd := range expectedCommands {
		if !strings.Contains(result, cmd) {
			t.Errorf("renderReaderCommands() expected to contain '%s', got: %s", cmd, result)
		}
	}
}
