package ui

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

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

// renderSimpleMarkdown renders our consistent markdown format with proper wrapping
func renderSimpleMarkdown(content string, width int) string {
	theme := CleanCyberTheme
	lines := strings.Split(content, "\n")
	var result []string

	skipLines := 0 // Track lines to skip when we've processed them

	for i, line := range lines {
		if skipLines > 0 {
			skipLines--
			continue
		}

		trimmed := strings.TrimSpace(line)

		// Handle headers (## Header -> ▸ Header in cyan)
		if strings.HasPrefix(trimmed, "## ") {
			headerText := strings.TrimPrefix(trimmed, "## ")

			// Special handling for Overview - put content in a box
			if headerText == "Overview" {
				// Add the header
				styled := lipgloss.NewStyle().
					Foreground(theme.Cyan).
					Bold(true).
					Render("▸ " + headerText)
				result = append(result, styled)

				// Collect overview content until next header or key points
				var overviewLines []string
				for j := i + 1; j < len(lines); j++ {
					nextLine := strings.TrimSpace(lines[j])
					if strings.HasPrefix(nextLine, "#") || strings.HasPrefix(nextLine, "-") || nextLine == "" {
						break
					}
					overviewLines = append(overviewLines, nextLine)
					skipLines++
				}

				// Box the overview content
				if len(overviewLines) > 0 {
					overviewText := strings.Join(overviewLines, " ")
					wrapped := wrapText(overviewText, width-6) // Account for box padding

					boxStyle := lipgloss.NewStyle().
						Border(lipgloss.RoundedBorder()).
						BorderForeground(theme.Purple).
						Padding(0, 1).
						Width(width - 2)

					boxed := boxStyle.Render(wrapped)
					result = append(result, boxed)
				}
			} else {
				styled := lipgloss.NewStyle().
					Foreground(theme.Cyan).
					Bold(true).
					Render("▸ " + headerText)
				result = append(result, styled)
				result = append(result, "") // Add space after header
			}
		} else if strings.HasPrefix(trimmed, "# ") {
			// Bigger headers
			headerText := strings.TrimPrefix(trimmed, "# ")

			// Also handle # Overview here in case it was converted
			if headerText == "Overview" {
				// Add the header
				styled := lipgloss.NewStyle().
					Foreground(theme.Cyan).
					Bold(true).
					Render("▸ " + headerText)
				result = append(result, styled)

				// Collect overview content until next header or key points
				var overviewLines []string
				for j := i + 1; j < len(lines); j++ {
					nextLine := strings.TrimSpace(lines[j])
					if strings.HasPrefix(nextLine, "#") || strings.HasPrefix(nextLine, "-") || nextLine == "" {
						break
					}
					overviewLines = append(overviewLines, nextLine)
					skipLines++
				}

				// Box the overview content
				if len(overviewLines) > 0 {
					overviewText := strings.Join(overviewLines, " ")
					wrapped := wrapText(overviewText, width-6) // Account for box padding

					boxStyle := lipgloss.NewStyle().
						Border(lipgloss.RoundedBorder()).
						BorderForeground(theme.Purple).
						Padding(0, 1).
						Width(width - 2)

					boxed := boxStyle.Render(wrapped)
					result = append(result, boxed)
				}
			} else {
				styled := lipgloss.NewStyle().
					Foreground(theme.Cyan).
					Bold(true).
					Render("▸ " + headerText)
				result = append(result, styled)
				result = append(result, "") // Add space after header
			}
		} else if strings.HasPrefix(trimmed, "> ") {
			// Handle quotes with indentation and color
			quoteText := strings.TrimPrefix(trimmed, "> ")
			// Remove the outer quotes if present
			quoteText = strings.Trim(quoteText, "\"")

			// Wrap the quote text with indentation
			wrapped := wrapTextWithPrefix(quoteText, width-4, "  │ ", "  │ ")
			// Style it with gray/dim color
			for _, wline := range strings.Split(wrapped, "\n") {
				styled := lipgloss.NewStyle().
					Foreground(theme.Gray).
					Render(wline)
				result = append(result, styled)
			}
		} else if strings.HasPrefix(trimmed, "- ") {
			// Handle list items with proper wrapping - cyberpunk style
			itemText := strings.TrimPrefix(trimmed, "- ")
			// Use diamond bullets for cyber look
			wrapped := wrapTextWithPrefix(itemText, width-4, "  ◆ ", "    ")
			// Add cyan color to the bullet
			lines := strings.Split(wrapped, "\n")
			if len(lines) > 0 {
				// Color just the bullet part
				if strings.HasPrefix(lines[0], "  ◆ ") {
					bulletPart := lipgloss.NewStyle().
						Foreground(theme.Cyan).
						Render("  ◆")
					textPart := strings.TrimPrefix(lines[0], "  ◆")
					lines[0] = bulletPart + textPart
				}
				wrapped = strings.Join(lines, "\n")
			}
			result = append(result, wrapped)
		} else if trimmed == "" {
			// Empty line
			result = append(result, "")
		} else {
			// Regular paragraph - just wrap it properly with indentation
			// Strip any bold markers for cleaner display
			cleaned := strings.ReplaceAll(trimmed, "**", "")
			wrapped := wrapText(cleaned, width-2) // Reduce width for indent

			// Add indent to each line
			lines := strings.Split(wrapped, "\n")
			for _, wline := range lines {
				if wline != "" {
					result = append(result, "  "+wline) // 2-space indent
				} else {
					result = append(result, "")
				}
			}
		}
	}

	return strings.Join(result, "\n")
}

// wrapTextWithPrefix wraps text with different prefixes for first and continuation lines
func wrapTextWithPrefix(text string, width int, firstPrefix, contPrefix string) string {
	if width <= 0 {
		return firstPrefix + text
	}

	// Calculate available width after prefixes
	firstWidth := width - len(firstPrefix)
	contWidth := width - len(contPrefix)

	words := strings.Fields(text)
	if len(words) == 0 {
		return firstPrefix
	}

	var lines []string
	var currentLine strings.Builder
	currentLine.WriteString(firstPrefix)

	lineWidth := firstWidth
	isFirstLine := true

	for _, word := range words {
		wordLen := len(word)
		currentLen := currentLine.Len() - len(firstPrefix)
		if !isFirstLine {
			currentLen = currentLine.Len() - len(contPrefix)
		}

		// Check if adding this word would exceed width
		needSpace := currentLen > 0
		spaceLen := 0
		if needSpace {
			spaceLen = 1
		}

		if currentLen+spaceLen+wordLen > lineWidth {
			// Start new line
			if currentLine.Len() > 0 {
				lines = append(lines, currentLine.String())
				currentLine.Reset()
				currentLine.WriteString(contPrefix)
				isFirstLine = false
				lineWidth = contWidth
			}
			currentLine.WriteString(word)
		} else {
			// Add to current line
			if needSpace {
				currentLine.WriteString(" ")
			}
			currentLine.WriteString(word)
		}
	}

	// Add remaining line
	if currentLine.Len() > 0 {
		lines = append(lines, currentLine.String())
	}

	return strings.Join(lines, "\n")
}
