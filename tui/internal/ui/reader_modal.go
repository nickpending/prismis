package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/nickpending/prismis-local/internal/db"
)

// readerStatusMsg is sent to the main model to display status messages
type readerStatusMsg struct {
	message string
}

// ReaderModal represents the article reader modal
type ReaderModal struct {
	Modal             // Embed base modal
	items             []db.ContentItem
	cursor            int // Current article index (exported for access from Model)
	viewport          viewport.Model
	ready             bool
	width             int
	height            int
	flashActive       bool   // Whether to show flash effect
	mainStatusMessage string // Status message from main model
}

// NewReaderModal creates a new ReaderModal instance
func NewReaderModal() ReaderModal {
	vp := viewport.New(0, 0)
	return ReaderModal{
		Modal:    NewModal("", 90, 30), // 90% width, 30 lines height
		viewport: vp,
		ready:    false,
	}
}

// SetItems sets the items and cursor position for the reader
func (m *ReaderModal) SetItems(items []db.ContentItem, cursor int) {
	m.items = items
	m.cursor = cursor
	// Update content immediately when items are set
	m.UpdateContent()
}

// SetSize updates the modal size based on terminal dimensions
func (m *ReaderModal) SetSize(width, height int) {
	// Calculate modal size - smaller to show header/footer
	modalWidth := int(float64(width) * 0.85) // 85% width instead of 90%
	modalHeight := height - 8                // Leave more room (was 6)

	if modalWidth < 60 {
		modalWidth = 60
	}
	if modalHeight < 15 {
		modalHeight = 15
	}

	m.width = modalWidth
	m.height = modalHeight
	m.Modal.width = modalWidth
	m.Modal.height = modalHeight

	// Update viewport size for content area
	// Account for modal chrome and reader header
	vpHeight := modalHeight - 8 // Reduced from 12 - just account for header, metadata, status bar
	if vpHeight < 5 {
		vpHeight = 5
	}
	m.viewport.Width = modalWidth - 4 // Account for padding
	m.viewport.Height = vpHeight
	m.ready = true
}

// Update handles input for the reader modal
func (m *ReaderModal) Update(msg tea.Msg) (ReaderModal, tea.Cmd) {
	if !m.visible {
		return *m, nil
	}

	var cmd tea.Cmd

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "esc", "q":
			m.Hide()
			return *m, nil

		case "l", "right":
			// Next article (right arrow or 'l')
			if m.cursor < len(m.items)-1 {
				m.cursor++
				m.UpdateContent() // Update content immediately
			}

		case "h", "left":
			// Previous article (left arrow or 'h')
			if m.cursor > 0 {
				m.cursor--
				m.UpdateContent() // Update content immediately
			}

		case "j", "down":
			// Scroll content down
			m.viewport.LineDown(1)

		case "k", "up":
			// Scroll content up
			m.viewport.LineUp(1)

		case "y":
			// Yank URL - handled by main model
			return *m, nil

		case "c":
			// Copy content - handled by main model
			return *m, nil

		case "o":
			// Open in browser - would trigger browser open
			return *m, nil

		case "m":
			// Toggle read/unread status - handled by main model
			return *m, nil

		case "f":
			// Toggle favorite status - handled by main model
			return *m, nil

		case "pgup":
			m.viewport.ViewUp()

		case "pgdown", "space":
			m.viewport.ViewDown()

		case "home":
			m.viewport.GotoTop()

		case "end":
			m.viewport.GotoBottom()

		default:
			// Handle viewport scrolling with arrow keys
			m.viewport, cmd = m.viewport.Update(msg)
		}

	case clearStatusMsg:
		m.mainStatusMessage = ""

	case tea.WindowSizeMsg:
		m.SetSize(msg.Width, msg.Height)
	}

	return *m, cmd
}

// UpdateContent updates the modal content with current article
func (m *ReaderModal) UpdateContent() {
	if m.cursor >= len(m.items) || len(m.items) == 0 {
		m.SetContent("No content selected")
		return
	}

	item := m.items[m.cursor]

	// Viewport dimensions should already be set by SetSize()
	// Only set if not initialized (safety check)
	if m.viewport.Width <= 0 {
		m.viewport.Width = m.width - 4 // Account for modal padding
	}
	if m.viewport.Height <= 0 {
		m.viewport.Height = m.height - 12
	}

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
	// (Overview handling is now done inside renderSimpleMarkdown)
	contentToShow = renderSimpleMarkdown(contentToShow, m.viewport.Width)

	// Set the viewport content
	m.viewport.SetContent(contentToShow)

	// Reset viewport to top
	m.viewport.GotoTop()
}

// View renders the reader modal
func (m ReaderModal) View() string {
	if !m.visible {
		return ""
	}

	// Debug: Check what's preventing display
	if !m.ready {
		// Modal is visible but not ready - still show something
		modalStyle := lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#FF0000")).
			Width(60).
			Height(20).
			Padding(1, 2)
		return modalStyle.Render("Modal not ready - size not set")
	}

	if m.cursor >= len(m.items) || len(m.items) == 0 {
		modalStyle := lipgloss.NewStyle().
			Border(lipgloss.RoundedBorder()).
			BorderForeground(lipgloss.Color("#FF0000")).
			Width(m.width).
			Height(m.height).
			Padding(1, 2)
		return modalStyle.Render(fmt.Sprintf("No items: cursor=%d, items=%d", m.cursor, len(m.items)))
	}

	theme := CleanCyberTheme
	item := m.items[m.cursor]

	var content strings.Builder

	// Priority dot or star for favorited items
	var priorityDot string
	var dotColor lipgloss.Color
	
	if item.Favorited {
		// Show heart for favorited items instead of priority dot - gradient purple
		priorityDot = "♥"
		dotColor = lipgloss.Color("#9F4DFF") // Gradient purple for favorites
	} else {
		// Show priority dot for non-favorited items
		switch item.Priority {
		case "high":
			priorityDot = "●"
			dotColor = theme.Red
		case "medium":
			priorityDot = "●"
			dotColor = theme.Orange
		case "low":
			priorityDot = "●"
			dotColor = theme.Cyan
		default:
			priorityDot = "●"
			dotColor = theme.Gray
		}
	}

	priorityDotRendered := lipgloss.NewStyle().Foreground(dotColor).Render(priorityDot)

	// Title (no flash effect on title)
	titleStyle := lipgloss.NewStyle().Foreground(theme.White).Bold(true)
	titleText := titleStyle.Render(item.Title)

	// Position text on right
	positionText := fmt.Sprintf("ARTICLE %d of %d", m.cursor+1, len(m.items))
	positionStyle := lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true)
	positionRendered := positionStyle.Render(positionText)

	// Calculate spacing to align position to the right
	// Account for priority dot + space + title
	leftSide := priorityDotRendered + " " + titleText
	leftWidth := lipgloss.Width(leftSide)
	positionWidth := lipgloss.Width(positionRendered)
	spacing := m.width - 4 - leftWidth - positionWidth // -4 for modal padding
	if spacing < 1 {
		spacing = 1
	}

	// Build the title line with priority dot, title, and position
	titleLine := leftSide + strings.Repeat(" ", spacing) + positionRendered
	content.WriteString(titleLine)
	content.WriteString("\n")

	// Build metadata like list view
	timeAgo := formatTime(time.Since(item.Published))
	metaStyle := lipgloss.NewStyle().Foreground(theme.Gray)

	// No priority dot in metadata since it's now with the title
	metaParts := []string{
		metaStyle.Render(item.SourceType), // Show source type (reddit, rss, youtube)
	}

	// Add source name if available (like subreddit name)
	if item.SourceName != "" {
		metaParts = append(metaParts, metaStyle.Render(item.SourceName))
	}

	// For RSS feeds, also show the domain
	if item.SourceType == "rss" {
		domain := extractDomain(item.URL)
		metaParts = append(metaParts, metaStyle.Render(domain))
	}

	// Time ago
	metaParts = append(metaParts, metaStyle.Render(timeAgo))

	// Add source-specific metrics
	if item.SourceType == "reddit" {
		redditMetrics := extractRedditMetrics(item.Analysis)
		if redditMetrics.score > 0 {
			metaParts = append(metaParts,
				lipgloss.NewStyle().Foreground(theme.Orange).Render(fmt.Sprintf("↑%d", redditMetrics.score)))
		}
		if redditMetrics.numComments > 0 {
			metaParts = append(metaParts, metaStyle.Render(fmt.Sprintf("%dc", redditMetrics.numComments)))
		}
	}

	// Add ALL tags at the end (reader has room to show them all)
	tags := extractAllTags(item.Analysis)
	if tags != "" {
		metaParts = append(metaParts, tags)
	}

	content.WriteString(strings.Join(metaParts, " | "))
	content.WriteString("\n\n")

	// Check what content we have to show
	readingSummary := extractReadingSummary(item.Analysis)
	hasRichContent := readingSummary != ""

	// Show summary section only if we have it and it's different from main content
	if item.Summary != "" && !hasRichContent {
		summaryTitle := lipgloss.NewStyle().
			Foreground(theme.Cyan).
			Bold(true).
			Render("── SUMMARY " + strings.Repeat("─", max(0, m.width-20)))
		content.WriteString(summaryTitle)
		content.WriteString("\n")
		content.WriteString(wrapText(item.Summary, m.width-4))
		content.WriteString("\n\n")
	}

	// The viewport already has the content from UpdateContent()
	// No need for a section divider - just show the content
	content.WriteString(m.viewport.View())

	// Build the modal content without the status bar
	mainContent := content.String()

	// Create status bar styled like list view
	statusStyle := lipgloss.NewStyle().
		Background(theme.DarkGray).
		Foreground(theme.Gray).
		Width(m.width-4). // Account for modal padding
		Padding(0, 1)

	// Show status message if present, otherwise show commands
	var statusContent string
	if m.mainStatusMessage != "" {
		// Always show status message in cyan (like main view) - NO PURPLE FLASH
		statusContent = lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render(m.mainStatusMessage)
	} else {
		statusContent = "[←→/h/l] prev/next  [↑↓/j/k] scroll  [y]ank URL  [c]opy  [o]pen  [m]ark read  [ESC] close"
	}
	statusBar := statusStyle.Render(statusContent)

	// Join content and status bar
	modalContent := lipgloss.JoinVertical(
		lipgloss.Left,
		mainContent,
		"\n",
		statusBar,
	)

	// Build the modal frame
	modalStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(lipgloss.Color("#00D9FF")).
		Width(m.width).
		Height(m.height).
		Padding(1, 2).
		Align(lipgloss.Left)

	return modalStyle.Render(modalContent)
}

// ViewWithOverlay renders the modal over a dimmed background
func (m ReaderModal) ViewWithOverlay(backgroundView string, width, height int) string {
	if !m.visible {
		return backgroundView
	}

	// Get OUR modal view (not the base Modal's view)
	modalView := m.View()

	// Split background into lines
	bgLines := strings.Split(backgroundView, "\n")

	// Keep the first line (header) undimmed, clear everything else
	for i := range bgLines {
		if i == 0 {
			// Keep the header line as-is (PRISMIS gradient bar)
			continue
		} else {
			// Replace all other lines with empty space
			bgLines[i] = strings.Repeat(" ", width)
		}
	}

	// Rejoin the modified background
	dimmedBg := strings.Join(bgLines, "\n")

	// Calculate position to center modal
	modalLines := strings.Split(modalView, "\n")
	modalHeight := len(modalLines)
	modalWidth := m.width + 4 // Account for border and padding

	startY := max(0, (height-modalHeight)/2)
	startX := max(0, (width-modalWidth)/2)

	// Overlay modal on background
	bgLinesArray := strings.Split(dimmedBg, "\n")
	result := make([]string, max(len(bgLinesArray), startY+len(modalLines)))
	copy(result, bgLinesArray)

	// Place modal lines at the calculated position
	for i, modalLine := range modalLines {
		lineIdx := startY + i
		if lineIdx < len(result) {
			padding := strings.Repeat(" ", startX)
			result[lineIdx] = padding + modalLine
		}
	}

	return strings.Join(result, "\n")
}

// Helper function to truncate strings
func truncateString(s string, maxLen int) string {
	if len(s) <= maxLen {
		return s
	}
	if maxLen <= 3 {
		return s[:maxLen]
	}
	return s[:maxLen-3] + "..."
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

