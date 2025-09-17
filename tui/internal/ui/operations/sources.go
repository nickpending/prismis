package operations

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/api"
)

// Source operation result messages
type SourceOperationMsg struct {
	Message string
	Success bool
	Error   error
}

// AddSource adds a new source
func AddSource(url string, name string) tea.Cmd {
	return func() tea.Msg {
		// Create API client
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Detect source type
		sourceType := detectSourceType(url)
		
		// Create request
		request := api.SourceRequest{
			URL:  url,
			Type: sourceType,
		}
		
		// Add name if provided
		if name != "" {
			request.Name = &name
		}
		
		// Call API
		resp, err := apiClient.AddSource(request)
		if err != nil {
			// Parse error for user-friendly message
			errStr := err.Error()
			var message string
			
			if strings.Contains(errStr, "validation error") {
				// Extract the actual validation error message
				parts := strings.SplitN(errStr, ": ", 2)
				if len(parts) >= 2 {
					message = parts[1]
				} else {
					message = errStr
				}
			} else if strings.Contains(errStr, "network error") {
				message = "Cannot connect to daemon - is it running?"
			} else if strings.Contains(errStr, "already exists") {
				message = "Source already exists"
			} else {
				message = fmt.Sprintf("Failed to add source: %v", err)
			}
			
			return SourceOperationMsg{
				Message: message,
				Success: false,
				Error:   err,
			}
		}
		
		// Success - return success message with source info
		sourceName := url
		if resp.Data != nil {
			if name, ok := resp.Data["name"].(string); ok && name != "" {
				sourceName = name
			}
		}
		
		return SourceOperationMsg{
			Message: fmt.Sprintf("✓ Added %s source: %s", sourceType, sourceName),
			Success: true,
			Error:   nil,
		}
	}
}

// RemoveSource removes a source by ID, URL, or name
func RemoveSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Use helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return SourceOperationMsg{
				Message: err.Error(),
				Success: false,
				Error:   err,
			}
		}
		
		// Delete the source by ID
		_, err = apiClient.DeleteSource(sourceID)
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to remove source: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		return SourceOperationMsg{
			Message: fmt.Sprintf("✓ Removed source: %s", sourceName),
			Success: true,
			Error:   nil,
		}
	}
}

// PauseSource pauses a source by ID, URL, or name
func PauseSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Use helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return SourceOperationMsg{
				Message: err.Error(),
				Success: false,
				Error:   err,
			}
		}
		
		// Pause the source
		_, err = apiClient.PauseSource(sourceID)
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to pause source: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		return SourceOperationMsg{
			Message: fmt.Sprintf("⏸ Paused source: %s", sourceName),
			Success: true,
			Error:   nil,
		}
	}
}

// ResumeSource resumes a paused source by ID, URL, or name
func ResumeSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Use helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return SourceOperationMsg{
				Message: err.Error(),
				Success: false,
				Error:   err,
			}
		}
		
		// Resume the source
		_, err = apiClient.ResumeSource(sourceID)
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to resume source: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		return SourceOperationMsg{
			Message: fmt.Sprintf("▶ Resumed source: %s", sourceName),
			Success: true,
			Error:   nil,
		}
	}
}

// EditSourceName edits the name of a source
func EditSourceName(identifier string, newName string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Use helper to find source
		sourceID, _, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return SourceOperationMsg{
				Message: err.Error(),
				Success: false,
				Error:   err,
			}
		}
		
		// Get the current source data to preserve URL and Type
		sourcesResp, err := apiClient.GetSources()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to get source details: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Find the source to get current URL and Type
		var currentURL, currentType string
		for _, source := range sourcesResp.Sources {
			if source.ID == sourceID {
				currentURL = source.URL
				currentType = source.Type
				break
			}
		}
		
		if currentURL == "" || currentType == "" {
			return SourceOperationMsg{
				Message: "Could not find source details",
				Success: false,
				Error:   fmt.Errorf("source not found"),
			}
		}
		
		// Update with current URL/Type and new name
		updates := map[string]interface{}{
			"url":  currentURL,
			"type": currentType,
			"name": newName,
		}
		
		// Use UpdateSource
		return UpdateSource(sourceID, updates)()
	}
}

// UpdateSource updates a source with the given changes
func UpdateSource(sourceID string, updates map[string]interface{}) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to create API client: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Build the update request
		// URL and Type are REQUIRED by the API
		request := api.SourceRequest{}
		
		// Set URL (required)
		if url, ok := updates["url"].(string); ok && url != "" {
			request.URL = url
		} else {
			return SourceOperationMsg{
				Message: "URL is required for update",
				Success: false,
				Error:   fmt.Errorf("missing URL"),
			}
		}
		
		// Set type (required) 
		if sourceType, ok := updates["type"].(string); ok && sourceType != "" {
			request.Type = sourceType
		} else {
			return SourceOperationMsg{
				Message: "Type is required for update",
				Success: false,
				Error:   fmt.Errorf("missing type"),
			}
		}
		
		// Set name if provided (optional)
		if name, ok := updates["name"].(string); ok && name != "" {
			request.Name = &name
		}
		
		// Call the update API
		resp, err := apiClient.UpdateSource(sourceID, request)
		if err != nil {
			return SourceOperationMsg{
				Message: fmt.Sprintf("Failed to update source: %v", err),
				Success: false,
				Error:   err,
			}
		}
		
		// Extract the updated source name for the success message
		sourceName := "source"
		if resp.Data != nil {
			if name, ok := resp.Data["name"].(string); ok && name != "" {
				sourceName = name
			}
		}
		
		return SourceOperationMsg{
			Message: fmt.Sprintf("✓ Updated source: %s", sourceName),
			Success: true,
			Error:   nil,
		}
	}
}

// RefreshSources triggers a manual refresh of all sources
func RefreshSources() tea.Cmd {
	return func() tea.Msg {
		// There's no RefreshSources API endpoint
		// The refresh command in the registry already triggers a UI refresh
		// This is a placeholder for future API implementation
		return SourceOperationMsg{
			Message: "✓ UI refreshed - use daemon to trigger source fetch",
			Success: true,
			Error:   nil,
		}
	}
}

// CleanupUnprioritized removes all unprioritized content
func CleanupUnprioritized() tea.Cmd {
	return func() tea.Msg {
		// For now, return a message that cleanup needs confirmation
		// In the future, this could call an API endpoint
		return SourceOperationMsg{
			Message: "Cleanup command not yet implemented - use CLI for now",
			Success: false,
			Error:   fmt.Errorf("not implemented"),
		}
	}
}

// ShowLogs returns the last N lines of daemon logs
func ShowLogs() tea.Cmd {
	return func() tea.Msg {
		// For now, return a message to check logs via CLI
		// In the future, this could fetch logs from the daemon
		return SourceOperationMsg{
			Message: "Use 'prismis-cli logs' to view daemon logs",
			Success: false,
			Error:   fmt.Errorf("not implemented"),
		}
	}
}

// Helper functions

// detectSourceType detects the type of source from the URL
func detectSourceType(url string) string {
	url = strings.ToLower(url)
	
	if strings.Contains(url, "reddit.com") || strings.HasPrefix(url, "reddit://") {
		return "reddit"
	} else if strings.Contains(url, "youtube.com") || strings.Contains(url, "youtu.be") || strings.HasPrefix(url, "youtube://") {
		return "youtube"
	} else {
		// Default to RSS for everything else
		return "rss"
	}
}

// normalizeSourceURL normalizes special protocol URLs to real URLs
func normalizeSourceURL(url string, sourceType string) string {
	url = strings.TrimSpace(url)

	if sourceType == "reddit" {
		if strings.HasPrefix(url, "reddit://") {
			// Convert reddit://rust to https://www.reddit.com/r/rust
			subreddit := url[9:]
			subreddit = strings.Trim(subreddit, "/")
			return fmt.Sprintf("https://www.reddit.com/r/%s", subreddit)
		}
	} else if sourceType == "youtube" {
		if strings.HasPrefix(url, "youtube://") {
			// Convert youtube:// URLs to real YouTube URLs
			channel := url[10:]
			channel = strings.Trim(channel, "/")

			// Handle @username format
			if strings.HasPrefix(channel, "@") {
				return fmt.Sprintf("https://www.youtube.com/%s", channel)
			}

			// Handle channel IDs (usually start with UC)
			if strings.HasPrefix(channel, "UC") {
				return fmt.Sprintf("https://www.youtube.com/channel/%s", channel)
			}

			// Default to @handle format
			return fmt.Sprintf("https://www.youtube.com/@%s", channel)
		}
	}

	// Handle RSS protocol
	if sourceType == "rss" {
		if strings.HasPrefix(url, "rss://") {
			// Convert rss://example.com/feed to https://example.com/feed
			feedURL := url[6:]
			feedURL = strings.TrimPrefix(feedURL, "//")
			
			// Add https:// if not already present
			if !strings.HasPrefix(feedURL, "http://") && !strings.HasPrefix(feedURL, "https://") {
				return fmt.Sprintf("https://%s", feedURL)
			}
			return feedURL
		}
	}
	
	// For already-normalized URLs, return as-is
	return url
}

// isUUID checks if a string looks like a UUID
func isUUID(s string) bool {
	// Simple UUID format check: 8-4-4-4-12 characters with hyphens
	if len(s) != 36 {
		return false
	}
	if s[8] != '-' || s[13] != '-' || s[18] != '-' || s[23] != '-' {
		return false
	}
	return true
}

// lookupSourceByIdentifier finds a source by ID, URL, or name
func lookupSourceByIdentifier(identifier string, apiClient *api.APIClient) (string, string, error) {
	// If it's already a database ID, use it directly
	if isUUID(identifier) {
		return identifier, "source", nil
	}
	
	// Get all sources for lookup
	sourcesResp, err := apiClient.GetSources()
	if err != nil {
		return "", "", fmt.Errorf("failed to get sources: %v", err)
	}
	
	// Normalize the identifier as a URL (in case it's a protocol URL)
	sourceType := detectSourceType(identifier)
	normalizedURL := normalizeSourceURL(identifier, sourceType)
	
	// Try to find source by:
	// 1. Exact URL match (case insensitive)
	// 2. Normalized URL match (case insensitive)
	// 3. Name match (case insensitive)
	for _, source := range sourcesResp.Sources {
		// Check URL matches (case insensitive)
		if strings.EqualFold(source.URL, identifier) || strings.EqualFold(source.URL, normalizedURL) {
			name := source.URL
			if source.Name != nil {
				name = *source.Name
			}
			return source.ID, name, nil
		}
		
		// Check name match (case insensitive)
		if source.Name != nil && strings.EqualFold(*source.Name, identifier) {
			return source.ID, *source.Name, nil
		}
	}
	
	return "", "", fmt.Errorf("source not found: %s", identifier)
}