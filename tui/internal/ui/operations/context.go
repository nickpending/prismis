package operations

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
	"github.com/nickpending/prismis/internal/clipboard"
	"github.com/nickpending/prismis/internal/db"
)

// Context operation result messages
type ContextReviewedMsg struct {
	Count   int
	Success bool
	Error   error
}

// ContextSuggestionsMsg contains LLM-generated topic suggestions
type ContextSuggestionsMsg struct {
	Suggestions string
	Count       int
	Success     bool
	Error       error
}

// ContextEditMsg signals that context.md was opened in editor
type ContextEditMsg struct {
	Success bool
	Error   error
}

// ReviewFlaggedItems returns count of flagged items
func ReviewFlaggedItems() tea.Cmd {
	return func() tea.Msg {
		items, err := db.GetInterestingItems()
		if err != nil {
			return ContextReviewedMsg{
				Count:   0,
				Success: false,
				Error:   err,
			}
		}

		return ContextReviewedMsg{
			Count:   len(items),
			Success: true,
			Error:   nil,
		}
	}
}

// GetContextSuggestions calls API to analyze flagged items and suggest topics
func GetContextSuggestions() tea.Cmd {
	return func() tea.Msg {
		// Create API client
		apiClient, err := api.NewClient()
		if err != nil {
			return ContextSuggestionsMsg{
				Success: false,
				Error:   fmt.Errorf("failed to create API client: %w", err),
			}
		}

		// Call API to get suggestions (this will block for 3-10 seconds)
		response, err := apiClient.GetContextSuggestions()
		if err != nil {
			return ContextSuggestionsMsg{
				Success: false,
				Error:   fmt.Errorf("failed to get suggestions: %w", err),
			}
		}

		// Format suggestions as markdown for clipboard
		var formatted strings.Builder
		formatted.WriteString("# Suggested Topics for context.md\n\n")

		if len(response.SuggestedTopics) == 0 {
			formatted.WriteString("No new topics suggested.\n")
		} else {
			// Group by section
			highTopics := []api.TopicSuggestion{}
			mediumTopics := []api.TopicSuggestion{}
			lowTopics := []api.TopicSuggestion{}

			for _, suggestion := range response.SuggestedTopics {
				switch suggestion.Section {
				case "high":
					highTopics = append(highTopics, suggestion)
				case "medium":
					mediumTopics = append(mediumTopics, suggestion)
				case "low":
					lowTopics = append(lowTopics, suggestion)
				}
			}

			if len(highTopics) > 0 {
				formatted.WriteString("## High Priority Topics\n\n")
				for _, topic := range highTopics {
					formatted.WriteString(fmt.Sprintf("- %s\n", topic.Topic))
					formatted.WriteString(fmt.Sprintf("  **Action:** %s", topic.Action))
					if topic.ExistingTopic != nil {
						formatted.WriteString(fmt.Sprintf(" | **Existing:** %s", *topic.ExistingTopic))
					}
					formatted.WriteString("\n")
					formatted.WriteString(fmt.Sprintf("  **Gap:** %s\n", topic.GapAnalysis))
					formatted.WriteString(fmt.Sprintf("  *%s*\n\n", topic.Rationale))
				}
			}

			if len(mediumTopics) > 0 {
				formatted.WriteString("## Medium Priority Topics\n\n")
				for _, topic := range mediumTopics {
					formatted.WriteString(fmt.Sprintf("- %s\n", topic.Topic))
					formatted.WriteString(fmt.Sprintf("  **Action:** %s", topic.Action))
					if topic.ExistingTopic != nil {
						formatted.WriteString(fmt.Sprintf(" | **Existing:** %s", *topic.ExistingTopic))
					}
					formatted.WriteString("\n")
					formatted.WriteString(fmt.Sprintf("  **Gap:** %s\n", topic.GapAnalysis))
					formatted.WriteString(fmt.Sprintf("  *%s*\n\n", topic.Rationale))
				}
			}

			if len(lowTopics) > 0 {
				formatted.WriteString("## Low Priority Topics\n\n")
				for _, topic := range lowTopics {
					formatted.WriteString(fmt.Sprintf("- %s\n", topic.Topic))
					formatted.WriteString(fmt.Sprintf("  **Action:** %s", topic.Action))
					if topic.ExistingTopic != nil {
						formatted.WriteString(fmt.Sprintf(" | **Existing:** %s", *topic.ExistingTopic))
					}
					formatted.WriteString("\n")
					formatted.WriteString(fmt.Sprintf("  **Gap:** %s\n", topic.GapAnalysis))
					formatted.WriteString(fmt.Sprintf("  *%s*\n\n", topic.Rationale))
				}
			}
		}

		// Copy to clipboard
		formattedText := formatted.String()
		if err := clipboard.CopyToClipboard(formattedText); err != nil {
			return ContextSuggestionsMsg{
				Suggestions: formattedText,
				Count:       len(response.SuggestedTopics),
				Success:     false,
				Error:       fmt.Errorf("suggestions generated but clipboard failed: %w", err),
			}
		}

		return ContextSuggestionsMsg{
			Suggestions: formattedText,
			Count:       len(response.SuggestedTopics),
			Success:     true,
			Error:       nil,
		}
	}
}

// EditContextFile opens context.md in $EDITOR
func EditContextFile() tea.Cmd {
	// Get context.md path
	configDir := os.Getenv("XDG_CONFIG_HOME")
	if configDir == "" {
		homeDir, err := os.UserHomeDir()
		if err != nil {
			return func() tea.Msg {
				return ContextEditMsg{
					Success: false,
					Error:   fmt.Errorf("failed to get home directory: %w", err),
				}
			}
		}
		configDir = filepath.Join(homeDir, ".config")
	}
	contextPath := filepath.Join(configDir, "prismis", "context.md")

	// Check if file exists
	if _, err := os.Stat(contextPath); os.IsNotExist(err) {
		return func() tea.Msg {
			return ContextEditMsg{
				Success: false,
				Error:   fmt.Errorf("context.md not found at %s", contextPath),
			}
		}
	}

	// Get editor from environment
	editor := os.Getenv("EDITOR")
	if editor == "" {
		editor = "vim" // Fallback to vim
	}

	// Use tea.ExecProcess to properly suspend TUI and restore terminal
	c := exec.Command(editor, contextPath)
	return tea.ExecProcess(c, func(err error) tea.Msg {
		if err != nil {
			return ContextEditMsg{
				Success: false,
				Error:   fmt.Errorf("editor failed: %w", err),
			}
		}
		return ContextEditMsg{
			Success: true,
			Error:   nil,
		}
	})
}
