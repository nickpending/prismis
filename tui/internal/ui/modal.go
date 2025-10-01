package ui

import (
	"strings"

	"github.com/charmbracelet/lipgloss"
)

// Modal represents a generic modal overlay component
type Modal struct {
	title   string
	width   int
	height  int
	content string
	visible bool
}

// NewModal creates a new Modal instance
func NewModal(title string, width, height int) Modal {
	return Modal{
		title:   title,
		width:   width,
		height:  height,
		visible: false,
	}
}

// Show makes the modal visible
func (m *Modal) Show() {
	m.visible = true
}

// Hide makes the modal invisible
func (m *Modal) Hide() {
	m.visible = false
}

// IsVisible returns whether the modal is currently visible
func (m Modal) IsVisible() bool {
	return m.visible
}

// SetContent updates the modal content
func (m *Modal) SetContent(content string) {
	m.content = content
}

// View renders the modal if visible
func (m Modal) View(theme StyleTheme) string {
	if !m.visible {
		return ""
	}

	// Create modal style with border and colors
	modalStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(theme.Cyan).
		Width(m.width).
		Height(m.height).
		Padding(1, 2).
		Align(lipgloss.Center)

	// Title style
	titleStyle := lipgloss.NewStyle().
		Bold(true).
		Foreground(theme.Cyan).
		MarginBottom(1)

	// Combine title and content
	var fullContent strings.Builder
	if m.title != "" {
		fullContent.WriteString(titleStyle.Render(m.title))
		fullContent.WriteString("\n")
	}
	fullContent.WriteString(m.content)

	return modalStyle.Render(fullContent.String())
}

// ViewWithOverlay renders the modal with a dimmed background overlay
func (m Modal) ViewWithOverlay(backgroundView string, termWidth, termHeight int, theme StyleTheme) string {
	if !m.visible {
		return backgroundView
	}

	// Split background into lines
	bgLines := strings.Split(backgroundView, "\n")

	// Keep the first line (header) undimmed, clear everything else
	for i := range bgLines {
		if i == 0 {
			// Keep the header line as-is (PRISMIS gradient bar)
			continue
		} else {
			// Replace all other lines with empty space
			bgLines[i] = strings.Repeat(" ", termWidth)
		}
	}

	// Rejoin dimmed background
	dimmedBg := strings.Join(bgLines, "\n")

	// Get modal view
	modalView := m.View(theme)
	if modalView == "" {
		return dimmedBg
	}

	// Calculate position to center modal
	modalLines := strings.Split(modalView, "\n")
	modalHeight := len(modalLines)
	modalWidth := m.width + 4 // Account for border and padding

	// Calculate starting positions
	startY := modalMax(0, (termHeight-modalHeight)/2)
	startX := modalMax(0, (termWidth-modalWidth)/2)

	// Split background and modal into lines for overlay
	bgLinesArray := strings.Split(dimmedBg, "\n")
	modalLinesArray := strings.Split(modalView, "\n")

	// Overlay modal on background
	result := make([]string, modalMax(len(bgLinesArray), startY+len(modalLinesArray)))
	copy(result, bgLinesArray)

	// Place modal lines at the calculated position
	for i, modalLine := range modalLinesArray {
		lineIdx := startY + i
		if lineIdx < len(result) {
			// For simplicity, replace the entire line with modal content
			// In a real implementation, you'd overlay character by character
			padding := strings.Repeat(" ", startX)
			result[lineIdx] = padding + modalLine
		}
	}

	return strings.Join(result, "\n")
}

// modalMax returns the maximum of two integers (renamed to avoid conflict)
func modalMax(a, b int) int {
	if a > b {
		return a
	}
	return b
}
