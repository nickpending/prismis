package ui

import (
	"encoding/json"
	"fmt"
	"strings"
)

// ContentMetadata represents the metadata extracted from analysis JSON
type ContentMetadata struct {
	Entities []string `json:"entities"`
	Quotes   []string `json:"quotes"`
	Tools    []string `json:"tools"`
	URLs     []string `json:"urls"`
}

// parseMetadata extracts metadata from the Analysis JSON field
func parseMetadata(analysisJSON string) ContentMetadata {
	if analysisJSON == "" {
		return ContentMetadata{}
	}

	var analysis map[string]interface{}
	if err := json.Unmarshal([]byte(analysisJSON), &analysis); err != nil {
		return ContentMetadata{}
	}

	metadata := ContentMetadata{}

	// Extract entities (these are the "topics")
	if entities, ok := analysis["entities"].([]interface{}); ok {
		for _, entity := range entities {
			if str, ok := entity.(string); ok {
				metadata.Entities = append(metadata.Entities, str)
			}
		}
	}

	// Extract quotes
	if quotes, ok := analysis["quotes"].([]interface{}); ok {
		for _, quote := range quotes {
			if str, ok := quote.(string); ok {
				metadata.Quotes = append(metadata.Quotes, str)
			}
		}
	}

	// Extract tools
	if tools, ok := analysis["tools"].([]interface{}); ok {
		for _, tool := range tools {
			if str, ok := tool.(string); ok {
				metadata.Tools = append(metadata.Tools, str)
			}
		}
	}

	// Extract URLs
	if urls, ok := analysis["urls"].([]interface{}); ok {
		for _, url := range urls {
			if str, ok := url.(string); ok {
				metadata.URLs = append(metadata.URLs, str)
			}
		}
	}

	return metadata
}

// injectQuotesIntoSummary inserts quotes section right before "## Takeaways"
func injectQuotesIntoSummary(readingSummary string, quotes []string) string {
	if len(quotes) == 0 {
		return readingSummary
	}

	// Replace "## Takeaways" with quotes section + "## Takeaways"
	quotesSection := "\n## Key Quotes\n\n"
	for i, quote := range quotes {
		quotesSection += fmt.Sprintf("> %s", quote)
		// Add blank line between quotes (but not after the last one)
		if i < len(quotes)-1 {
			quotesSection += "\n\n"
		} else {
			quotesSection += "\n"
		}
	}
	quotesSection += "\n## Takeaways"

	return strings.Replace(readingSummary, "## Takeaways", quotesSection, 1)
}

// renderMetadata formats metadata as markdown for proper styling (quotes now injected into reading summary)
func renderMetadata(metadata ContentMetadata, width int) string {
	// Only show tools and URLs - quotes are now injected into the reading summary
	if len(metadata.Tools) == 0 && len(metadata.URLs) == 0 {
		return ""
	}

	var sections []string

	// Add spacing before metadata starts
	sections = append(sections, "")
	sections = append(sections, "")

	// Tools as section
	if len(metadata.Tools) > 0 {
		sections = append(sections, "## Tools")
		for _, tool := range metadata.Tools {
			sections = append(sections, fmt.Sprintf("- %s", tool))
		}
		sections = append(sections, "")
	}

	// URLs as section
	if len(metadata.URLs) > 0 {
		sections = append(sections, "## Links")
		for i, url := range metadata.URLs {
			if i < 3 { // Show first 3
				sections = append(sections, fmt.Sprintf("- %s", url))
			} else if i == 3 && len(metadata.URLs) > 3 {
				sections = append(sections, fmt.Sprintf("- ... and %d more links", len(metadata.URLs)-3))
				break
			}
		}
		sections = append(sections, "")
	}

	return strings.Join(sections, "\n")
}


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

	// Parse metadata once for use throughout
	metadata := parseMetadata(item.Analysis)

	// Try to extract reading_summary from Analysis JSON (often has richer content)
	var contentToShow string
	readingSummary := extractReadingSummary(item.Analysis)

	if readingSummary != "" {
		// Inject quotes into reading summary after Key Points section
		contentToShow = injectQuotesIntoSummary(readingSummary, metadata.Quotes)
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

	// Append remaining metadata (tools/links) BEFORE markdown rendering
	metadataSection := renderMetadata(metadata, m.viewport.Width)
	if metadataSection != "" {
		contentToShow += metadataSection
	}

	// Render our simple markdown format ourselves for proper wrapping
	contentToShow = renderSimpleMarkdown(contentToShow, m.viewport.Width)

	// Set the viewport content
	m.viewport.SetContent(contentToShow)

	// Reset viewport to top
	m.viewport.GotoTop()
}
