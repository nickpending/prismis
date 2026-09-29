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
	modalStyle := modalFrameStyle(theme, m.width, m.height, lipgloss.Center)

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

	// Account for border and padding
	return overlayModal(backgroundView, m.View(theme), termWidth, termHeight, m.width+4, 0)
}

// modalFrameStyle builds the border/padding frame both Modal.View and SourceModal.View
// render their content into - width, height and content alignment are the only things
// that vary per caller (Modal centers its content, SourceModal left-aligns its own layout).
func modalFrameStyle(theme StyleTheme, width, height int, align lipgloss.Position) lipgloss.Style {
	return lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(theme.Cyan).
		Width(width).
		Height(height).
		Padding(1, 2).
		Align(align)
}

// overlayModal composes an already-rendered modal view onto a dimmed background, centered
// on termWidth/termHeight. modalWidth and minStartY are passed in rather than derived here
// because each modal type has its own layout convention: Modal's own View() adds a uniform
// border+padding it accounts for in modalWidth (m.width+4) and allows the modal to start at
// line 0, while SourceModal builds its own layout (modalWidth = m.width, no +4) and floors
// its start row at 1 so it doesn't overlap the header. If modalView is empty, the dimmed
// background is returned with nothing overlaid.
func overlayModal(backgroundView, modalView string, termWidth, termHeight, modalWidth, minStartY int) string {
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

	if modalView == "" {
		return dimmedBg
	}

	// Calculate position to center modal
	modalLines := strings.Split(modalView, "\n")
	modalHeight := len(modalLines)

	// Calculate starting positions
	startY := modalMax(minStartY, (termHeight-modalHeight)/2)
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
