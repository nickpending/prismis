package operations

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
)

// ExtractOperationMsg represents the result of an on-demand deep extraction request.
type ExtractOperationMsg struct {
	ContentID      string
	DeepExtraction map[string]interface{}
	Success        bool
	Error          error
}

// ExtractContent calls the API to trigger deep extraction for a content entry.
// The returned tea.Cmd runs asynchronously; the API call may block 10-30s on
// the first invocation, but is idempotent thereafter (cached result returned).
func ExtractContent(contentID string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return ExtractOperationMsg{
				ContentID: contentID,
				Success:   false,
				Error:     fmt.Errorf("failed to create API client: %w", err),
			}
		}

		data, err := apiClient.ExtractEntry(contentID)
		if err != nil {
			return ExtractOperationMsg{
				ContentID: contentID,
				Success:   false,
				Error:     err,
			}
		}

		de, _ := data["deep_extraction"].(map[string]interface{})
		return ExtractOperationMsg{
			ContentID:      contentID,
			DeepExtraction: de,
			Success:        true,
		}
	}
}
