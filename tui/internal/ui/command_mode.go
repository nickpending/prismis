package ui

import (
	"strings"
	"time"

	"github.com/charmbracelet/bubbles/textinput"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/nickpending/prismis-local/internal/commands"
)

// CommandMode represents the neovim-style command mode
type CommandMode struct {
	active      bool
	input       textinput.Model
	history     []string
	historyIdx  int
	suggestions []string
	registry    *commands.Registry
	width       int
	error       string // Error message to display
}

// clearErrorMsg is sent to clear command error after delay
type clearErrorMsg struct{}

// NewCommandMode creates a new command mode instance
func NewCommandMode() CommandMode {
	ti := textinput.New()
	ti.Placeholder = ""
	ti.CharLimit = 256
	ti.Width = 50
	ti.Prompt = ":"
	
	return CommandMode{
		active:      false,
		input:       ti,
		history:     make([]string, 0, 100),
		historyIdx:  -1,
		suggestions: []string{},
		registry:    commands.NewRegistry(),
		width:       80,
	}
}

// SetWidth updates the width of the command mode display
func (c *CommandMode) SetWidth(width int) {
	c.width = width
	c.input.Width = width - 4 // Leave some padding
}

// Show activates command mode
func (c *CommandMode) Show() {
	c.active = true
	c.input.Focus()
	c.input.SetValue("")
	c.historyIdx = len(c.history)
	c.error = "" // Clear any error
}

// Hide deactivates command mode
func (c *CommandMode) Hide() {
	c.active = false
	c.input.Blur()
	c.input.SetValue("")
	c.historyIdx = -1
	c.error = ""
}

// IsActive returns whether command mode is currently active
func (c CommandMode) IsActive() bool {
	return c.active
}

// SetError sets an error message to display
func (c *CommandMode) SetError(err string) tea.Cmd {
	c.error = err
	// Keep command mode active to show the error
	c.active = true
	c.input.Blur() // But unfocus input
	
	// Return command to clear error after delay
	return tea.Tick(2*time.Second, func(t time.Time) tea.Msg {
		return clearErrorMsg{}
	})
}

// Update handles input events for command mode
func (c *CommandMode) Update(msg tea.Msg) (CommandMode, tea.Cmd) {
	if !c.active {
		return *c, nil
	}
	
	switch msg := msg.(type) {
	case clearErrorMsg:
		// Clear the error and hide command mode
		c.error = ""
		c.Hide()
		return *c, nil
		
	case tea.KeyMsg:
		// If showing error, any key clears it
		if c.error != "" {
			c.error = ""
			c.Hide()
			return *c, nil
		}
		
		// Normal key handling
		switch msg.Type {
		case tea.KeyEscape, tea.KeyCtrlC:
			// Cancel command mode
			c.Hide()
			return *c, nil
			
		case tea.KeyEnter:
			// Execute command
			cmd := strings.TrimSpace(c.input.Value())
			if cmd == "" {
				c.Hide()
				return *c, nil
			}
			
			// Add to history
			c.addToHistory(cmd)
			
			// Parse command and arguments with quote support
			parts := parseCommandWithQuotes(cmd)
			if len(parts) == 0 {
				c.Hide()
				return *c, nil
			}
			
			cmdName := parts[0]
			args := parts[1:]
			
			// Hide command mode before executing
			c.Hide()
			
			// Execute via registry
			return *c, c.registry.Execute(cmdName, args)
			
		case tea.KeyUp:
			// Navigate history backwards
			if c.historyIdx > 0 {
				c.historyIdx--
				c.input.SetValue(c.history[c.historyIdx])
				c.input.CursorEnd()
			}
			return *c, nil
			
		case tea.KeyDown:
			// Navigate history forwards
			if c.historyIdx < len(c.history)-1 {
				c.historyIdx++
				c.input.SetValue(c.history[c.historyIdx])
				c.input.CursorEnd()
			} else if c.historyIdx == len(c.history)-1 {
				c.historyIdx = len(c.history)
				c.input.SetValue("")
			}
			return *c, nil
			
		case tea.KeyTab:
			// Tab completion
			current := c.input.Value()
			if current == "" {
				return *c, nil
			}
			
			// Get completions from registry
			completions := c.Complete(current)
			if len(completions) == 0 {
				return *c, nil
			}
			
			// If only one match, complete it
			if len(completions) == 1 {
				c.input.SetValue(completions[0])
				c.input.CursorEnd()
			} else {
				// TODO: Show multiple matches (future enhancement)
				// For now, complete to longest common prefix
				c.input.SetValue(completions[0])
				c.input.CursorEnd()
			}
			return *c, nil
			
		case tea.KeyBackspace:
			// Cancel if empty
			if c.input.Value() == "" {
				c.Hide()
				return *c, nil
			}
		}
	}
	
	// Let textinput handle other input
	var cmd tea.Cmd
	c.input, cmd = c.input.Update(msg)
	return *c, cmd
}

// View renders the command mode interface
func (c CommandMode) View() string {
	if !c.active {
		return ""
	}
	
	// If there's an error, show it with vibrant purple
	if c.error != "" {
		theme := CleanCyberTheme
		errorStyle := lipgloss.NewStyle().
			Foreground(theme.VibrantPurple). // Vibrant purple for errors
			Width(c.width).
			Padding(0, 1)
		return errorStyle.Render("Unknown command: " + c.error)
	}
	
	// Normal command line style - clean, no background
	style := lipgloss.NewStyle().
		Foreground(lipgloss.Color("#00D9FF")).
		Width(c.width).
		Padding(0, 1)
	
	return style.Render(c.input.View())
}

// Complete returns command completions for the given prefix
func (c *CommandMode) Complete(prefix string) []string {
	if c.registry == nil {
		return nil
	}
	
	// Get all command names
	commands := c.registry.GetCommands()
	
	// Filter by prefix (case-insensitive)
	var matches []string
	lowerPrefix := strings.ToLower(prefix)
	
	for _, cmd := range commands {
		if strings.HasPrefix(strings.ToLower(cmd), lowerPrefix) {
			matches = append(matches, cmd)
		}
	}
	
	return matches
}

// addToHistory adds a command to the history
func (c *CommandMode) addToHistory(cmd string) {
	// Don't add duplicates of the last command
	if len(c.history) > 0 && c.history[len(c.history)-1] == cmd {
		return
	}
	
	// Limit history size
	if len(c.history) >= 100 {
		c.history = c.history[1:]
	}
	
	c.history = append(c.history, cmd)
}

// parseCommandWithQuotes parses a command string with quote support
func parseCommandWithQuotes(cmd string) []string {
	var args []string
	var current strings.Builder
	var inQuotes bool
	var escaped bool
	
	runes := []rune(cmd)
	
	for i := 0; i < len(runes); i++ {
		r := runes[i]
		
		switch {
		case escaped:
			// Previous character was backslash, add this literally
			current.WriteRune(r)
			escaped = false
			
		case r == '\\':
			// Escape next character
			escaped = true
			
		case r == '"' && !escaped:
			// Toggle quote mode
			inQuotes = !inQuotes
			
		case r == ' ' && !inQuotes:
			// Space outside quotes - end current arg
			if current.Len() > 0 {
				args = append(args, current.String())
				current.Reset()
			}
			// Skip consecutive spaces
			for i+1 < len(runes) && runes[i+1] == ' ' {
				i++
			}
			
		default:
			// Regular character
			current.WriteRune(r)
		}
	}
	
	// Add final argument if any
	if current.Len() > 0 {
		args = append(args, current.String())
	}
	
	return args
}