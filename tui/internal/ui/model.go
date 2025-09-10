package ui

import (
	"encoding/json"
	"fmt"
	"os/exec"
	"runtime"
	"time"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis-local/internal/config"
	"github.com/nickpending/prismis-local/internal/db"
	"github.com/nickpending/prismis-local/internal/service"
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
	readerModal ReaderModal // Modal for reading articles
	helpModal   HelpModal   // Modal for keyboard shortcuts help
	// Auto-refresh state
	refreshInterval time.Duration // Interval for auto-refresh (0 = disabled)
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
		readerModal:   NewReaderModal(), // Initialize reader modal
		helpModal:     NewHelpModal(),   // Initialize help modal
	}
}

// Init initializes the model and returns a command to fetch initial content
func (m Model) Init() tea.Cmd {
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
		// Update modal sizes
		m.sourceModal.SetSize(msg.Width, msg.Height)
		m.readerModal.SetSize(msg.Width, msg.Height)
		m.helpModal.SetSize(msg.Width, msg.Height)
	}

	// Handle source modal updates if it's visible
	if m.sourceModal.IsVisible() {
		// CRITICAL: Handle sourcesLoadedMsg even when modal is visible
		// Otherwise the modal never gets updated sources after add/delete
		switch msg := msg.(type) {
		case sourcesLoadedMsg:
			if msg.err == nil {
				m.sources = msg.sources
				m.sourceModal.LoadSources(msg.sources)
			}
		}

		m.sourceModal, cmd = m.sourceModal.Update(msg)
		// If modal was closed, refresh sources
		if !m.sourceModal.IsVisible() {
			return m, fetchSources()
		}
		return m, cmd
	}

	// Handle reader modal updates if it's visible
	if m.readerModal.IsVisible() {
		// Handle messages that need main model handling before the early return
		switch msg.(type) {
		case clearStatusMsg:
			m.statusMessage = ""
			// Don't pass to modal, just clear and continue
		case clearFlashMsg:
			m.flashItem = -1
			m.readerModal.flashActive = false
			// Don't pass to modal, already handled
		default:
			m.readerModal, cmd = m.readerModal.Update(msg)
		}

		// Update main cursor to match reader modal cursor (for operations)
		m.cursor = m.readerModal.cursor
		
		// Check if reader modal was just closed and refresh the list if so
		if !m.readerModal.IsVisible() {
			// Modal was closed, refresh items from database to get updated state
			// This ensures any changes made in the reader modal are reflected
			return m, fetchItemsWithState(m)
		}

		// Handle clipboard and browser operations from reader
		switch msg := msg.(type) {
		case tea.KeyMsg:
			if m.cursor < len(m.items) {
				item := m.items[m.cursor]
				switch msg.String() {
				case "y":
					// Yank URL
					err := CopyToClipboard(item.URL)
					if err != nil {
						m.statusMessage = "Failed to copy URL"
					} else {
						m.statusMessage = "URL copied to clipboard"
						// Flash the reader modal
						m.readerModal.flashActive = true
						cmds = append(cmds, flashItemCmd())
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				case "c":
					// Copy content (same as list view - use reading summary)
					readingSummary := extractReadingSummary(item.Analysis)
					if readingSummary == "" {
						m.statusMessage = "No content available"
						cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
						break
					}

					err := CopyToClipboard(readingSummary)
					if err != nil {
						m.statusMessage = "Failed to copy content"
					} else {
						m.statusMessage = "Content copied to clipboard"
						// Flash the reader modal
						m.readerModal.flashActive = true
						cmds = append(cmds, flashItemCmd())
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				case "o":
					// Open in browser
					var openCmd *exec.Cmd
					switch runtime.GOOS {
					case "darwin":
						openCmd = exec.Command("open", item.URL)
					case "linux":
						openCmd = exec.Command("xdg-open", item.URL)
					case "windows":
						openCmd = exec.Command("cmd", "/c", "start", item.URL)
					}
					if openCmd != nil {
						err := openCmd.Start()
						if err != nil {
							m.statusMessage = "Failed to open browser"
						} else {
							m.statusMessage = "Opening in browser..."
						}
						cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
					}
				case "m":
					// Toggle read/unread status
					if item.Read {
						err := service.MarkAsUnread(item.ID)
						if err != nil {
							m.statusMessage = fmt.Sprintf("Failed to mark unread: %v", err)
						} else {
							// Update both main items and reader modal items
							m.items[m.cursor].Read = false
							m.readerModal.items[m.cursor].Read = false
							m.statusMessage = "Marked as unread"
							// Flash the reader modal
							m.readerModal.flashActive = true
							cmds = append(cmds, flashItemCmd())
						}
					} else {
						err := service.MarkAsRead(item.ID)
						if err != nil {
							m.statusMessage = fmt.Sprintf("Failed to mark read: %v", err)
						} else {
							// Update both main items and reader modal items
							m.items[m.cursor].Read = true
							m.readerModal.items[m.cursor].Read = true
							m.statusMessage = "Marked as read"
							// Flash the reader modal
							m.readerModal.flashActive = true
							cmds = append(cmds, flashItemCmd())
						}
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				case "f":
					// Toggle favorite status
					newFavStatus := !item.Favorited
					err := service.ToggleFavorite(item.ID, newFavStatus)
					if err != nil {
						m.statusMessage = fmt.Sprintf("Failed to toggle favorite: %v", err)
					} else {
						// Update both main items and reader modal items
						m.items[m.cursor].Favorited = newFavStatus
						m.readerModal.items[m.cursor].Favorited = newFavStatus
						if newFavStatus {
							m.statusMessage = "★ Favorited"
						} else {
							m.statusMessage = "☆ Unfavorited"
						}
						// Flash the reader modal
						m.readerModal.flashActive = true
						cmds = append(cmds, flashItemCmd())
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				}
			}
		}

		cmds = append(cmds, cmd)
		return m, tea.Batch(cmds...)
	}

	// Handle help modal updates if it's visible
	if m.helpModal.IsVisible() {
		m.helpModal, cmd = m.helpModal.Update(msg)
		return m, cmd
	}

	// Handle view-specific updates
	if m.view == "reader" {
		// Update viewport in reader view
		m.viewport, cmd = m.viewport.Update(msg)
		cmds = append(cmds, cmd)
	}

	switch msg := msg.(type) {
	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c":
			return m, tea.Quit

		// Open reader modal
		case "enter":
			if m.view == "list" && len(m.items) > 0 && !m.readerModal.IsVisible() {
				// Ensure modal has correct size
				m.readerModal.SetSize(m.width, m.height)
				// Set items and cursor for reader modal
				m.readerModal.SetItems(m.items, m.cursor)
				// Show the modal
				m.readerModal.Show()
			}
		case "esc":
			if m.view == "reader" {
				m.view = "list"
			}

		// Toggle read/unread status
		case "m":
			if len(m.items) > 0 && m.cursor < len(m.items) {
				item := m.items[m.cursor]
				if item.Read {
					// Mark as unread
					err := service.MarkAsUnread(item.ID)
					if err != nil {
						m.statusMessage = fmt.Sprintf("Error marking unread: %v", err)
					} else {
						m.items[m.cursor].Read = false
						m.statusMessage = "Marked as unread"
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				} else {
					// Mark as read
					err := service.MarkAsRead(item.ID)
					if err != nil {
						m.statusMessage = fmt.Sprintf("Error marking read: %v", err)
					} else {
						if m.showAll {
							// When showing all items, just update the Read status
							m.items[m.cursor].Read = true
							m.statusMessage = "Marked as read"
						} else {
							// When showing unread only, remove from list
							m.items = append(m.items[:m.cursor], m.items[m.cursor+1:]...)
							// Adjust cursor if needed
							if m.cursor >= len(m.items) && m.cursor > 0 {
								m.cursor--
							}
							m.statusMessage = "Marked as read"
						}
					}
					cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
				}
			}
		// Toggle favorite status
		case "f":
			if len(m.items) > 0 && m.cursor < len(m.items) {
				item := m.items[m.cursor]
				newFavStatus := !item.Favorited
				
				err := service.ToggleFavorite(item.ID, newFavStatus)
				if err != nil {
					m.statusMessage = fmt.Sprintf("Error toggling favorite: %v", err)
				} else {
					m.items[m.cursor].Favorited = newFavStatus
					if newFavStatus {
						m.statusMessage = "★ Favorited"
					} else {
						m.statusMessage = "☆ Unfavorited"
					}
				}
				cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
			}
		// Copy article content to clipboard (reader view markdown)
		case "c":
			if len(m.items) > 0 && m.cursor < len(m.items) {
				item := m.items[m.cursor]
				// Extract the formatted reading summary (same as reader view)
				readingSummary := extractReadingSummary(item.Analysis)

				if readingSummary == "" {
					m.statusMessage = "No content available"
					cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
					break
				}

				err := CopyToClipboard(readingSummary)
				if err != nil {
					m.statusMessage = "Failed to copy content"
				} else {
					m.statusMessage = "Content copied to clipboard"
					m.flashItem = m.cursor
					cmds = append(cmds, flashItemCmd())
				}
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			}
		// Yank (copy) URL to clipboard
		case "y":
			if len(m.items) > 0 && m.cursor < len(m.items) {
				item := m.items[m.cursor]
				err := CopyToClipboard(item.URL)
				if err != nil {
					m.statusMessage = "Failed to copy URL"
				} else {
					m.statusMessage = "URL copied to clipboard"
					m.flashItem = m.cursor
					cmds = append(cmds, flashItemCmd())
				}
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			}
		// Open article URL in browser
		case "o":
			if len(m.items) > 0 && m.cursor < len(m.items) {
				item := m.items[m.cursor]
				err := openInBrowser(item.URL)
				if err != nil {
					m.statusMessage = "Failed to open browser"
				} else {
					m.statusMessage = "Opening in browser..."
				}
				cmds = append(cmds, clearStatusAfterDelay(3*time.Second))
			}

		// Navigation in list view
		case "j", "down":
			if m.view == "list" && m.cursor < len(m.items)-1 {
				m.cursor++
			}
		case "k", "up":
			if m.view == "list" && m.cursor > 0 {
				m.cursor--
			}
		case "g":
			if m.view == "list" {
				// Go to top (vim-style gg but simplified to single g)
				m.cursor = 0
			}
		case "G":
			if m.view == "list" {
				// Go to bottom
				if len(m.items) > 0 {
					m.cursor = len(m.items) - 1
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
		// Manual refresh (preserves current filters and position)
		case "r", "R":
			if m.view == "list" && !m.loading {
				// Save current item ID to restore position if possible
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
					}
				}

				return m, refreshCmd
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
			if m.view == "list" && !m.sourceModal.IsVisible() && !m.readerModal.IsVisible() {
				// Only open help from main view when no other modals are open
				m.helpModal.SetSize(m.width, m.height)
				m.helpModal.Show()
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
		// Also clear flash on reader modal if it's visible
		if m.readerModal.IsVisible() {
			m.readerModal.flashActive = false
			// No need to call UpdateContent() - the view will re-render automatically
		}

	case autoRefreshMsg:
		// Handle automatic refresh - only if not already loading and in list view
		if !m.loading && m.view == "list" && !m.sourceModal.IsVisible() && !m.readerModal.IsVisible() {
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

	case sourcesLoadedMsg:
		if msg.err == nil {
			m.sources = msg.sources
			// Also update the source modal with fresh data
			m.sourceModal.LoadSources(msg.sources)
		}

	case sourceOperationSuccessMsg:
		// Handle success message from source modal operations
		m.statusMessage = msg.message
		cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
	case readerStatusMsg:
		// Handle status message from reader modal operations
		m.statusMessage = msg.message
		cmds = append(cmds, clearStatusAfterDelay(2*time.Second))
	}

	if len(cmds) > 0 {
		return m, tea.Batch(cmds...)
	}
	return m, nil
}

// View renders the current model state
func (m Model) View() string {
	// Always render the feed as base view now
	baseView := RenderList(m)

	// Overlay reader modal if visible (with dimming)
	if m.readerModal.IsVisible() {
		// Pass status message to reader modal
		m.readerModal.mainStatusMessage = m.statusMessage
		return m.readerModal.ViewWithOverlay(baseView, m.width, m.height)
	}

	// Overlay source modal if visible (with dimming)
	if m.sourceModal.IsVisible() {
		return m.sourceModal.ViewWithOverlay(baseView, m.width, m.height)
	}

	// Overlay help modal if visible (with dimming)
	if m.helpModal.IsVisible() {
		return m.helpModal.ViewWithOverlay(baseView, m.width, m.height)
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
