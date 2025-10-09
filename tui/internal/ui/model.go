package ui

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"runtime"
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/nickpending/prismis/internal/clipboard"
	"github.com/nickpending/prismis/internal/commands"
	"github.com/nickpending/prismis/internal/config"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

// Model represents the application state for the TUI
type Model struct {
	items             []db.ContentItem
	sources           []db.Source // All sources with counts
	cursor            int
	priority          string // "high", "medium", "low", "all"
	view              string // "list", "reader"
	loading           bool
	err               error
	viewport          viewport.Model // For scrollable content in reader view
	ready             bool           // Viewport ready flag
	width             int            // Terminal width
	height            int            // Terminal height
	showReader        bool           // Show reader view (from clean_cyber)
	showUnprioritized bool           // Show items with null/empty priority (default false)
	hiddenCount       int            // Count of hidden unprioritized items
	// View state fields for header display
	showAll    bool   // Show all items vs unread only (default false - unread only)
	sortNewest bool   // Sort by newest first vs oldest first (default true - newest)
	filterType string // Source type filter: "all", "rss", "reddit", "youtube" (default "all")
	// Status message for user feedback
	statusMessage string // Temporary status message to display
	flashItem     int    // Index of item to flash (-1 for none)
	// Modal state
	sourceModal SourceModal // Modal for managing sources
	helpModal   HelpModal   // Modal for keyboard shortcuts help
	commandMode CommandMode // Neovim-style command mode
	// Auto-refresh state
	refreshInterval time.Duration // Interval for auto-refresh (0 = disabled)
	// Prune confirmation state
	pruneConfirm pruneConfirmState
	// Sources viewport for scrollable source list
	sourcesViewport viewport.Model // Viewport for source list scrolling
	// Pane focus system (vim-style)
	focusedPane string // "sources", "content" (content is either list or reader based on view)
	// Theme system
	theme StyleTheme // Current color theme
}

// itemsLoadedMsg represents content items loaded from database
type itemsLoadedMsg struct {
	items          []db.ContentItem
	hiddenCount    int // Count of unprioritized items that were filtered out
	err            error
	preserveCursor bool   // If true, try to preserve cursor position
	targetItemID   string // Item ID to position cursor on (if preserveCursor is true)
	isAutoRefresh  bool   // If true, this was triggered by auto-refresh timer
}

// sourcesLoadedMsg represents sources loaded from database
type sourcesLoadedMsg struct {
	sources []db.Source
	err     error
}

// clearStatusMsg is sent to clear the status message after a delay
type clearStatusMsg struct{}

// clearFlashMsg is sent to clear the flash effect after a delay
type clearFlashMsg struct{}

// autoRefreshMsg is sent by the timer to trigger automatic refresh
type autoRefreshMsg struct{}

// pruneConfirmState tracks the prune confirmation workflow
type pruneConfirmState struct {
	active bool
	count  int
	days   *int
}

// NewModel creates a new Model instance
func NewModel() Model {
	return Model{
		items:             []db.ContentItem{},
		cursor:            0,
		priority:          "all",
		view:              "list",
		loading:           true,
		viewport:          viewport.New(80, 20), // Initialize viewport with default size
		showUnprioritized: false,                // Hide unprioritized by default
		hiddenCount:       0,
		// Initialize view state with good defaults
		showAll:       false,            // Show unread only by default
		sortNewest:    true,             // Show newest first by default
		filterType:    "all",            // Show all source types by default
		statusMessage: "",               // No status message initially
		flashItem:     -1,               // No item flashing initially
		sourceModal:   NewSourceModal(), // Initialize source modal
		helpModal:     NewHelpModal(),   // Initialize help modal
		commandMode:   NewCommandMode(), // Initialize command mode
		// Initialize sources viewport
		sourcesViewport: viewport.New(20, 10), // Will be resized properly in View()
		focusedPane:     "content",            // Start with content focused (list or reader)
		// Initialize theme
		theme: CleanCyberTheme, // Default theme
	}
}

// Init initializes the model and returns a command to fetch initial content
func (m Model) Init() tea.Cmd {
	// Initialize the sources viewport with empty content first
	m.updateSourcesViewport()

	cmds := []tea.Cmd{
		fetchItemsWithState(m),
		fetchSources(),
	}

	// Load config and set up auto-refresh if enabled
	if cfg, err := config.LoadConfig(); err == nil {
		interval := cfg.GetRefreshInterval()
		if interval > 0 {
			m.refreshInterval = time.Duration(interval) * time.Second
			// Start the auto-refresh timer
			cmds = append(cmds, autoRefreshCmd(m.refreshInterval))
		}
	}

	return tea.Batch(cmds...)
}

// Update handles messages and updates the model state
func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmd tea.Cmd
	var cmds []tea.Cmd

	// Handle window size for viewport and modals
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		// Set viewport size with some padding
		m.viewport.Width = msg.Width - 4
		m.viewport.Height = msg.Height - 8
		m.ready = true
		// Set sources viewport size (sidebar is 1/4 width)
		sidebarWidth := msg.Width / 4
		if sidebarWidth < 30 {
			sidebarWidth = 30
		}
		m.sourcesViewport.Width = sidebarWidth - 2 // Padding for borders
		// Sources take ~65% of sidebar height (after stats section)
		sidebarHeight := msg.Height - 6 // Account for header/footer
		m.sourcesViewport.Height = (sidebarHeight * 65) / 100
		// Update modal sizes
		m.sourceModal.SetSize(msg.Width, msg.Height)
		m.helpModal.SetSize(msg.Width, msg.Height)
		m.commandMode.SetWidth(msg.Width)
	}

	// Handle command mode updates first (highest priority)
	if m.commandMode.IsActive() {
		m.commandMode, cmd = m.commandMode.Update(msg)

		// Handle command messages
		if !m.commandMode.IsActive() {
			// Command mode was just closed, check for command execution
			cmds = append(cmds, cmd)
		}

		return m, tea.Batch(cmds...)
	}

	// Handle source modal updates if it's visible
	if m.sourceModal.IsVisible() {
		m.sourceModal, cmd = m.sourceModal.Update(msg)
		// If modal was closed, refresh sources
		if !m.sourceModal.IsVisible() {
			return m, fetchSources()
		}
		return m, cmd
	}

	// Handle help modal updates if it's visible
	if m.helpModal.IsVisible() {
		m.helpModal, cmd = m.helpModal.Update(msg)
		return m, cmd
	}

	// Handle view-specific updates - only update reader viewport when content pane is focused
	if m.view == "reader" && m.focusedPane == "content" {
		// Update viewport in reader view only when it has focus
		m.viewport, cmd = m.viewport.Update(msg)
		cmds = append(cmds, cmd)
	}

	switch msg := msg.(type) {
	case commands.RefreshMsg:
		// Handle refresh command
		if msg.PreserveCursor && m.view == "list" && !m.loading {
			// Save current item ID to restore position if possible
			var currentItemID string
			if m.cursor < len(m.items) && m.cursor >= 0 {
				currentItemID = m.items[m.cursor].ID
			}

			m.loading = true

			// Create refresh command that preserves position (same as old 'r' key)
			refreshCmd := func() tea.Msg {
				items, hiddenCount, err := db.GetContentWithFilters(
					m.priority,
					m.showUnprioritized,
					m.showAll,
					m.filterType,
					m.sortNewest,
				)

				return itemsLoadedMsg{
					items:          items,
					hiddenCount:    hiddenCount,
					err:            err,
					preserveCursor: true,
					targetItemID:   currentItemID,
				}
			}

			return m, refreshCmd
		} else {
			// Simple refresh without cursor preservation
			m.loading = true
			return m, fetchItemsWithState(m)
		}

	case commands.ErrorMsg:
		// Show error in command line instead of status
		cmd := m.commandMode.SetError(msg.Message)
		return m, cmd

	case commands.HelpMsg:
		// Show the help modal (same as pressing ?)
		m.helpModal.Show()
		return m, nil

	case commands.AddSourceMsg:
		// Add source (refresh happens in response to success message)
		return m, operations.AddSource(msg.URL, "")

	case commands.RemoveSourceMsg:
		// Remove source (refresh happens in response to success message)
		return m, operations.RemoveSource(msg.Identifier)

	case commands.ShowLogsMsg:
		// Show logs (placeholder for now)
		return m, operations.ShowLogs()

	case commands.PruneMsg:
		// Handle prune command with optional confirmation
		return m, operations.HandlePruneCommand(msg)

	case commands.PauseSourceMsg:
		// Pause source (refresh happens in response to success message)
		return m, operations.PauseSource(msg.URL)

	case commands.ResumeSourceMsg:
		// Resume source (refresh happens in response to success message)
		return m, operations.ResumeSource(msg.URL)

	case commands.EditSourceMsg:
		// Edit source name using the identifier lookup
		return m, operations.EditSourceName(msg.Identifier, msg.NewName)

	case commands.ReportMsg:
		// Generate report with specified period
		return m, operations.GenerateReport(msg.Period)

	case commands.AudioMsg:
		// Generate audio briefing from HIGH priority content
		m.statusMessage = "Generating audio briefing..."
		return m, operations.GenerateAudioBriefing()

	case commands.ExportSourcesMsg:
		// Export sources to clipboard
		return m, operations.ExportSources()

	case commands.FabricMsg:
		// Execute Fabric pattern on current item's full content
		currentContent := ""
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			// Only use full content - no fallback to summary
			currentContent = item.Content
		}
		return m, operations.ExecuteFabricCommand(msg.Pattern, msg.ListOnly, currentContent)

	case commands.ThemeMsg:
		// Cycle to next theme
		currentIdx := -1
		for i, theme := range AvailableThemes {
			if theme.Name == m.theme.Name {
				currentIdx = i
				break
			}
		}
		// Move to next theme (wrap around)
		nextIdx := (currentIdx + 1) % len(AvailableThemes)
		m.theme = AvailableThemes[nextIdx]
		m.statusMessage = fmt.Sprintf("Theme: %s", m.theme.Name)
		// Update sources viewport with new theme
		m.updateSourcesViewport()
		cmds = append(cmds, clearStatusAfterDelay(2*time.Second))

	// Reader command handlers
	case commands.MarkMsg:
		// Toggle read/unread status (works in both list and reader views)
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			// Use the operations package to toggle read status
			return m, operations.ToggleArticleRead(item)
		}

	case commands.FavoriteMsg:
		// Toggle favorite status (works in both list and reader views)
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			// Use the operations package to toggle favorite status
			return m, operations.ToggleArticleFavorite(item)
		}

	case commands.OpenMsg:
		// Open URL in browser (works in both list and reader views)
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			err := openInBrowser(item.URL)
			if err != nil {
				m.statusMessage = "Failed to open browser"
			} else {
				m.statusMessage = "Opening in browser..."
			}
			cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
		}

	case commands.YankMsg:
		// Copy URL to clipboard (works in both list and reader views)
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			err := clipboard.CopyToClipboard(item.URL)
			if err != nil {
				m.statusMessage = "Failed to copy URL"
			} else {
				m.statusMessage = "URL copied to clipboard"
			}
			cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
		}

	case commands.CopyMsg:
		// Copy content to clipboard (works in both list and reader views)
		if len(m.items) > 0 && m.cursor < len(m.items) {
			item := m.items[m.cursor]
			readingSummary := extractReadingSummary(item.Analysis)

			if readingSummary == "" {
				m.statusMessage = "No content available"
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			} else {
				err := clipboard.CopyToClipboard(readingSummary)
				if err != nil {
					m.statusMessage = "Failed to copy content"
				} else {
					m.statusMessage = "Content copied to clipboard"
				}
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			}
		}

	case tea.KeyMsg:
		// Check if waiting for prune confirmation
		if m.pruneConfirm.active {
			switch msg.String() {
			case "y", "Y":
				// Execute the prune
				m.pruneConfirm.active = false
				m.statusMessage = "Pruning..."
				return m, operations.ExecutePrune(m.pruneConfirm.days)
			case "n", "N", "esc":
				// Cancel the prune
				m.pruneConfirm = pruneConfirmState{}
				m.statusMessage = "Prune cancelled"
				return m, nil
			default:
				// Ignore other keys during confirmation
				return m, nil
			}
		}

		// Check if command mode should be disabled for normal keys
		if m.commandMode.IsActive() {
			// Command mode is active, don't process normal navigation keys
			// Let command mode handle everything
			m.commandMode, cmd = m.commandMode.Update(msg)
			return m, cmd
		}

		switch msg.String() {
		case ":":
			// Activate command mode
			m.commandMode.Show()
			return m, nil

		case "q":
			if m.view == "reader" {
				// In reader view, q goes back to list
				m.view = "list"
				return m, nil
			}
			// In list view, q quits
			return m, tea.Quit

		case "ctrl+c":
			return m, tea.Quit

		// Switch to reader view
		case "enter":
			if m.view == "list" && len(m.items) > 0 {
				m.view = "reader"
				// Update viewport with current article content
				m.updateReaderContent()
			}
		case "esc":
			if m.view == "reader" {
				m.view = "list"
			}

		// Vim-style pane navigation
		case "ctrl+w":
			// Start vim window command mode - wait for next key
			m.statusMessage = "-- WINDOW --"
			// This would need a small state machine, for now use direct shortcuts

		case "ctrl+h", "ctrl+w h":
			// Move to left pane (sources)
			m.focusedPane = "sources"
			m.statusMessage = ""

		case "ctrl+l", "ctrl+w l":
			// Move to right pane (content)
			m.focusedPane = "content"
			m.statusMessage = ""

		case "ctrl+w w", "tab":
			// Cycle through panes
			if m.focusedPane == "sources" {
				m.focusedPane = "content"
			} else {
				m.focusedPane = "sources"
			}
			m.statusMessage = ""

		// NOTE: Actions like mark, favorite, copy, yank, open are now :commands
		// They can be executed with :m, :f, :c, :y, :o (or full names)

		// Navigation - different behavior based on focused pane
		case "j", "down":
			if m.focusedPane == "sources" {
				// When sources pane is focused, j/k scroll the sources
				m.sourcesViewport.LineDown(1)
			} else if m.focusedPane == "content" {
				// Content pane focused - depends on view
				if m.view == "list" && m.cursor < len(m.items)-1 {
					m.cursor++
				} else if m.view == "reader" {
					m.viewport.LineDown(1)
				}
			}
		case "k", "up":
			if m.focusedPane == "sources" {
				// When sources pane is focused, j/k scroll the sources
				m.sourcesViewport.LineUp(1)
			} else if m.focusedPane == "content" {
				// Content pane focused - depends on view
				if m.view == "list" && m.cursor > 0 {
					m.cursor--
				} else if m.view == "reader" {
					m.viewport.LineUp(1)
				}
			}

		// Reader-specific navigation (only when content pane is focused)
		case "h", "left":
			if m.focusedPane == "content" && m.view == "reader" && m.cursor > 0 {
				// Previous article
				m.cursor--
				m.updateReaderContent()
			}
		case "l", "right":
			if m.focusedPane == "content" && m.view == "reader" && m.cursor < len(m.items)-1 {
				// Next article
				m.cursor++
				m.updateReaderContent()
			}
		case "g":
			if m.focusedPane == "sources" {
				// Go to top of sources
				m.sourcesViewport.GotoTop()
			} else if m.focusedPane == "content" {
				if m.view == "list" {
					// Go to top of list
					m.cursor = 0
				} else if m.view == "reader" {
					// Go to top of reader content
					m.viewport.GotoTop()
				}
			}
		case "G":
			if m.focusedPane == "sources" {
				// Go to bottom of sources
				m.sourcesViewport.GotoBottom()
			} else if m.focusedPane == "content" {
				if m.view == "list" && len(m.items) > 0 {
					// Go to bottom of list
					m.cursor = len(m.items) - 1
				} else if m.view == "reader" {
					// Go to bottom of reader content
					m.viewport.GotoBottom()
				}
			}
		case "0":
			// Show only unprioritized items
			if m.view == "list" {
				m.priority = "unprioritized"
				m.cursor = 0
				m.loading = true
				// Note: showUnprioritized is always true for this view
				m.showUnprioritized = true
				return m, fetchItemsWithState(m)
			}
		case "1":
			if m.view == "list" {
				m.priority = "high"
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		case "2":
			if m.view == "list" {
				m.priority = "medium"
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		case "3":
			if m.view == "list" {
				m.priority = "low"
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		case "4", "*":
			if m.view == "list" {
				m.priority = "favorites"
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		case "a":
			if m.view == "list" {
				m.priority = "all"
				m.cursor = 0
				m.loading = true
				// Note: showUnprioritized is false for 'all' to show only prioritized items
				m.showUnprioritized = false
				return m, fetchItemsWithState(m)
			}
		// Toggle unread/all view
		case "u":
			if m.view == "list" {
				m.showAll = !m.showAll
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		// Toggle date sort (newest/oldest)
		case "d":
			if m.view == "list" {
				m.sortNewest = !m.sortNewest
				// Sort items in place without refetching
				sortItemsByDate(m.items, m.sortNewest)
				// Keep cursor in bounds
				if m.cursor >= len(m.items) && len(m.items) > 0 {
					m.cursor = len(m.items) - 1
				}
			}
		// Cycle source type filter
		case "s":
			if m.view == "list" {
				// Cycle through: all -> rss -> reddit -> youtube -> all
				filterTypes := []string{"all", "rss", "reddit", "youtube"}
				currentIdx := 0
				for i, ft := range filterTypes {
					if ft == m.filterType {
						currentIdx = i
						break
					}
				}
				// Move to next filter type with modulo wrap
				m.filterType = filterTypes[(currentIdx+1)%len(filterTypes)]
				m.cursor = 0
				m.loading = true
				return m, fetchItemsWithState(m)
			}
		// Open source management modal (capital S)
		case "S":
			if m.view == "list" {
				// Load fresh sources and show modal
				m.sourceModal.SetSize(m.width, m.height)
				m.sourceModal.LoadSources(m.sources)
				m.sourceModal.Show()
				m.sourceModal.UpdateContent()
			}
		// Open help modal
		case "?":
			if m.view == "list" && !m.sourceModal.IsVisible() {
				// Only open help from main view when no other modals are open
				m.helpModal.SetSize(m.width, m.height)
				m.helpModal.Show()
			}
		}

	case sourcesLoadedMsg:
		// Handle source updates regardless of modal visibility
		if msg.err == nil {
			m.sources = msg.sources
			// Update the sources viewport with new data
			m.updateSourcesViewport()
			// Update modal if it's visible
			if m.sourceModal.IsVisible() {
				m.sourceModal.LoadSources(msg.sources)
			}
		}

	case itemsLoadedMsg:
		m.loading = false
		m.err = msg.err
		if msg.err == nil {
			previousCount := len(m.items)
			m.items = msg.items
			m.hiddenCount = msg.hiddenCount

			// Handle cursor position
			if msg.preserveCursor && msg.targetItemID != "" {
				// This was a manual refresh
				// Try to find the same item and position cursor there
				found := false
				for i, item := range m.items {
					if item.ID == msg.targetItemID {
						m.cursor = i
						found = true
						break
					}
				}
				// If item not found, keep cursor in reasonable position
				if !found && m.cursor >= len(m.items) && m.cursor > 0 {
					m.cursor = len(m.items) - 1
				}

				// Show refresh completion message
				newCount := len(m.items)
				if msg.isAutoRefresh {
					// Auto-refresh messages
					if newCount > previousCount {
						diff := newCount - previousCount
						m.statusMessage = fmt.Sprintf("✓ Auto-refreshed! %d new item(s)", diff)
					} else if newCount < previousCount {
						m.statusMessage = "✓ Auto-refreshed (some items marked as read)"
					} else {
						m.statusMessage = "✓ Auto-refreshed"
					}
				} else {
					// Manual refresh messages
					if newCount > previousCount {
						diff := newCount - previousCount
						m.statusMessage = fmt.Sprintf("✓ Refreshed! %d new item(s)", diff)
					} else if newCount < previousCount {
						m.statusMessage = "✓ Refreshed (some items marked as read)"
					} else {
						m.statusMessage = "✓ Refreshed"
					}
				}
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			} else {
				// Normal cursor bounds check
				if m.cursor >= len(m.items) {
					m.cursor = 0
				}
			}
		} else {
			// Show error message if refresh failed
			if msg.preserveCursor {
				m.statusMessage = fmt.Sprintf("✗ Refresh failed: %v", msg.err)
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			}
		}
	case clearStatusMsg:
		m.statusMessage = ""
	case clearFlashMsg:
		m.flashItem = -1

	case autoRefreshMsg:
		// Handle automatic refresh - only if not already loading and in list view
		if !m.loading && m.view == "list" && !m.sourceModal.IsVisible() {
			// Save current item ID to restore position
			var currentItemID string
			if m.cursor < len(m.items) && m.cursor >= 0 {
				currentItemID = m.items[m.cursor].ID
			}

			m.loading = true

			// Create refresh command that preserves position
			refreshCmd := func() tea.Msg {
				items, hiddenCount, err := db.GetContentWithFilters(
					m.priority,
					m.showUnprioritized,
					m.showAll,
					m.filterType,
					m.sortNewest,
				)

				return itemsLoadedMsg{
					items:          items,
					hiddenCount:    hiddenCount,
					err:            err,
					preserveCursor: true,
					targetItemID:   currentItemID,
					isAutoRefresh:  true,
				}
			}

			// Schedule next auto-refresh
			cmds = append(cmds, refreshCmd, autoRefreshCmd(m.refreshInterval))
		} else if m.refreshInterval > 0 {
			// If we couldn't refresh (loading/modal open), reschedule for next interval
			cmds = append(cmds, autoRefreshCmd(m.refreshInterval))
		}

	case operations.PruneCountMsg:
		// Received count for prune confirmation or display
		if msg.Count == 0 {
			m.statusMessage = "No unprioritized items to prune"
		} else if msg.ShowOnly {
			// Just show the count (for :prune? or :prune count)
			statusMsg := fmt.Sprintf("%d unprioritized items", msg.Count)
			if msg.Days != nil {
				statusMsg = fmt.Sprintf("%d unprioritized items older than %d days", msg.Count, *msg.Days)
			}
			m.statusMessage = statusMsg
		} else {
			// Store state for confirmation
			m.pruneConfirm = pruneConfirmState{
				active: true,
				count:  msg.Count,
				days:   msg.Days,
			}

			// Show vim-style confirmation prompt
			confirmPrompt := fmt.Sprintf("Delete %d unprioritized items", msg.Count)
			if msg.Days != nil {
				confirmPrompt = fmt.Sprintf("Delete %d unprioritized items older than %d days", msg.Count, *msg.Days)
			}
			m.statusMessage = confirmPrompt + "? (y/n) "
		}

	case operations.PruneResultMsg:
		// Handle prune operation result
		m.pruneConfirm = pruneConfirmState{} // Clear confirmation state

		if msg.Error != nil {
			m.statusMessage = fmt.Sprintf("Prune failed: %v", msg.Error)
		} else if msg.Deleted == 0 {
			m.statusMessage = "No items were pruned"
		} else {
			m.statusMessage = fmt.Sprintf("Pruned %d unprioritized items", msg.Deleted)
			// Trigger refresh to update the UI
			cmds = append(cmds, func() tea.Msg {
				return commands.RefreshMsg{PreserveCursor: true}
			})
		}

	case operations.SourceOperationMsg:
		// Handle source operation message from operations package
		m.statusMessage = msg.Message

		// If operation was successful, trigger a refresh to show changes
		if msg.Success {
			// Trigger content refresh to show new/updated sources
			refreshCmd := func() tea.Msg {
				return commands.RefreshMsg{PreserveCursor: false}
			}
			cmds = append(cmds, refreshCmd)

			// Also refresh the sources panel to show updated source list
			cmds = append(cmds, fetchSources())
		}

	case operations.ReportOperationMsg:
		// Handle report generation message from operations package
		m.statusMessage = msg.Message

		// Clear status message after delay (success or error)
		cmds = append(cmds, clearStatusAfterDelay(3*time.Second))

	case operations.AudioOperationMsg:
		// Handle audio briefing generation message from operations package
		m.statusMessage = msg.Message

		// Clear status message after delay (success or error)
		cmds = append(cmds, clearStatusAfterDelay(5*time.Second))

	case operations.FabricOperationMsg:
		// Handle Fabric operation results
		if msg.Success {
			if msg.Result != "" {
				// Show result in reader view or modal (for now, just status)
				m.statusMessage = msg.Message
				// TODO: Display full result in a modal or reader view
			} else {
				// Show patterns list or success message
				m.statusMessage = msg.Message
			}
		} else {
			m.statusMessage = msg.Message
		}

		// Clear status message after delay
		cmds = append(cmds, clearStatusAfterDelay(5*time.Second))

	// Article operation messages from operations package
	case operations.ArticleMarkedMsg:
		if msg.Success {
			// Update the item in our local state
			for i, item := range m.items {
				if item.ID == msg.ID {
					m.items[i].Read = msg.Read
					break
				}
			}
			if msg.Read {
				m.statusMessage = "Marked as read"
			} else {
				m.statusMessage = "Marked as unread"
			}

			// If we're in unread-only mode and just marked as read, refresh to filter it out
			// Or if we're showing all and just marked as unread, refresh to ensure proper display
			if (!m.showAll && msg.Read) || (m.showAll && !msg.Read) {
				// If we're in reader view and this item will be filtered out,
				// go back to list view to avoid showing empty content
				if m.view == "reader" && !m.showAll && msg.Read {
					m.view = "list"
				}

				// Trigger content refresh to update filtered view
				refreshCmd := func() tea.Msg {
					return commands.RefreshMsg{PreserveCursor: true}
				}
				cmds = append(cmds, refreshCmd)
			}
		} else {
			m.statusMessage = fmt.Sprintf("Failed to mark: %v", msg.Error)
		}
		cmds = append(cmds, clearStatusAfterDelay(2*time.Second))

	case operations.ArticleFavoritedMsg:
		if msg.Success {
			// Update the item in our local state
			for i, item := range m.items {
				if item.ID == msg.ID {
					m.items[i].Favorited = msg.Favorited
					break
				}
			}
			if msg.Favorited {
				m.statusMessage = "★ Favorited"
			} else {
				m.statusMessage = "☆ Unfavorited"
			}
		} else {
			m.statusMessage = fmt.Sprintf("Failed to toggle favorite: %v", msg.Error)
		}
		cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
	}

	if len(cmds) > 0 {
		return m, tea.Batch(cmds...)
	}
	return m, nil
}

// View renders the current model state
func (m Model) View() string {
	// RenderList now handles both list and reader views
	baseView := RenderList(m)

	// Overlay source modal if visible (with dimming)
	if m.sourceModal.IsVisible() {
		return m.sourceModal.ViewWithOverlay(baseView, m.width, m.height, m.theme)
	}

	// Overlay help modal if visible (with dimming)
	if m.helpModal.IsVisible() {
		return m.helpModal.ViewWithOverlay(baseView, m.width, m.height, m.theme)
	}

	return baseView
}

// fetchItems removed - consolidated to fetchItemsWithState for single code path

// fetchItemsWithState returns a command that fetches content with all current state applied
func fetchItemsWithState(m Model) tea.Cmd {
	return func() tea.Msg {
		items, hiddenCount, err := db.GetContentWithFilters(
			m.priority,
			m.showUnprioritized,
			m.showAll,
			m.filterType,
			m.sortNewest,
		)
		return itemsLoadedMsg{
			items:       items,
			hiddenCount: hiddenCount,
			err:         err,
		}
	}
}

// sortItemsByDate sorts items in place by published date
func sortItemsByDate(items []db.ContentItem, newest bool) {
	// Sort using Go's sort.Slice
	for i := 0; i < len(items)-1; i++ {
		for j := i + 1; j < len(items); j++ {
			swap := false
			if newest {
				// Newest first: later dates come first
				swap = items[j].Published.After(items[i].Published)
			} else {
				// Oldest first: earlier dates come first
				swap = items[j].Published.Before(items[i].Published)
			}
			if swap {
				items[i], items[j] = items[j], items[i]
			}
		}
	}
}

// autoRefreshCmd returns a command that triggers auto-refresh after the specified interval
func autoRefreshCmd(interval time.Duration) tea.Cmd {
	return tea.Tick(interval, func(t time.Time) tea.Msg {
		return autoRefreshMsg{}
	})
}

// fetchSources returns a command that fetches all sources from the database
func fetchSources() tea.Cmd {
	return func() tea.Msg {
		sources, err := db.GetSourcesWithCounts()
		return sourcesLoadedMsg{
			sources: sources,
			err:     err,
		}
	}
}

// clearStatusAfterDelay returns a command that clears the status message after a delay
func clearStatusAfterDelay(delay time.Duration) tea.Cmd {
	return tea.Tick(delay, func(t time.Time) tea.Msg {
		return clearStatusMsg{}
	})
}

// flashItemCmd returns a command that flashes an item and then clears it
func flashItemCmd() tea.Cmd {
	return tea.Tick(200*time.Millisecond, func(t time.Time) tea.Msg {
		return clearFlashMsg{}
	})
}

// updateSourcesViewport updates the sources viewport with formatted source list
func (m *Model) updateSourcesViewport() {
	content := m.buildSourcesContent(m.theme)
	m.sourcesViewport.SetContent(content)
}

// buildSourcesContent builds the formatted source list with proper theming
func (m *Model) buildSourcesContent(theme StyleTheme) string {
	ls := lipgloss.NewStyle()

	// Group sources by type for display
	sourcesByType := make(map[string][]db.Source)
	for _, source := range m.sources {
		sourcesByType[source.Type] = append(sourcesByType[source.Type], source)
	}

	var lines []string

	// RSS Sources
	if rssources, ok := sourcesByType["rss"]; ok && len(rssources) > 0 {
		header := ls.Foreground(theme.Cyan).Bold(true).Render(fmt.Sprintf("RSS [%d]", len(rssources)))
		lines = append(lines, header)
		for _, source := range rssources {
			lines = append(lines, m.formatSourceLine(source, theme))
		}
		lines = append(lines, "")
	}

	// Reddit Sources
	if redditsources, ok := sourcesByType["reddit"]; ok && len(redditsources) > 0 {
		header := ls.Foreground(theme.Cyan).Bold(true).Render(fmt.Sprintf("REDDIT [%d]", len(redditsources)))
		lines = append(lines, header)
		for _, source := range redditsources {
			lines = append(lines, m.formatSourceLine(source, theme))
		}
		lines = append(lines, "")
	}

	// YouTube Sources
	if ytsources, ok := sourcesByType["youtube"]; ok && len(ytsources) > 0 {
		header := ls.Foreground(theme.Cyan).Bold(true).Render(fmt.Sprintf("YOUTUBE [%d]", len(ytsources)))
		lines = append(lines, header)
		for _, source := range ytsources {
			lines = append(lines, m.formatSourceLine(source, theme))
		}
	}

	return strings.Join(lines, "\n")
}

// formatSourceLine formats a single source line with status indicator and count
func (m *Model) formatSourceLine(source db.Source, theme StyleTheme) string {
	ls := lipgloss.NewStyle()

	// Determine status icon and color
	var statusIcon string
	var statusColor lipgloss.Color

	if !source.Active {
		statusIcon = "○" // Paused/inactive
		statusColor = theme.Red
	} else if source.ErrorCount > 3 {
		statusIcon = "●" // Has errors
		statusColor = theme.Red
	} else if source.LastFetched == nil || time.Since(*source.LastFetched) > 24*time.Hour {
		statusIcon = "●" // Stale
		statusColor = theme.Orange
	} else {
		statusIcon = "●" // Healthy
		statusColor = theme.Green
	}

	status := ls.Foreground(statusColor).Render(statusIcon)
	count := ls.Foreground(theme.White).Render(fmt.Sprintf("[%d]", source.UnreadCount))

	// Truncate name to fit viewport (like original code did with width-12)
	// The -12 accounts for status icon, spaces, and count display
	name := truncate(source.Name, m.sourcesViewport.Width-12)

	return fmt.Sprintf("%s %s %s", status, name, count)
}

// extractReadingSummary extracts the reading_summary field from the Analysis JSON
func extractReadingSummary(analysisJSON string) string {
	if analysisJSON == "" {
		return ""
	}

	var analysis map[string]interface{}
	if err := json.Unmarshal([]byte(analysisJSON), &analysis); err != nil {
		return ""
	}

	if readingSummary, ok := analysis["reading_summary"].(string); ok {
		return readingSummary
	}

	return ""
}

// openInBrowser opens the given URL in the default browser.
// It detects the OS and uses the appropriate command.
// Uses Start() instead of Run() to avoid blocking the TUI.
func openInBrowser(url string) error {
	if url == "" {
		return fmt.Errorf("cannot open empty URL")
	}

	var cmd *exec.Cmd

	switch runtime.GOOS {
	case "darwin":
		// macOS: use open command
		cmd = exec.Command("open", url)
	case "linux":
		// Linux: use xdg-open
		cmd = exec.Command("xdg-open", url)
	case "windows":
		// Windows: use cmd /c start
		cmd = exec.Command("cmd", "/c", "start", url)
	default:
		return fmt.Errorf("unsupported platform: %s", runtime.GOOS)
	}

	// Use Start() instead of Run() to avoid blocking the TUI
	// We don't need to wait for the browser to finish launching
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to open browser: %w", err)
	}

	return nil
}
