package operations

import (
	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/db"
)

// Context operation result messages
type ContextReviewedMsg struct {
	Count   int
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
