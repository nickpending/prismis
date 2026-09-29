package ui

import (
	"fmt"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

// SourceModal represents the source management modal
type SourceModal struct {
	Modal    // Embed base modal
	sources  []db.Source
	cursor   int
	mode     string // "list", "add", "edit", "confirm_remove"
	errorMsg string

	// Form fields for add/edit modes - now using textinput.Model
	urlInput       textinput.Model // URL input field
	nameInput      textinput.Model // Name input field
	activeField    string          // Which field is currently being edited
	sourceToDelete string          // ID of source being deleted

	// Status message for temporary feedback (like main/reader modal)
	statusMessage string // Temporary status message to display

	// Viewport for scrolling content
	viewport viewport.Model
	ready    bool // Whether viewport is ready

	// Remote mode support
	remoteURL string // If non-empty, use API instead of local DB
}

// NewSourceModal creates a new SourceModal instance
func NewSourceModal() SourceModal {
	vp := viewport.New(0, 0)

	// Create URL input
	urlInput := textinput.New()
	urlInput.Placeholder = "https://example.com/feed.xml"
	urlInput.Width = 36
	urlInput.CharLimit = 512

	// Create name input
	nameInput := textinput.New()
	nameInput.Placeholder = "Optional display name"
	nameInput.Width = 36
	nameInput.CharLimit = 100

	return SourceModal{
		Modal:       NewModal("SOURCES", 45, 12),
		mode:        "list",
		urlInput:    urlInput,
		nameInput:   nameInput,
		activeField: "url", // Default to URL field
		viewport:    vp,
		ready:       false,
	}
}

// SetRemoteURL sets the remote URL for API-based source fetching
func (m *SourceModal) SetRemoteURL(url string) {
	m.remoteURL = url
}

// SetSize updates the modal size based on terminal dimensions
func (m *SourceModal) SetSize(width, height int) {
	// Small fixed size for source modal
	modalWidth := 45
	modalHeight := 12

	// Only adjust if terminal is really small
	if width < 50 {
		modalWidth = width - 5
	}
	if height < 15 {
		modalHeight = height - 3
	}

	m.width = modalWidth
	m.height = modalHeight
	m.Modal.width = modalWidth
	m.Modal.height = modalHeight

	// Update viewport size to fill the modal
	// Account for: border (2) + padding (2) + header (2) + status bar (1) = 7
	vpHeight := modalHeight - 7
	if vpHeight < 3 {
		vpHeight = 3
	}
	m.viewport.Width = modalWidth - 4 // Account for padding
	m.viewport.Height = vpHeight
	m.ready = true
}

// LoadSources updates the modal with fresh source data
func (m *SourceModal) LoadSources(sources []db.Source) {
	m.sources = sources
	// Reset cursor if it's out of bounds
	if m.cursor >= len(m.sources) && len(m.sources) > 0 {
		m.cursor = len(m.sources) - 1
	}
	// Update the modal content to reflect the new sources if visible and in list mode
	if m.visible && m.mode == "list" {
		m.UpdateContent()
	}
	// Ensure viewport is initialized if not ready
	if !m.ready && m.height > 0 {
		vpHeight := m.height - 7
		if vpHeight < 3 {
			vpHeight = 3
		}
		m.viewport.Width = m.width - 4
		m.viewport.Height = vpHeight
		m.ready = true
		m.UpdateContent()
	}
}

// Update handles input for the source modal
func (m SourceModal) Update(msg tea.Msg) (SourceModal, tea.Cmd) {
	if !m.visible {
		return m, nil
	}

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch m.mode {
		case "list":
			switch msg.String() {
			case "j", "down":
				if m.cursor < len(m.sources)-1 {
					m.cursor++
				}
			case "k", "up":
				if m.cursor > 0 {
					m.cursor--
				}
			case "a":
				m.mode = "add"
				// Reset textinput fields
				m.urlInput.SetValue("")
				m.nameInput.SetValue("")
				m.activeField = "url"
				m.urlInput.Focus()
				m.nameInput.Blur()
				m.errorMsg = ""
			case "enter":
				// Enter edits the selected source
				if len(m.sources) > 0 {
					m.mode = "edit"
					source := m.sources[m.cursor]
					// Set textinput values
					m.urlInput.SetValue(source.URL)
					m.nameInput.SetValue(source.Name)
					m.activeField = "url" // Start with URL field for consistency
					m.urlInput.Focus()
					m.nameInput.Blur()
					m.errorMsg = ""
				}
			case "p":
				// Toggle pause/resume for selected source
				if len(m.sources) > 0 && m.cursor < len(m.sources) {
					source := m.sources[m.cursor]
					if source.Active {
						return m, operations.PauseSource(source.ID)
					} else {
						return m, operations.ResumeSource(source.ID)
					}
				}
			case "d":
				if len(m.sources) > 0 && m.cursor < len(m.sources) {
					m.mode = "confirm_remove"
					m.sourceToDelete = m.sources[m.cursor].ID
					m.errorMsg = ""
				}
			case "esc", "q":
				m.Hide()
				m.mode = "list"
				m.errorMsg = ""
			}

		case "add":
			switch msg.String() {
			case "tab":
				m.handleFieldTab()
			case "enter":
				// Add source using textinput values
				url := strings.TrimSpace(m.urlInput.Value())
				if url == "" {
					m.errorMsg = "URL is required"
					return m, nil
				}

				name := strings.TrimSpace(m.nameInput.Value())
				return m, operations.AddSource(url, name)
			case "esc":
				m.clearFormAndReturnToList()
			default:
				// Let textinput handle all other keys (including paste!)
				return m, m.updateActiveTextInput(msg)
			}

		case "edit":
			switch msg.String() {
			case "tab":
				m.handleFieldTab()
			case "enter":
				// Prepare to update source
				if m.cursor >= len(m.sources) {
					m.errorMsg = "No source selected"
					return m, nil
				}

				source := m.sources[m.cursor]
				url := strings.TrimSpace(m.urlInput.Value())
				name := strings.TrimSpace(m.nameInput.Value())

				// Check if anything actually changed
				if url == source.URL && name == source.Name {
					// No changes made, just go back to list
					m.clearFormAndReturnToList()
					return m, nil
				}

				// Build updates map for the update command
				updates := map[string]interface{}{
					"url":  url,
					"type": source.Type, // Keep same type
				}
				if name != "" {
					updates["name"] = name
				}

				// Clear form and go back to list
				// The actual update will happen via the command
				m.clearFormAndReturnToList()

				// Update content before returning
				m.UpdateContent()

				// Return the update command directly (like add/remove/pause/resume)
				return m, operations.UpdateSource(source.ID, updates)
			case "esc":
				m.clearFormAndReturnToList()
			default:
				// Let textinput handle all other keys (including paste!)
				return m, m.updateActiveTextInput(msg)
			}

		case "confirm_remove":
			switch msg.String() {
			case "y":
				// Delete source
				if m.sourceToDelete == "" {
					m.errorMsg = "No source selected for deletion"
					m.mode = "list"
					return m, nil
				}

				// Use shared removal function
				return m, operations.RemoveSource(m.sourceToDelete)

			case "n", "esc":
				m.mode = "list"
				m.sourceToDelete = ""
				m.errorMsg = ""
			}
		}

	case operations.SourceOperationMsg:
		if msg.Success {
			// Success: return to list mode with status message
			m.statusMessage = msg.Message
			m.mode = "list"
			m.urlInput.SetValue("")
			m.nameInput.SetValue("")
			m.sourceToDelete = "" // Clear deletion state
			m.errorMsg = ""
			m.UpdateContent()
			return m, tea.Batch(
				fetchSources(m.remoteURL),
				tea.Tick(2*time.Second, func(t time.Time) tea.Msg {
					return clearStatusMsg{}
				}),
			)
		} else {
			// Error: show error and return to list mode
			m.errorMsg = msg.Message
			m.mode = "list"
			m.sourceToDelete = "" // Clear deletion state
			m.UpdateContent()
			return m, nil
		}

	case clearStatusMsg:
		m.statusMessage = ""
		// Update content to refresh status bar
		m.UpdateContent()
		return m, nil
	}

	// Update the modal content based on current mode
	m.UpdateContent()

	// Also update viewport if in list mode
	if m.mode == "list" {
		var cmd tea.Cmd
		m.viewport, cmd = m.viewport.Update(msg)
		return m, cmd
	}

	return m, nil
}

// UpdateContent refreshes the modal content based on current mode (exported for testing)
func (m *SourceModal) UpdateContent() {
	switch m.mode {
	case "list":
		// For list mode, update viewport content
		m.viewport.SetContent(m.renderListContentOnly())
	case "confirm_remove":
		m.SetContent(m.renderConfirmContentOnly())
	}
}

// handleFieldTab switches the active add/edit form field between "url" and "name",
// focusing the newly active textinput and blurring the other.
func (m *SourceModal) handleFieldTab() {
	if m.activeField == "url" {
		m.activeField = "name"
		m.urlInput.Blur()
		m.nameInput.Focus()
	} else {
		m.activeField = "url"
		m.nameInput.Blur()
		m.urlInput.Focus()
	}
}

// clearFormAndReturnToList resets the add/edit form fields and any error message,
// then returns to list mode. Callers that need to keep the form's values (e.g. to
// build an update command) must read them before calling this.
func (m *SourceModal) clearFormAndReturnToList() {
	m.mode = "list"
	m.urlInput.SetValue("")
	m.nameInput.SetValue("")
	m.urlInput.Blur()
	m.nameInput.Blur()
	m.errorMsg = ""
}

// updateActiveTextInput passes msg to whichever of the URL/name textinputs is
// currently active, letting it handle keys the add/edit form doesn't intercept
// itself (including paste).
func (m *SourceModal) updateActiveTextInput(msg tea.Msg) tea.Cmd {
	var cmd tea.Cmd
	if m.activeField == "url" {
		m.urlInput, cmd = m.urlInput.Update(msg)
	} else {
		m.nameInput, cmd = m.nameInput.Update(msg)
	}
	return cmd
}

// View renders the source modal
func (m SourceModal) View(theme StyleTheme) string {
	if !m.visible {
		return ""
	}

	var content strings.Builder

	// Title line (like reader modal)
	titleStyle := lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true)

	// Mode indicator
	var modeStr string
	switch m.mode {
	case "add":
		modeStr = "ADD SOURCE"
	case "edit":
		modeStr = "EDIT SOURCE"
	case "confirm_remove":
		modeStr = "CONFIRM REMOVAL"
	default:
		modeStr = "SOURCE MANAGEMENT"
	}

	titleText := titleStyle.Render(modeStr)

	// Count on right (for list mode)
	if m.mode == "list" && len(m.sources) > 0 {
		countText := fmt.Sprintf("%d sources", len(m.sources))
		countStyle := lipgloss.NewStyle().Foreground(theme.Gray)
		countRendered := countStyle.Render(countText)

		// Calculate spacing
		leftWidth := lipgloss.Width(titleText)
		rightWidth := lipgloss.Width(countRendered)
		spacing := m.width - 4 - leftWidth - rightWidth
		if spacing < 1 {
			spacing = 1
		}

		content.WriteString(titleText + strings.Repeat(" ", spacing) + countRendered)
	} else {
		content.WriteString(titleText)
	}
	content.WriteString("\n\n")

	// Get content based on mode
	var mainContentStr string

	if m.mode == "list" {
		// For list mode, use viewport
		content.WriteString(m.viewport.View())
		mainContentStr = content.String()
	} else {
		// For other modes, render content directly
		var mainContent string
		switch m.mode {
		case "add":
			mainContent = m.renderAddContentOnly()
		case "edit":
			mainContent = m.renderEditContentOnly()
		case "confirm_remove":
			mainContent = m.renderConfirmContentOnly()
		}
		content.WriteString(mainContent)
		mainContentStr = content.String()
	}

	// Create status bar
	var statusContent string
	if m.statusMessage != "" {
		// Show status message in cyan like main/reader modal
		statusContent = lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render(m.statusMessage)
	} else {
		// Show commands when no status message
		switch m.mode {
		case "list":
			statusContent = "[a]dd [↵] edit [d]elete [esc] close"
		case "add", "edit":
			statusContent = "[tab] switch [↵] save [esc] cancel"
		case "confirm_remove":
			statusContent = "[y] delete [n] cancel"
		}
	}

	statusStyle := lipgloss.NewStyle().
		Background(theme.DarkGray).
		Foreground(theme.Gray).
		Width(m.width-4). // Account for modal padding
		Padding(0, 1)

	statusBar := statusStyle.Render(statusContent)

	// Join content and status bar
	modalContent := lipgloss.JoinVertical(
		lipgloss.Left,
		mainContentStr,
		"\n",
		statusBar,
	)

	// Build the modal frame (like reader modal)
	modalStyle := modalFrameStyle(theme, m.width, m.height, lipgloss.Left)

	return modalStyle.Render(modalContent)
}

// renderListContentOnly renders just the list content without header/commands
func (m SourceModal) renderListContentOnly() string {
	theme := CleanCyberTheme
	var lines []string

	// Source list
	if len(m.sources) == 0 {
		noSourcesStyle := theme.MutedStyle().Italic(true)
		lines = append(lines, "", noSourcesStyle.Render("No sources configured"))
		lines = append(lines, "", theme.MutedStyle().Render("Press [a] to add your first source"))
	} else {
		for i, source := range m.sources {
			// Status indicator
			var status string
			if !source.Active {
				status = theme.ErrorStyle().Render("○") // Red - inactive
			} else if source.ErrorCount > 3 {
				status = lipgloss.NewStyle().Foreground(theme.Orange).Render("●") // Orange - errors
			} else {
				status = theme.SuccessStyle().Render("●") // Green - healthy
			}

			// Selection indicator
			selector := "  "
			if i == m.cursor {
				selector = lipgloss.NewStyle().Foreground(theme.Cyan).Render("▸ ")
			}

			// Format source type (right-aligned)
			typeStr := strings.ToUpper(source.Type)
			if i == m.cursor {
				typeStr = theme.TagStyle().Render(typeStr)
			} else {
				typeStr = theme.MutedStyle().Render(typeStr)
			}

			// Format unread count (right-aligned)
			var countStr string
			if source.UnreadCount > 0 {
				countStr = lipgloss.NewStyle().Foreground(theme.Cyan).Render(fmt.Sprintf("%d", source.UnreadCount))
			} else {
				countStr = theme.MutedStyle().Render("0")
			}

			// Create columnar layout: Title (25 chars) | Type (8 chars) | Count
			titleWidth := 25
			typeWidth := 8

			// Truncate title if needed and apply styling after truncation
			displayName := source.Name
			if len(displayName) > titleWidth {
				displayName = displayName[:titleWidth-3] + "..."
			}

			// Apply styling to the truncated name
			var nameStr string
			if i == m.cursor {
				nameStr = lipgloss.NewStyle().Foreground(theme.White).Bold(true).Render(displayName)
			} else {
				nameStr = theme.TextStyle().Render(displayName)
			}

			// Calculate padding based on actual string length (not styled width)
			titlePadding := titleWidth - len(displayName)
			if titlePadding < 0 {
				titlePadding = 0
			}
			typePadding := typeWidth - len(strings.ToUpper(source.Type))
			if typePadding < 0 {
				typePadding = 0
			}

			// Build columnar line
			line := fmt.Sprintf("%s%s %s%s %s%s %s",
				selector,
				status,
				nameStr,
				strings.Repeat(" ", titlePadding),
				typeStr,
				strings.Repeat(" ", typePadding),
				countStr,
			)

			lines = append(lines, line)
		}
	}

	lines = appendErrorLine(theme, lines, m.errorMsg)

	return strings.Join(lines, "\n")
}

// appendErrorLine appends the blank-line-then-rendered-error tail every content-only
// renderer ends with, when there is an error to show. A no-op (returns lines unchanged)
// when errorMsg is empty.
func appendErrorLine(theme StyleTheme, lines []string, errorMsg string) []string {
	if errorMsg != "" {
		lines = append(lines, "")
		lines = append(lines, theme.ErrorStyle().Render("⚠ "+errorMsg))
	}
	return lines
}

// renderAddContentOnly renders just the add form content
func (m SourceModal) renderAddContentOnly() string {
	theme := CleanCyberTheme
	var lines []string

	// URL field
	labelStyle := theme.TextStyle()
	lines = append(lines, labelStyle.Render("URL:"))
	lines = append(lines, m.urlInput.View())
	lines = append(lines, "")

	// Name field
	lines = append(lines, labelStyle.Render("Name (optional):"))
	lines = append(lines, m.nameInput.View())
	lines = append(lines, "")

	// Help text
	lines = append(lines, theme.MutedStyle().Render("Supported: RSS/Atom feeds, Reddit URLs, YouTube channels"))

	lines = appendErrorLine(theme, lines, m.errorMsg)

	return strings.Join(lines, "\n")
}

// renderEditContentOnly renders just the edit form content
func (m SourceModal) renderEditContentOnly() string {
	theme := CleanCyberTheme
	if m.cursor >= len(m.sources) {
		return "Invalid source selection"
	}

	var lines []string

	labelStyle := theme.TextStyle()

	// URL field (first - consistent with add form)
	lines = append(lines, labelStyle.Render("URL:"))
	lines = append(lines, m.urlInput.View())
	lines = append(lines, "")

	// Name field (second - consistent with add form)
	lines = append(lines, labelStyle.Render("Name:"))
	lines = append(lines, m.nameInput.View())

	lines = appendErrorLine(theme, lines, m.errorMsg)

	return strings.Join(lines, "\n")
}

// renderConfirmContentOnly renders just the confirmation content
func (m SourceModal) renderConfirmContentOnly() string {
	theme := CleanCyberTheme
	if m.cursor >= len(m.sources) {
		return "Invalid source selection"
	}

	source := m.sources[m.cursor]
	var lines []string

	nameStyle := lipgloss.NewStyle().Foreground(theme.White).Bold(true)

	lines = append(lines, fmt.Sprintf("Delete source: %s", nameStyle.Render(source.Name)))
	lines = append(lines, "")
	lines = append(lines, theme.MutedStyle().Render("This cannot be undone."))

	return strings.Join(lines, "\n")
}

// ViewWithOverlay renders the modal with a dimmed background overlay
func (m SourceModal) ViewWithOverlay(backgroundView string, termWidth, termHeight int, theme StyleTheme) string {
	if !m.visible {
		return backgroundView
	}

	// modalWidth is m.width (no +4): SourceModal builds its own layout and doesn't
	// add Modal's uniform border+padding. minStartY is 1 to not overlap the header.
	return overlayModal(backgroundView, m.View(theme), termWidth, termHeight, m.width, 1)
}
