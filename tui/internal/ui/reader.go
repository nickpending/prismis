package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
)

// RenderReader renders the reader view with clean cyber styling
func RenderReader(m Model) string {
	if m.width == 0 {
		return "Loading..."
	}

	if m.cursor >= len(m.items) {
		return "No content selected\n\nPress Esc to return"
	}

	theme := CleanCyberTheme
	item := m.items[m.cursor]

	// Header bar (same style as main view)
	headerStyle := lipgloss.NewStyle().
		Background(theme.DarkGray).
		Foreground(theme.Cyan).
		Width(m.width).
		Padding(0, 1)

	// Count stats for navigation
	var highCount, medCount, lowCount int
	for _, i := range m.items {
		switch i.Priority {
		case "high":
			highCount++
		case "medium":
			medCount++
		case "low":
			lowCount++
		}
	}

	header := headerStyle.Render(fmt.Sprintf(
		"▼ prismis  [%d]  ● %d HIGH  ● %d MED  ● %d LOW  ◆ %s",
		len(m.items),
		highCount,
		medCount,
		lowCount,
		time.Now().Format("15:04"),
	))

	// Main content area
	contentHeight := m.height - 5 // header + empty line + status + borders
	contentWidth := m.width

	// Reader content style
	readerStyle := lipgloss.NewStyle().
		Width(contentWidth).
		Height(contentHeight).
		Padding(0, 2)

	// Article header
	articleHeader := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true).
		Render(fmt.Sprintf("◄ ARTICLE %d of %d", m.cursor+1, len(m.items)))

	// Title with priority indicator
	var priorityDot string
	switch item.Priority {
	case "high":
		priorityDot = lipgloss.NewStyle().Foreground(theme.Red).Render("●")
	case "medium":
		priorityDot = lipgloss.NewStyle().Foreground(theme.Orange).Render("●")
	case "low":
		priorityDot = lipgloss.NewStyle().Foreground(theme.Gray).Render("●")
	}

	title := fmt.Sprintf("%s %s",
		priorityDot,
		lipgloss.NewStyle().Foreground(theme.White).Bold(true).Render(item.Title))

	// Metadata line (without URL - we'll show it separately)
	timeAgo := formatTime(time.Since(item.Published))
	sourceInfo := extractDomain(item.URL)

	metaStyle := lipgloss.NewStyle().Foreground(theme.Gray)

	// Build metadata parts
	metaParts := []string{
		metaStyle.Render(sourceInfo),
		metaStyle.Render(timeAgo),
	}

	// Add Reddit metrics if available
	if item.SourceType == "reddit" {
		redditMetrics := extractRedditMetrics(item.Analysis)
		if redditMetrics.score > 0 {
			metaParts = append(metaParts, lipgloss.NewStyle().Foreground(theme.Orange).Render(fmt.Sprintf("↑%d", redditMetrics.score)))
		}
		if redditMetrics.numComments > 0 {
			metaParts = append(metaParts, metaStyle.Render(fmt.Sprintf("%dc", redditMetrics.numComments)))
		}
	}

	// Add YouTube metrics if available
	if item.SourceType == "youtube" {
		youtubeMetrics := extractYouTubeMetrics(item.Analysis)
		if youtubeMetrics.viewCount > 0 {
			var viewStr string
			if youtubeMetrics.viewCount >= 1000000 {
				viewStr = fmt.Sprintf("%.1fM views", float64(youtubeMetrics.viewCount)/1000000)
			} else if youtubeMetrics.viewCount >= 1000 {
				viewStr = fmt.Sprintf("%.1fK views", float64(youtubeMetrics.viewCount)/1000)
			} else {
				viewStr = fmt.Sprintf("%d views", youtubeMetrics.viewCount)
			}
			metaParts = append(metaParts, metaStyle.Render(viewStr))
		}
		if youtubeMetrics.duration > 0 {
			metaParts = append(metaParts, metaStyle.Render(formatDurationMinutes(youtubeMetrics.duration)))
		}
	}

	metaLine := strings.Join(metaParts, " | ")

	// Task 7.3: Add full URL line (separate from metadata)
	// Helper function to truncate URL if needed
	truncateURL := func(url string, maxWidth int) string {
		if len(url) <= maxWidth {
			return url
		}
		if maxWidth <= 3 {
			return url[:maxWidth]
		}
		return url[:maxWidth-3] + "..."
	}

	urlLine := lipgloss.NewStyle().
		Foreground(theme.Gray).
		Render(fmt.Sprintf("URL: %s", truncateURL(item.URL, contentWidth-8)))

	// Summary section
	summaryTitle := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true).
		MarginTop(2).
		Render("── SUMMARY " + strings.Repeat("─", contentWidth-15))

	summaryText := lipgloss.NewStyle().
		Foreground(theme.White).
		Width(contentWidth - 4).
		MarginTop(1).
		Render(wrapText(item.Summary, contentWidth-4))

	// Content section - using viewport content
	contentTitle := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true).
		MarginTop(2).
		Render("── CONTENT " + strings.Repeat("─", contentWidth-15))

	// Use viewport for scrollable content
	contentText := m.viewport.View()

	// Build the complete content (including URL line from task 7.3)
	content := lipgloss.JoinVertical(
		lipgloss.Left,
		articleHeader,
		"",
		title,
		metaLine,
		urlLine, // Task 7.3: Full URL shown separately
		summaryTitle,
		summaryText,
		contentTitle,
		contentText,
	)

	main := readerStyle.Render(content)

	// Task 7.4: Use the enhanced status bar
	status := renderReaderStatus(m)

	return lipgloss.JoinVertical(
		lipgloss.Left,
		header,
		"",
		main,
		status,
	)
}

// renderReaderStatus renders the enhanced reader status bar with position and commands
func renderReaderStatus(m Model) string {
	theme := CleanCyberTheme

	// Article position (Task 7.2: position calculator)
	var position string
	if len(m.items) == 0 {
		position = "NO ARTICLES"
	} else {
		position = fmt.Sprintf("ARTICLE %d of %d", m.cursor+1, len(m.items))
	}

	// Commands list matching the existing shortcuts
	commands := []string{
		"[o]pen browser",
		"[c]opy content",
		"[y]ank URL",
		"[m]ark read",
		"[f]avorite",
		"[j/k] prev/next",
		"[ESC] back",
	}

	// Style the position (left side)
	positionStyle := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true)
	statusLeft := positionStyle.Render(position)

	// Style the commands (right side)
	commandStyle := lipgloss.NewStyle().
		Foreground(theme.Gray)
	statusRight := commandStyle.Render(strings.Join(commands, "  "))

	// Calculate spacing to align left and right
	leftWidth := lipgloss.Width(statusLeft)
	rightWidth := lipgloss.Width(statusRight)
	spacing := m.width - leftWidth - rightWidth - 2 // -2 for padding
	if spacing < 1 {
		spacing = 1
	}

	// Combine with proper spacing
	statusContent := statusLeft + strings.Repeat(" ", spacing) + statusRight

	// Apply the status bar background style
	statusStyle := lipgloss.NewStyle().
		Background(theme.DarkGray).
		Foreground(theme.Gray).
		Width(m.width).
		Padding(0, 1)

	// Show temporary status message if present
	if m.statusMessage != "" {
		// Override with status message temporarily
		messageStyle := lipgloss.NewStyle().
			Background(theme.DarkGray).
			Foreground(theme.Cyan).
			Width(m.width).
			Padding(0, 1).
			Align(lipgloss.Center)
		return messageStyle.Render(m.statusMessage)
	}

	return statusStyle.Render(statusContent)
}

// wrapText wraps text to fit within the specified width
func wrapText(text string, width int) string {
	if width <= 0 {
		return text
	}

	words := strings.Fields(text)
	if len(words) == 0 {
		return ""
	}

	var lines []string
	var currentLine strings.Builder

	for _, word := range words {
		// Check if adding this word would exceed the width
		potentialLength := currentLine.Len() + len(word)
		if currentLine.Len() > 0 {
			potentialLength++ // Account for space
		}

		if potentialLength > width && currentLine.Len() > 0 {
			// Start a new line
			lines = append(lines, currentLine.String())
			currentLine.Reset()
		}

		// Add space if not at the beginning of a line
		if currentLine.Len() > 0 {
			currentLine.WriteString(" ")
		}
		currentLine.WriteString(word)
	}

	// Add the last line if it has content
	if currentLine.Len() > 0 {
		lines = append(lines, currentLine.String())
	}

	return strings.Join(lines, "\n")
}
