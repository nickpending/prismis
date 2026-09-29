package operations

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
	"github.com/nickpending/prismis/internal/commands"
)

// PruneResultMsg contains the result of a prune operation
type PruneResultMsg struct {
	Count   int
	Deleted int
	Days    *int
	Error   error
}

// PruneCountMsg contains the count for confirmation
type PruneCountMsg struct {
	Count    int
	Days     *int
	ShowOnly bool // If true, just show count without prompting for confirmation
}

// GetPruneCount gets the count of items that would be pruned
func GetPruneCount(days *int) tea.Cmd {
	return func() tea.Msg {
		apiClient, errMsg := newClientOrPruneErrMsg()
		if errMsg != nil {
			return errMsg
		}

		// Get the count
		count, err := apiClient.PruneCount(days)
		if err != nil {
			return PruneResultMsg{
				Error: fmt.Errorf("failed to get prune count: %w", err),
			}
		}

		return PruneCountMsg{
			Count: count,
			Days:  days,
		}
	}
}

// ExecutePrune performs the actual prune operation
func ExecutePrune(days *int) tea.Cmd {
	return func() tea.Msg {
		apiClient, errMsg := newClientOrPruneErrMsg()
		if errMsg != nil {
			return errMsg
		}

		// Get count first (for the message)
		count, _ := apiClient.PruneCount(days)

		// Execute the prune
		deleted, err := apiClient.PruneUnprioritized(days)
		if err != nil {
			return PruneResultMsg{
				Error: fmt.Errorf("failed to prune items: %w", err),
			}
		}

		return PruneResultMsg{
			Count:   count,
			Deleted: deleted,
			Days:    days,
		}
	}
}

// HandlePruneCommand processes the prune command with confirmation
func HandlePruneCommand(msg commands.PruneMsg) tea.Cmd {
	// If just counting, delegate to GetPruneCount and mark the result
	// show-only rather than re-deriving the count here.
	if msg.CountOnly {
		return func() tea.Msg {
			result := GetPruneCount(msg.Days)()
			if countMsg, ok := result.(PruneCountMsg); ok {
				countMsg.ShowOnly = true
				return countMsg
			}
			// PruneResultMsg (error case) passes through unchanged.
			return result
		}
	}

	// If force flag is set, execute immediately
	if msg.Force {
		return ExecutePrune(msg.Days)
	}

	// Otherwise, get count first for confirmation
	return GetPruneCount(msg.Days)
}

// newClientOrPruneErrMsg creates an API client, returning the client on
// success or a ready-to-return PruneResultMsg (as a tea.Msg) describing the
// failure. Callers check errMsg for nil before using the client.
func newClientOrPruneErrMsg() (*api.APIClient, tea.Msg) {
	apiClient, err := api.NewClient()
	if err != nil {
		return nil, PruneResultMsg{
			Error: fmt.Errorf("failed to create API client: %w", err),
		}
	}
	return apiClient, nil
}
