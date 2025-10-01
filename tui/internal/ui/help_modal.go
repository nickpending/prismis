package ui

import (
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
)

// HelpModal represents the help/keyboard shortcuts modal
type HelpModal struct {
	Modal  // Embed base modal
	width  int
	height int
}

// NewHelpModal creates a new HelpModal instance
func NewHelpModal() HelpModal {
	return HelpModal{
		Modal: NewModal("", 80, 30), // Will be sized dynamically
	}
}

// SetSize updates the modal size based on terminal dimensions
func (m *HelpModal) SetSize(width, height int) {
	// Calculate modal size - more conservative for help modal
	modalWidth := int(float64(width) * 0.75) // 75% instead of 85%
	modalHeight := height - 8

	// Minimum reasonable size
	if modalWidth < 50 {
		modalWidth = 50
	}
	if modalHeight < 20 {
		modalHeight = 20
	}

	// But don't exceed terminal size
	if modalWidth > width-4 {
		modalWidth = width - 4
	}

	m.width = modalWidth
	m.height = modalHeight
	m.Modal.width = modalWidth
	m.Modal.height = modalHeight
}

// Update handles input for the help modal
func (m HelpModal) Update(msg tea.Msg) (HelpModal, tea.Cmd) {
	if !m.visible {
		return m, nil
	}

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "esc", "q", "?":
			m.Hide()
			return m, nil
		}

	case tea.WindowSizeMsg:
		m.SetSize(msg.Width, msg.Height)
	}

	return m, nil
}

// View renders the help modal
func (m HelpModal) View(theme StyleTheme) string {
	if !m.visible {
		return ""
	}

	var content strings.Builder

	// Title with diamond bullet like in feed
	titleStyle := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true)

	title := "KEYBOARD SHORTCUTS"
	// Center the title
	titleWidth := lipgloss.Width(title)
	titlePadding := (m.width - 4 - titleWidth) / 2
	if titlePadding < 0 {
		titlePadding = 0
	}
	centeredTitle := strings.Repeat(" ", titlePadding) + title
	content.WriteString(titleStyle.Render(centeredTitle))
	content.WriteString("\n\n")

	// Add intro text about command mode
	introStyle := lipgloss.NewStyle().
		Foreground(theme.Gray).
		Italic(true)
	introText := "Press : to enter command mode. Use Tab to complete commands and fabric patterns."
	introPadding := (m.width - 4 - lipgloss.Width(introText)) / 2
	if introPadding < 0 {
		introPadding = 0
	}
	centeredIntro := strings.Repeat(" ", introPadding) + introText
	content.WriteString(introStyle.Render(centeredIntro))
	content.WriteString("\n\n")

	// Section header style (like in reader modal)
	sectionStyle := lipgloss.NewStyle().
		Foreground(theme.Cyan).
		Bold(true)

	// Command style
	keyStyle := lipgloss.NewStyle().
		Foreground(theme.Purple).
		Bold(true)

	descStyle := lipgloss.NewStyle().
		Foreground(theme.White)

	// Helper function to format a command row
	formatCmd := func(key, desc string) string {
		// Fixed width columns for alignment
		keyCol := keyStyle.Render(key)
		descCol := descStyle.Render(desc)
		// Pad key column to fixed width
		keyPadded := keyCol + strings.Repeat(" ", max(0, 12-lipgloss.Width(key)))
		return "  " + keyPadded + descCol
	}

	// Helper function to format two-column layout (if width permits)
	format2Col := func(key1, desc1, key2, desc2 string) string {
		// Only use two columns if we have enough width (>70 chars)
		if m.width > 70 {
			col1 := formatCmd(key1, desc1)
			// Calculate spacing for second column
			col1Width := lipgloss.Width(col1)
			spacing := max(2, (m.width/2)-col1Width)
			col2 := formatCmd(key2, desc2)
			return col1 + strings.Repeat(" ", spacing) + col2
		}
		// Fall back to single column for narrow terminals
		return formatCmd(key1, desc1) + "\n" + formatCmd(key2, desc2)
	}

	// Helper function to create section headers
	sectionHeader := func(title string) string {
		headerText := "── " + title + " "
		remainingWidth := m.width - 8 - lipgloss.Width(headerText)
		if remainingWidth < 0 {
			remainingWidth = 0
		}
		return sectionStyle.Render(headerText + strings.Repeat("─", remainingWidth))
	}

	// NAVIGATION section
	content.WriteString(sectionHeader("NAVIGATION"))
	content.WriteString("\n")
	content.WriteString(format2Col("j/k", "Move up/down", "g/G", "Jump to top/bottom"))
	content.WriteString("\n")
	content.WriteString(format2Col("Enter", "Read article", "q", "Quit/Back"))
	content.WriteString("\n")
	content.WriteString(format2Col(":", "Command mode", "?", "This help"))
	content.WriteString("\n")
	content.WriteString(format2Col("S", "Source manager", "", ""))
	content.WriteString("\n\n")

	// FILTERS & SORTING section
	content.WriteString(sectionHeader("FILTERS & SORTING"))
	content.WriteString("\n")
	content.WriteString(format2Col("1/2/3", "HIGH/MED/LOW", "0", "Unprioritized"))
	content.WriteString("\n")
	content.WriteString(format2Col("a", "Show all", "u", "Toggle unread"))
	content.WriteString("\n")
	content.WriteString(format2Col("d", "Toggle date sort", "s", "Cycle sources"))
	content.WriteString("\n\n")

	// ARTICLE COMMANDS section - Most used commands
	content.WriteString(sectionHeader("ARTICLE COMMANDS (:)"))
	content.WriteString("\n")
	content.WriteString(format2Col(":mark", "Toggle read", ":favorite", "Toggle star"))
	content.WriteString("\n")
	content.WriteString(format2Col(":open", "Open in browser", ":yank", "Copy URL"))
	content.WriteString("\n")
	content.WriteString(format2Col(":copy", "Copy content", ":fabric <pattern>", "AI analysis"))
	content.WriteString("\n")
	content.WriteString(format2Col(":report", "Daily report", "", ""))
	content.WriteString("\n\n")

	// SOURCE COMMANDS section
	content.WriteString(sectionHeader("SOURCE COMMANDS (:)"))
	content.WriteString("\n")
	content.WriteString(format2Col(":add <url>", "Add source", ":remove <id>", "Remove source"))
	content.WriteString("\n")
	content.WriteString(format2Col(":pause <url>", "Pause source", ":resume <url>", "Resume source"))
	content.WriteString("\n")
	content.WriteString(format2Col(":edit <id> <name>", "Rename source", "", ""))
	content.WriteString("\n\n")

	// MAINTENANCE COMMANDS section
	content.WriteString(sectionHeader("MAINTENANCE (:)"))
	content.WriteString("\n")
	content.WriteString(format2Col(":unprioritized", "Count unprioritized", ":prune", "Delete unprioritized"))
	content.WriteString("\n")
	content.WriteString(format2Col(":prune!", "Force delete", ":prune 7d", "Delete older than 7 days"))
	content.WriteString("\n\n")

	// READER MODE section - Simplified
	content.WriteString(sectionHeader("READER MODE"))
	content.WriteString("\n")
	content.WriteString(format2Col("j/k", "Scroll up/down", "h/l", "Prev/Next article"))
	content.WriteString("\n")
	content.WriteString(format2Col("Space", "Page down", "ESC/q", "Back to list"))
	content.WriteString("\n\n")

	// Footer hint
	footerStyle := lipgloss.NewStyle().
		Foreground(theme.Gray).
		Italic(true)
	footerText := "Press ESC or ? to close"
	footerPadding := (m.width - 4 - lipgloss.Width(footerText)) / 2
	if footerPadding < 0 {
		footerPadding = 0
	}
	centeredFooter := strings.Repeat(" ", footerPadding) + footerText
	content.WriteString(footerStyle.Render(centeredFooter))

	// Build the modal frame - matching other modals exactly
	modalStyle := lipgloss.NewStyle().
		Border(lipgloss.RoundedBorder()).
		BorderForeground(theme.Cyan). // Cyan border like other modals
		Width(m.width).
		Height(m.height).
		Padding(1, 2).
		Align(lipgloss.Left)

	return modalStyle.Render(content.String())
}

// ViewWithOverlay renders the modal over a dimmed background
func (m HelpModal) ViewWithOverlay(backgroundView string, width, height int, theme StyleTheme) string {
	if !m.visible {
		return backgroundView
	}

	// Get modal view
	modalView := m.View(theme)

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
