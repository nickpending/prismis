package ui

import (
	"strings"
)

// updateReaderContent updates the viewport with article content (called from model.go)
func (m *Model) updateReaderContent() {
	if m.cursor >= len(m.items) || len(m.items) == 0 {
		m.viewport.SetContent("No content selected")
		return
	}

	item := m.items[m.cursor]

	// Calculate content pane dimensions (same as in RenderList)
	contentHeight := m.height - 5
	sidebarWidth := m.width / 4
	if sidebarWidth < 30 {
		sidebarWidth = 30
	}
	contentWidth := m.width - sidebarWidth - 1

	// Viewport dimensions - account for reader header and metadata
	m.viewport.Width = contentWidth - 4   // Account for padding
	m.viewport.Height = contentHeight - 9 // Account for position, title+metadata, tags, divider

	// Try to extract reading_summary from Analysis JSON (often has richer content)
	var contentToShow string
	readingSummary := extractReadingSummary(item.Analysis)

	if readingSummary != "" {
		// Use the rich reading summary (may have markdown formatting)
		contentToShow = readingSummary
	} else if item.Content != "" {
		// Use the full article content
		contentToShow = item.Content
	} else if item.Summary != "" {
		// Fall back to summary
		contentToShow = item.Summary
	} else {
		contentToShow = "No content available for this article."
	}

	// Strip the title if it's the first line of markdown (to avoid double titles)
	lines := strings.Split(contentToShow, "\n")
	if len(lines) > 0 {
		firstLine := strings.TrimSpace(lines[0])
		// Check if first line is a markdown heading that matches the title
		if strings.HasPrefix(firstLine, "#") {
			// Remove the # marks and check if it matches the title
			titleFromContent := strings.TrimSpace(strings.TrimLeft(firstLine, "#"))
			if strings.EqualFold(titleFromContent, item.Title) ||
				strings.Contains(strings.ToLower(titleFromContent), strings.ToLower(item.Title)) ||
				strings.Contains(strings.ToLower(item.Title), strings.ToLower(titleFromContent)) {
				// Skip the first line (and possibly the blank line after it)
				if len(lines) > 1 {
					contentToShow = strings.Join(lines[1:], "\n")
					contentToShow = strings.TrimLeft(contentToShow, "\n")
				}
			}
		}
	}

	// Render our simple markdown format ourselves for proper wrapping
	contentToShow = renderSimpleMarkdown(contentToShow, m.viewport.Width)

	// Set the viewport content
	m.viewport.SetContent(contentToShow)

	// Reset viewport to top
	m.viewport.GotoTop()
}
