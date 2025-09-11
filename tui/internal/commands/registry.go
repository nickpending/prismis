package commands

import (
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
	
	// Register built-in commands
	r.Register("q", cmdQuit)
	r.Register("quit", cmdQuit)
	r.Register("refresh", cmdRefresh)
	r.Register("help", cmdHelp)
	r.Register("add", cmdAdd)
	r.Register("remove", cmdRemove)
	r.Register("rm", cmdRemove) // Alias for remove
	r.Register("logs", cmdLogs)
	r.Register("cleanup", cmdCleanup)
	r.Register("pause", cmdPause)
	r.Register("resume", cmdResume)
	
	return r
}

// Register adds a command to the registry
func (r *Registry) Register(name string, fn CommandFunc) {
	r.commands[name] = fn
}

// Execute runs a command by name with arguments
func (r *Registry) Execute(name string, args []string) tea.Cmd {
	if fn, ok := r.commands[name]; ok {
		return fn(args)
	}
	// Return error with just the command name
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

// cmdRefresh triggers a content refresh
func cmdRefresh(args []string) tea.Cmd {
	return func() tea.Msg {
		return RefreshMsg{}
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

// cmdCleanup removes unprioritized content
func cmdCleanup(args []string) tea.Cmd {
	return func() tea.Msg {
		return CleanupMsg{}
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

// showError returns a command that shows an error message
func showError(msg string) tea.Cmd {
	return func() tea.Msg {
		return ErrorMsg{Message: msg}
	}
}

// Message types for commands

// RefreshMsg signals that content should be refreshed
type RefreshMsg struct{}

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
type CleanupMsg struct{}

// PauseSourceMsg signals to pause a source
type PauseSourceMsg struct {
	URL string
}

// ResumeSourceMsg signals to resume a source
type ResumeSourceMsg struct {
	URL string
}