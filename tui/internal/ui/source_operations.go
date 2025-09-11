package ui

import (
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis-local/internal/api"
)

// normalizeSourceURL normalizes special protocol URLs to real URLs (copied from daemon)
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

// doAddSource is the shared function for adding sources
// Used by both the command mode (:add) and the source modal
func doAddSource(url string, name string) tea.Cmd {
	return func() tea.Msg {
		// Create API client
		apiClient, err := api.NewClient()
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to create API client: %v", err),
				success: false,
			}
		}
		
		// Detect source type
		sourceType := detectSourceType(url)
		
		// Create request
		request := api.SourceRequest{
			URL:  url,
			Type: sourceType,
		}
		
		// Add name if provided (modal provides this, command doesn't)
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
			
			return sourceOperationSuccessMsg{
				message: message,
				success: false,
			}
		}
		
		// Success - return success message with source info
		// Extract the name from the response data if available
		sourceName := url
		if resp.Data != nil {
			if name, ok := resp.Data["name"].(string); ok && name != "" {
				sourceName = name
			}
		}
		
		return sourceOperationSuccessMsg{
			message: fmt.Sprintf("✓ Added %s source: %s", sourceType, sourceName),
			success: true,
		}
	}
}

// doRemoveSource removes a source by database ID or URL
func doRemoveSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to create API client: %v", err),
				success: false,
			}
		}
		
		// Use shared helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: err.Error(),
				success: false,
			}
		}
		
		// Delete the source by ID
		_, err = apiClient.DeleteSource(sourceID)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to remove source: %v", err),
				success: false,
			}
		}
		
		return sourceOperationSuccessMsg{
			message: fmt.Sprintf("✓ Removed source: %s", sourceName),
			success: true,
		}
	}
}

// isUUID checks if a string looks like a UUID (simple check)
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
// Returns sourceID and sourceName if found, empty strings if not
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
	// 1. Exact URL match
	// 2. Normalized URL match  
	// 3. Name match (case insensitive)
	for _, source := range sourcesResp.Sources {
		// Check URL matches
		if source.URL == identifier || source.URL == normalizedURL {
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

// doRefreshSources triggers a manual refresh of all sources
func doRefreshSources() tea.Cmd {
	return func() tea.Msg {
		// There's no RefreshSources API endpoint
		// The refresh command in the registry already triggers a UI refresh
		// This is a placeholder for future API implementation
		return sourceOperationSuccessMsg{
			message: "✓ UI refreshed - use daemon to trigger source fetch",
			success: true,
		}
	}
}

// doCleanupUnprioritized removes all unprioritized content
func doCleanupUnprioritized() tea.Cmd {
	return func() tea.Msg {
		// For now, return a message that cleanup needs confirmation
		// In the future, this could call an API endpoint
		return sourceOperationSuccessMsg{
			message: "Cleanup command not yet implemented - use CLI for now",
			success: false,
		}
	}
}

// doShowLogs returns the last N lines of daemon logs
func doShowLogs() tea.Cmd {
	return func() tea.Msg {
		// For now, return a message to check logs via CLI
		// In the future, this could fetch logs from the daemon
		return sourceOperationSuccessMsg{
			message: "Use 'prismis-cli logs' to view daemon logs",
			success: false,
		}
	}
}

// doPauseSource pauses a source by ID or URL
func doPauseSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to create API client: %v", err),
				success: false,
			}
		}
		
		// Use shared helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: err.Error(),
				success: false,
			}
		}
		
		// Pause the source
		_, err = apiClient.PauseSource(sourceID)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to pause source: %v", err),
				success: false,
			}
		}
		
		return sourceOperationSuccessMsg{
			message: fmt.Sprintf("⏸ Paused source: %s", sourceName),
			success: true,
		}
	}
}

// doResumeSource resumes a paused source by ID or URL
func doResumeSource(identifier string) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to create API client: %v", err),
				success: false,
			}
		}
		
		// Use shared helper to find source
		sourceID, sourceName, err := lookupSourceByIdentifier(identifier, apiClient)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: err.Error(),
				success: false,
			}
		}
		
		// Resume the source
		_, err = apiClient.ResumeSource(sourceID)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to resume source: %v", err),
				success: false,
			}
		}
		
		return sourceOperationSuccessMsg{
			message: fmt.Sprintf("▶ Resumed source: %s", sourceName),
			success: true,
		}
	}
}

// doUpdateSource updates a source (name, category, etc.)
func doUpdateSource(sourceID string, updates map[string]interface{}) tea.Cmd {
	return func() tea.Msg {
		apiClient, err := api.NewClient()
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to create API client: %v", err),
				success: false,
			}
		}
		
		// Build the update request
		request := api.SourceRequest{}
		
		// Set URL if provided
		if url, ok := updates["url"].(string); ok {
			request.URL = url
		}
		
		// Set name if provided
		if name, ok := updates["name"].(string); ok && name != "" {
			request.Name = &name
		}
		
		// Set type if provided (though usually we keep the same type)
		if sourceType, ok := updates["type"].(string); ok {
			request.Type = sourceType
		}
		
		// Call the update API
		resp, err := apiClient.UpdateSource(sourceID, request)
		if err != nil {
			return sourceOperationSuccessMsg{
				message: fmt.Sprintf("Failed to update source: %v", err),
				success: false,
			}
		}
		
		// Extract the updated source name for the success message
		sourceName := "source"
		if resp.Data != nil {
			if name, ok := resp.Data["name"].(string); ok && name != "" {
				sourceName = name
			}
		}
		
		return sourceOperationSuccessMsg{
			message: fmt.Sprintf("✓ Updated source: %s", sourceName),
			success: true,
		}
	}
}

