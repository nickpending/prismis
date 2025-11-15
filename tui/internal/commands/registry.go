package commands

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// CommandFunc is a function that executes a command
type CommandFunc func(args []string) tea.Cmd

// Registry holds all available commands
type Registry struct {
	commands map[string]CommandFunc
}

// NewRegistry creates a new command registry with built-in commands
func NewRegistry() *Registry {
	r := &Registry{
		commands: make(map[string]CommandFunc),
	}

	// Register built-in commands (vim-style: full names only, completion handles prefixes)
	r.Register("quit", cmdQuit)
	r.Register("refresh", cmdRefresh)
	r.Register("help", cmdHelp)
	r.Register("add", cmdAdd)
	r.Register("remove", cmdRemove)
	r.Register("logs", cmdLogs)
	r.Register("unprioritized", cmdUnprioritized)
	r.Register("prune", cmdPrune)
	r.Register("prune!", cmdPruneForce)
	r.Register("pause", cmdPause)
	r.Register("resume", cmdResume)
	r.Register("edit", cmdEdit)
	r.Register("fabric", cmdFabric)

	// Reader-specific commands (actions only, not navigation)
	r.Register("mark", cmdMark)
	r.Register("favorite", cmdFavorite)
	r.Register("interesting", cmdInteresting)
	r.Register("open", cmdOpen)
	r.Register("yank", cmdYank)
	r.Register("copy", cmdCopy)

	// Theme switching
	r.Register("theme", cmdTheme)

	// Audio briefing generation
	r.Register("audio", cmdAudio)

	// Export commands
	r.Register("export", cmdExport)

	// Archive toggle
	r.Register("archived", cmdArchived)

	// Context commands
	r.Register("context", cmdContext)

	return r
}

// Register adds a command to the registry
func (r *Registry) Register(name string, fn CommandFunc) {
	r.commands[name] = fn
}

// Execute runs a command by name with arguments
func (r *Registry) Execute(name string, args []string) tea.Cmd {
	// First try exact match
	if fn, ok := r.commands[name]; ok {
		return fn(args)
	}

	// Then try prefix matching (vim-style)
	var matches []string
	var matchedFn CommandFunc
	lowerName := strings.ToLower(name)

	for cmdName, fn := range r.commands {
		if strings.HasPrefix(strings.ToLower(cmdName), lowerName) {
			matches = append(matches, cmdName)
			matchedFn = fn
		}
	}

	// If exactly one match, execute it
	if len(matches) == 1 {
		return matchedFn(args)
	}

	// If multiple matches, show ambiguous command error
	if len(matches) > 1 {
		return showError(fmt.Sprintf("Ambiguous command '%s': %s", name, strings.Join(matches, ", ")))
	}

	// No matches
	return showError(name)
}

// GetCommands returns all registered command names
func (r *Registry) GetCommands() []string {
	names := make([]string, 0, len(r.commands))
	for name := range r.commands {
		names = append(names, name)
	}
	return names
}

// Built-in command implementations

// cmdQuit exits the application
func cmdQuit(args []string) tea.Cmd {
	return tea.Quit
}

// cmdRefresh triggers a content refresh with cursor preservation
func cmdRefresh(args []string) tea.Cmd {
	return func() tea.Msg {
		return RefreshMsg{PreserveCursor: true}
	}
}

// cmdHelp shows available commands
func cmdHelp(args []string) tea.Cmd {
	return func() tea.Msg {
		return HelpMsg{}
	}
}

// cmdAdd adds a new source
func cmdAdd(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "add: URL required"}
		}

		url := args[0]
		// Return a message to trigger source addition
		return AddSourceMsg{URL: url}
	}
}

// cmdRemove removes a source
func cmdRemove(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "remove: URL required"}
		}

		identifier := args[0]
		return RemoveSourceMsg{Identifier: identifier}
	}
}

// cmdLogs shows daemon logs
func cmdLogs(args []string) tea.Cmd {
	return func() tea.Msg {
		return ShowLogsMsg{}
	}
}

// cmdUnprioritized shows count of unprioritized items
func cmdUnprioritized(args []string) tea.Cmd {
	return func() tea.Msg {
		// Parse optional age filter
		var days *int
		if len(args) > 0 {
			parsedDays := parseAge(args[0])
			if parsedDays < 0 {
				return ErrorMsg{Message: fmt.Sprintf("unprioritized: invalid age filter '%s' (use format like 7d, 2w, 1m)", args[0])}
			}
			days = &parsedDays
		}

		return PruneMsg{
			Days:      days,
			CountOnly: true,
		}
	}
}

// cmdPrune removes unprioritized content with confirmation
func cmdPrune(args []string) tea.Cmd {
	return func() tea.Msg {
		// Parse optional age filter
		var days *int
		if len(args) > 0 {
			parsedDays := parseAge(args[0])
			if parsedDays < 0 {
				return ErrorMsg{Message: fmt.Sprintf("prune: invalid age filter '%s' (use format like 7d, 2w, 1m)", args[0])}
			}
			days = &parsedDays
		}

		return PruneMsg{
			Days:      days,
			Force:     false,
			CountOnly: false,
		}
	}
}

// cmdPruneForce removes unprioritized content without confirmation
func cmdPruneForce(args []string) tea.Cmd {
	return func() tea.Msg {
		// Parse optional age filter
		var days *int
		if len(args) > 0 {
			parsedDays := parseAge(args[0])
			if parsedDays < 0 {
				return ErrorMsg{Message: fmt.Sprintf("prune!: invalid age filter '%s' (use format like 7d, 2w, 1m)", args[0])}
			}
			days = &parsedDays
		}

		return PruneMsg{
			Days:      days,
			Force:     true,
			CountOnly: false,
		}
	}
}

// parseAge parses age strings like "7d", "2w", "1m" to days
func parseAge(age string) int {
	if len(age) < 2 {
		return -1
	}

	// Extract number and unit
	numStr := age[:len(age)-1]
	unit := age[len(age)-1:]

	// Parse the number
	var num int
	if _, err := fmt.Sscanf(numStr, "%d", &num); err != nil {
		return -1
	}

	// Convert to days based on unit
	switch unit {
	case "d":
		return num
	case "w":
		return num * 7
	case "m":
		return num * 30 // Approximate
	default:
		return -1
	}
}

// cmdPause pauses a source
func cmdPause(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "pause: URL required"}
		}

		url := args[0]
		return PauseSourceMsg{URL: url}
	}
}

// cmdResume resumes a paused source
func cmdResume(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "resume: URL required"}
		}

		url := args[0]
		return ResumeSourceMsg{URL: url}
	}
}

// cmdEdit edits a source's name
func cmdEdit(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) < 2 {
			return ErrorMsg{Message: "edit: requires identifier and new name"}
		}

		identifier := args[0]
		// Join remaining args as the new name (handles spaces without quotes for now)
		newName := strings.Join(args[1:], " ")

		return EditSourceMsg{
			Identifier: identifier,
			NewName:    newName,
		}
	}
}

// Reader command implementations

// cmdMark toggles read/unread status of current article
func cmdMark(args []string) tea.Cmd {
	return func() tea.Msg {
		return MarkMsg{}
	}
}

// cmdFavorite toggles favorite status of current article
func cmdFavorite(args []string) tea.Cmd {
	return func() tea.Msg {
		return FavoriteMsg{}
	}
}

// cmdInteresting toggles interesting flag of current article
func cmdInteresting(args []string) tea.Cmd {
	return func() tea.Msg {
		return InterestingMsg{}
	}
}

// cmdOpen opens current article URL in browser
func cmdOpen(args []string) tea.Cmd {
	return func() tea.Msg {
		return OpenMsg{}
	}
}

// cmdYank copies current article URL to clipboard
func cmdYank(args []string) tea.Cmd {
	return func() tea.Msg {
		return YankMsg{}
	}
}

// cmdCopy copies current article content to clipboard
func cmdCopy(args []string) tea.Cmd {
	return func() tea.Msg {
		// Determine what to copy: "summary" (default), "content"
		target := "summary" // default
		if len(args) > 0 {
			target = args[0]
		}
		return CopyMsg{Target: target}
	}
}

// cmdAudio generates audio briefing from HIGH priority content
func cmdAudio(args []string) tea.Cmd {
	return func() tea.Msg {
		return AudioMsg{}
	}
}

// cmdExport handles export commands (currently only sources)
func cmdExport(args []string) tea.Cmd {
	return func() tea.Msg {
		// Parse subcommand
		if len(args) == 0 {
			return ErrorMsg{Message: "export: subcommand required (sources)"}
		}

		subcommand := args[0]
		switch subcommand {
		case "sources":
			return ExportSourcesMsg{}
		default:
			return ErrorMsg{Message: fmt.Sprintf("export: unknown subcommand '%s' (available: sources)", subcommand)}
		}
	}
}

// cmdFabric executes Fabric patterns on current content
func cmdFabric(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "fabric: pattern required (use tab completion to see available patterns)"}
		}

		// Execute pattern on current content
		pattern := args[0]

		return FabricMsg{
			Pattern:  pattern,
			ListOnly: false,
			Content:  "", // Content will be populated by the handler
		}
	}
}

// cmdTheme cycles through available themes
func cmdTheme(args []string) tea.Cmd {
	return func() tea.Msg {
		return ThemeMsg{}
	}
}

// cmdArchived toggles archived view
func cmdArchived(args []string) tea.Cmd {
	return func() tea.Msg {
		return ArchivedMsg{}
	}
}

// cmdContext handles context commands
func cmdContext(args []string) tea.Cmd {
	return func() tea.Msg {
		if len(args) == 0 {
			return ErrorMsg{Message: "context: subcommand required (review, suggest, edit)"}
		}

		switch args[0] {
		case "review":
			return ContextReviewMsg{}
		case "suggest":
			return ContextSuggestMsg{}
		case "edit":
			return ContextEditMsg{}
		default:
			return ErrorMsg{Message: fmt.Sprintf("context: unknown subcommand '%s' (available: review, suggest, edit)", args[0])}
		}
	}
}

// showError returns a command that shows an error message
func showError(msg string) tea.Cmd {
	return func() tea.Msg {
		return ErrorMsg{Message: msg}
	}
}

// Message types for commands

// RefreshMsg signals that content should be refreshed
type RefreshMsg struct {
	PreserveCursor bool // If true, try to maintain cursor position
}

// ErrorMsg contains an error message to display
type ErrorMsg struct {
	Message string
}

// HelpMsg signals to show the help modal
type HelpMsg struct{}

// AddSourceMsg signals to add a new source
type AddSourceMsg struct {
	URL string
}

// RemoveSourceMsg signals to remove a source
type RemoveSourceMsg struct {
	Identifier string // Can be ID or URL
}

// ShowLogsMsg signals to show daemon logs
type ShowLogsMsg struct{}

// CleanupMsg signals to cleanup unprioritized content
// PruneMsg signals to prune unprioritized content
type PruneMsg struct {
	Days      *int // Optional age filter in days
	Force     bool // If true, skip confirmation
	CountOnly bool // If true, just show count
}

// PauseSourceMsg signals to pause a source
type PauseSourceMsg struct {
	URL string
}

// ResumeSourceMsg signals to resume a source
type ResumeSourceMsg struct {
	URL string
}

// EditSourceMsg signals to edit a source's name
type EditSourceMsg struct {
	Identifier string
	NewName    string
}

// Reader command messages

// MarkMsg signals to toggle read/unread status
type MarkMsg struct{}

// FavoriteMsg signals to toggle favorite status
type FavoriteMsg struct{}

// InterestingMsg signals to toggle interesting flag
type InterestingMsg struct{}

// OpenMsg signals to open URL in browser
type OpenMsg struct{}

// YankMsg signals to copy URL to clipboard
type YankMsg struct{}

// CopyMsg signals to copy content to clipboard
type CopyMsg struct {
	Target string // "summary" (default) or "content"
}

// AudioMsg signals to generate an audio briefing
type AudioMsg struct{}

// FabricMsg signals to execute a Fabric pattern
type FabricMsg struct {
	Pattern  string // Pattern name to execute, or "--list" for pattern list
	ListOnly bool   // If true, just show available patterns
	Content  string // Content to process (populated by handler)
}

// ThemeMsg signals to cycle to the next theme
type ThemeMsg struct{}

// ExportSourcesMsg signals to export sources to clipboard
type ExportSourcesMsg struct{}

// ArchivedMsg signals to toggle archived view
type ArchivedMsg struct{}

// ContextReviewMsg signals to review flagged items
type ContextReviewMsg struct{}
type ContextSuggestMsg struct{}
type ContextEditMsg struct{}
