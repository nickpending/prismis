package ui

import (
	"fmt"
	"slices"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

// naughtyMarker follows the name of a source the daemon fetches with a browser
// user-agent (`prismis-cli source update <id> --naughty` sets it).
const naughtyMarker = " [browser UA]"

// sourceMark is the one status mark for a source, read from the daemon's stored state
// alone: paused, then kept, then its health. The sources screen and the main view's
// source list both draw it, so the two never disagree.
func sourceMark(source db.Source) (glyph string, state string) {
	switch {
	case !source.Active:
		return "○", "paused"
	case source.Kept():
		return "□", "kept"
	}
	switch source.Health {
	case "healthy":
		return "●", "healthy"
	case "broken":
		return "✗", "broken"
	case "empty":
		return "◇", "empty"
	case "dormant":
		return "◐", "dormant"
	}
	return "·", "unclassified"
}

// renderSourceMark draws sourceMark in its state's colour from theme.
func renderSourceMark(source db.Source, theme StyleTheme) string {
	glyph, state := sourceMark(source)
	color := map[string]lipgloss.Color{
		"paused":  theme.Red,
		"kept":    theme.Gray,
		"healthy": theme.Green,
		"broken":  theme.Red,
		"empty":   theme.Orange,
		"dormant": theme.Orange,
	}[state]
	if color == "" {
		color = theme.Gray
	}
	return lipgloss.NewStyle().Foreground(color).Render(glyph)
}

// singleLine strips every terminal control from s - C0 controls, ESC, DEL and C1
// controls - and flattens it to one line: line breaks and tabs become spaces and runs
// of spaces collapse. Text from a feed's server passes through here before it is drawn.
func singleLine(s string) string {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '\r' || r == '\n' || r == '\t':
			b.WriteRune(' ')
		case r < 0x20 || r == 0x7f || (r >= 0x80 && r <= 0x9f) || r == utf8.RuneError:
			// dropped
		default:
			b.WriteRune(r)
		}
	}
	return strings.Join(strings.Fields(b.String()), " ")
}

// sourceEvidence is the one line under a source on the sources screen saying why its
// mark reads as it does, or "" for a source with no attention, failures or keep.
func sourceEvidence(source db.Source) string {
	date := func(t *time.Time) string { return t.Local().Format("2006-01-02") }
	switch {
	case source.Kept():
		since := ""
		if source.KeptAt != nil {
			since = " since " + date(source.KeptAt)
		}
		return fmt.Sprintf("kept (%s)%s", source.KeptHealth, since)
	case source.Health == "empty":
		fetched := "never fetched"
		if source.LastFetched != nil {
			fetched = "fetched " + source.LastFetched.Local().Format("2006-01-02 15:04")
		}
		return "served 0 entries · " + fetched
	case source.Health == "dormant":
		if source.NewestItemAt != nil {
			return "newest item " + date(source.NewestItemAt)
		}
		if source.CreatedAt != nil {
			return "nothing dated since added " + date(source.CreatedAt)
		}
		return "nothing dated seen"
	case source.Health == "broken" || source.ErrorCount > 0:
		return failureEvidence(source)
	}
	return ""
}

// failureEvidence is "N failures · <last error> · next try <time> · last success
// <date or never>", the whole error stripped of controls and flattened to one line.
func failureEvidence(source db.Source) string {
	parts := []string{fmt.Sprintf("%d failure%s", source.ErrorCount, pluralize(source.ErrorCount))}
	if text := singleLine(source.LastError); text != "" {
		parts = append(parts, text)
	}
	switch {
	case !source.Active:
		parts = append(parts, "paused")
	case source.NextFetchAt != nil:
		parts = append(parts, "next try "+source.NextFetchAt.Local().Format("2006-01-02 15:04"))
	}
	success := "never"
	if source.LastFetched != nil {
		success = source.LastFetched.Local().Format("2006-01-02")
	}
	return strings.Join(append(parts, "last success "+success), " · ")
}

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

	// offset is the first list row the screen draws; UpdateContent and View derive it
	// from the selected source's rows (windowStart).
	offset int

	// Remote mode support
	remoteURL string // If non-empty, use API instead of local DB
}

// NewSourceModal creates a new SourceModal instance
func NewSourceModal() SourceModal {
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
	}
}

// SetRemoteURL sets the remote URL for API-based source fetching
func (m *SourceModal) SetRemoteURL(url string) {
	m.remoteURL = url
}

// SetSize sizes the modal by the help screen's rule for a width x height terminal
func (m *SourceModal) SetSize(width, height int) {
	m.width, m.height = modalSize(width, height)
	m.UpdateContent()
}

// listWidth is the columns the list draws in: the box's width less its padding.
func (m SourceModal) listWidth() int {
	return max(m.width-4, 1)
}

// listHeight is the rows the list and its error share: the box's height less its
// padding (2), the title and the blank row under it (2), and the status bar with the
// two blank rows above it (3).
func (m SourceModal) listHeight() int {
	return max(m.height-7, 3)
}

// LoadSources updates the modal with fresh source data
func (m *SourceModal) LoadSources(sources []db.Source) {
	m.sources = sources
	// Reset cursor if it's out of bounds
	if m.cursor >= len(m.sources) && len(m.sources) > 0 {
		m.cursor = len(m.sources) - 1
	}
	m.UpdateContent()
}

// moveCursor moves the cursor delta sources, staying on the list.
func (m *SourceModal) moveCursor(delta int) {
	m.cursor = max(min(m.cursor+delta, len(m.sources)-1), 0)
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
				m.moveCursor(1)
			case "k", "up":
				m.moveCursor(-1)
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
			case "K":
				// Keep a flagged source's current health; unkeep a kept one
				if len(m.sources) > 0 && m.cursor < len(m.sources) {
					source := m.sources[m.cursor]
					switch {
					case source.Kept():
						return m, operations.UnkeepSource(source.ID)
					case source.Attention:
						return m, operations.KeepSource(source.ID)
					default:
						m.errorMsg = "Nothing to keep: this source raises no warning"
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
				updates := map[string]any{
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

	case tea.MouseMsg:
		// The wheel moves the cursor, so the window follows it like j and k
		if m.mode == "list" && msg.Action == tea.MouseActionPress {
			switch msg.Button {
			case tea.MouseButtonWheelDown:
				m.moveCursor(1)
			case tea.MouseButtonWheelUp:
				m.moveCursor(-1)
			}
		}
	}

	// Update the modal content based on current mode
	m.UpdateContent()

	return m, nil
}

// UpdateContent keeps the selected source in the list's window after anything that
// moves the cursor or changes the rows, the size, the error or the status (exported
// for testing).
func (m *SourceModal) UpdateContent() {
	if m.mode == "list" {
		// Row widths and counts do not depend on colour, so no theme is needed here.
		_, m.offset = m.listWindow(StyleTheme{})
	}
}

// windowStart is the first of total rows to draw in a window of height rows so rows
// first..last are inside it, moved from offset as little as possible: up to first when
// first is above the window, down until last is its bottom row when last is below, never
// past the page that ends at the last row. When the rows outgrow the window, first stays
// in view.
func windowStart(first, last, offset, height, total int) int {
	height = max(height, 1)
	offset = max(min(offset, total-height), 0)
	if last >= offset+height {
		offset = last - height + 1
	}
	if first < offset {
		offset = first
	}
	return max(offset, 0)
}

// listWindow is the list rows the screen draws under the title - the fitted rows from
// the offset that keeps the selected source's rows in view, padded to the rows the
// error leaves - then the error's rows, and that offset.
func (m SourceModal) listWindow(theme StyleTheme) ([]string, int) {
	errorRows := m.errorRows(theme)
	height := max(m.listHeight()-len(errorRows), 1)
	rows, spans := m.listRows(theme, m.listWidth())
	first, last := 0, 0
	if m.cursor < len(spans) {
		first, last = spans[m.cursor].first, spans[m.cursor].last
	}
	offset := windowStart(first, last, m.offset, height, len(rows))
	drawn := slices.Clone(rows[offset:min(offset+height, len(rows))])
	for len(drawn) < height {
		drawn = append(drawn, "")
	}
	return append(drawn, errorRows...), offset
}

// errorRows is a blank row and the error wrapped to the list's width, or none when
// there is no error.
func (m SourceModal) errorRows(theme StyleTheme) []string {
	if m.errorMsg == "" {
		return nil
	}
	rows := []string{""}
	for row := range strings.SplitSeq(ansi.Wrap("⚠ "+singleLine(m.errorMsg), m.listWidth(), ""), "\n") {
		rows = append(rows, theme.ErrorStyle().Render(row))
	}
	return rows
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
		spacing := max(m.width-4-leftWidth-rightWidth, 1)

		content.WriteString(titleText + strings.Repeat(" ", spacing) + countRendered)
	} else {
		content.WriteString(titleText)
	}
	content.WriteString("\n\n")

	// Get content based on mode
	var mainContentStr string

	if m.mode == "list" {
		// The error sits below the scrolled list, not inside it (gh #87): inside,
		// an empty or long list pushed it out of the visible window.
		rows, _ := m.listWindow(theme)
		content.WriteString(strings.Join(rows, "\n"))
		mainContentStr = content.String()
	} else {
		// For other modes, render content directly
		var mainContent string
		switch m.mode {
		case "add":
			mainContent = m.renderAddContentOnly(theme)
		case "edit":
			mainContent = m.renderEditContentOnly(theme)
		case "confirm_remove":
			mainContent = m.renderConfirmContentOnly(theme)
		}
		content.WriteString(mainContent)
		mainContentStr = content.String()
	}

	// Create status bar: one row, its text cut to the bar's width less its padding, so
	// a long message never adds rows to the screen
	statusWidth := max(m.width-6, 1)
	var statusContent string
	if m.statusMessage != "" {
		// Show status message in cyan like main/reader modal
		statusContent = lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).
			Render(ansi.Truncate(singleLine(m.statusMessage), statusWidth, "…"))
	} else {
		// Show commands when no status message
		switch m.mode {
		case "list":
			statusContent = "[a]dd [↵] edit [p]ause [K]eep [d]elete [esc] close"
		case "add", "edit":
			statusContent = "[tab] switch [↵] save [esc] cancel"
		case "confirm_remove":
			statusContent = "[y] delete [n] cancel"
		}
		statusContent = ansi.Truncate(statusContent, statusWidth, "…")
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

// sourceSpan is the first and last of one source's rows in the list.
type sourceSpan struct {
	first, last int
}

// listRows is the list as rows that each fit in width columns, and each source's span
// of them: the source's own row cut to width, then its evidence wrapped to the width
// less its four-column indent, a word longer than that broken across rows. Evidence is
// styled after it is wrapped, so no style runs across a row break.
func (m SourceModal) listRows(theme StyleTheme, width int) ([]string, []sourceSpan) {
	var lines []string
	var spans []sourceSpan

	// Source list
	if len(m.sources) == 0 {
		noSourcesStyle := theme.MutedStyle().Italic(true)
		lines = append(lines, "", noSourcesStyle.Render(ansi.Truncate("No sources configured", width, "…")))
		lines = append(lines, "", theme.MutedStyle().Render(ansi.Truncate("Press [a] to add your first source", width, "…")))
	} else {
		for i, source := range m.sources {
			// Status indicator: the daemon's stored state, as the main view draws it
			status := renderSourceMark(source, theme)

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

			// Cut the title to its column by display width, then style it
			displayName := ansi.Truncate(source.Name, titleWidth, "...")

			// Apply styling to the truncated name
			var nameStr string
			if i == m.cursor {
				nameStr = lipgloss.NewStyle().Foreground(theme.White).Bold(true).Render(displayName)
			} else {
				nameStr = theme.TextStyle().Render(displayName)
			}

			// A source the daemon fetches with a browser user-agent is marked after its
			// name; the marker takes its width out of the name column's padding.
			markerText := ""
			if source.Naughty {
				markerText = naughtyMarker
				nameStr += theme.MutedStyle().Render(markerText)
			}

			// Calculate padding from the unstyled text's display width
			titlePadding := max(titleWidth-ansi.StringWidth(displayName)-ansi.StringWidth(markerText), 0)
			typePadding := max(typeWidth-ansi.StringWidth(strings.ToUpper(source.Type)), 0)

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

			span := sourceSpan{first: len(lines)}
			lines = append(lines, ansi.Truncate(line, width, "…"))
			if evidence := sourceEvidence(source); evidence != "" {
				for row := range strings.SplitSeq(ansi.Wrap(evidence, max(width-4, 1), ""), "\n") {
					lines = append(lines, "    "+theme.MutedStyle().Render(row))
				}
			}
			span.last = len(lines) - 1
			spans = append(spans, span)
		}
	}

	return lines, spans
}

// appendErrorLine appends a blank line and the rendered error when there is one;
// returns lines unchanged when errorMsg is empty.
func appendErrorLine(theme StyleTheme, lines []string, errorMsg string) []string {
	if errorMsg != "" {
		lines = append(lines, "")
		lines = append(lines, theme.ErrorStyle().Render("⚠ "+errorMsg))
	}
	return lines
}

// renderAddContentOnly renders just the add form content
func (m SourceModal) renderAddContentOnly(theme StyleTheme) string {
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
func (m SourceModal) renderEditContentOnly(theme StyleTheme) string {
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
func (m SourceModal) renderConfirmContentOnly(theme StyleTheme) string {
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
