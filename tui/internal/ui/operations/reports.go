package operations

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
	"github.com/nickpending/prismis/internal/config"
)

// ReportOperationMsg represents the result of a report generation operation
type ReportOperationMsg struct {
	Message  string
	Success  bool
	Error    error
	FilePath string // Path where report was saved
}

// AudioOperationMsg represents the result of an audio briefing generation operation
type AudioOperationMsg struct {
	Message  string
	Success  bool
	Error    error
	FilePath string // Path where audio file was saved
	Filename string // Filename of the audio file
}

// GenerateReport calls the API to generate a report and saves it to the configured location
func GenerateReport(period string) tea.Cmd {
	return func() tea.Msg {
		// Load configuration
		cfg, err := config.LoadConfig()
		if err != nil {
			return ReportOperationMsg{
				Message: fmt.Sprintf("Failed to load config: %v", err),
				Success: false,
				Error:   err,
			}
		}

		// Validate reports configuration
		outputPath, err := cfg.GetReportsOutputPath()
		if err != nil {
			return ReportOperationMsg{
				Message: err.Error(),
				Success: false,
				Error:   err,
			}
		}

		// Ensure output directory exists
		if err := os.MkdirAll(outputPath, 0755); err != nil {
			return ReportOperationMsg{
				Message: fmt.Sprintf("Failed to create reports directory: %v", err),
				Success: false,
				Error:   err,
			}
		}

		// Create API client
		apiClient, err := api.NewClient()
		if err != nil {
			return ReportOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}

		// Call the reports API
		reportData, err := apiClient.GetReport(period)
		if err != nil {
			return ReportOperationMsg{
				Message: fmt.Sprintf("Failed to generate report: %v", err),
				Success: false,
				Error:   err,
			}
		}

		// Generate filename with timestamp
		now := time.Now()
		filename := fmt.Sprintf("daily-%s.md", now.Format("2006-01-02"))
		filePath := filepath.Join(outputPath, filename)

		// Write report to file
		if err := os.WriteFile(filePath, []byte(reportData.Markdown), 0644); err != nil {
			return ReportOperationMsg{
				Message: fmt.Sprintf("Failed to save report: %v", err),
				Success: false,
				Error:   err,
			}
		}

		return ReportOperationMsg{
			Message:  fmt.Sprintf("Report saved to %s", filePath),
			Success:  true,
			FilePath: filePath,
		}
	}
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
