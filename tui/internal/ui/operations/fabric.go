package operations

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/fabric"
)

// FabricOperationMsg represents the result of a Fabric operation
type FabricOperationMsg struct {
	Message  string
	Success  bool
	Error    error
	Result   string // Pattern execution result
	Patterns []string // List of available patterns
}

// ExecuteFabricCommand handles fabric pattern execution
func ExecuteFabricCommand(pattern string, listOnly bool, content string) tea.Cmd {
	return func() tea.Msg {
		// Check if Fabric is available
		detector := fabric.NewDetector()
		if !detector.Check() {
			return FabricOperationMsg{
				Message: "Fabric is not installed. Install from https://github.com/fabric-ai/fabric",
				Success: false,
				Error:   fmt.Errorf("fabric not found in PATH"),
			}
		}

		// Validate pattern exists
		patterns := fabric.NewPatterns()
		if !patterns.ValidatePattern(pattern) {
				// Find similar patterns for suggestions
			availablePatterns := patterns.FilterPatterns(pattern[:min(3, len(pattern))])
			suggestion := ""
			if len(availablePatterns) > 0 {
				suggestion = fmt.Sprintf("\nDid you mean: %s", strings.Join(availablePatterns[:min(3, len(availablePatterns))], ", "))
				if len(availablePatterns) > 3 {
					suggestion += fmt.Sprintf(" (and %d more)", len(availablePatterns)-3)
				}
			}

			return FabricOperationMsg{
				Message: fmt.Sprintf("Pattern '%s' not found. Use tab completion to see available patterns.%s", pattern, suggestion),
				Success: false,
				Error:   fmt.Errorf("invalid pattern: %s", pattern),
			}
		}

		// Check if full content is available
		if content == "" {
			return FabricOperationMsg{
				Message: "No full content available for this item. Fabric requires complete article text.",
				Success: false,
				Error:   fmt.Errorf("no full content to process"),
			}
		}

		// Execute the pattern
		executor := fabric.NewExecutor()
		result, err := executor.ExecutePattern(pattern, content)
		if err != nil {
			return FabricOperationMsg{
				Message: fmt.Sprintf("Failed to execute pattern '%s': %v", pattern, err),
				Success: false,
				Error:   err,
			}
		}

		return FabricOperationMsg{
			Message: fmt.Sprintf("Pattern '%s' executed and copied to clipboard", pattern),
			Success: true,
			Result:  result,
		}
	}
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}