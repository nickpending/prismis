package operations

import (
	"fmt"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
)

// AudioOperationMsg represents the result of an audio briefing generation operation
type AudioOperationMsg struct {
	Message  string
	Success  bool
	Error    error
	FilePath string // Path where audio file was saved
	Filename string // Filename of the audio file
}

// GenerateAudioBriefing calls the API to generate an audio briefing
func GenerateAudioBriefing() tea.Cmd {
	return func() tea.Msg {
		// Create API client
		apiClient, err := api.NewClient()
		if err != nil {
			return AudioOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}

		// Call the audio briefings API (this will block for 10-30 seconds)
		audioData, err := apiClient.GenerateAudioBriefing()
		if err != nil {
			return AudioOperationMsg{
				Message: fmt.Sprintf("Failed to generate audio briefing: %v", err),
				Success: false,
				Error:   err,
			}
		}

		return AudioOperationMsg{
			Message:  fmt.Sprintf("Briefing ready: %s", audioData.Filename),
			Success:  true,
			FilePath: audioData.FilePath,
			Filename: audioData.Filename,
		}
	}
}
