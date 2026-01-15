package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/nickpending/prismis/internal/config"
)

// globalRemoteURL stores the remote URL set via --remote flag
var (
	globalRemoteURL string
	remoteURLMu     sync.RWMutex
)

// SetRemoteURL sets the global remote URL for all API clients
func SetRemoteURL(url string) {
	remoteURLMu.Lock()
	defer remoteURLMu.Unlock()
	globalRemoteURL = url
}

// GetRemoteURL returns the global remote URL
func GetRemoteURL() string {
	remoteURLMu.RLock()
	defer remoteURLMu.RUnlock()
	return globalRemoteURL
}

// APIClient handles HTTP communication with the daemon
type APIClient struct {
	baseURL    string
	apiKey     string
	httpClient *http.Client
}

// SourceRequest represents a request to add a source
type SourceRequest struct {
	URL  string  `json:"url"`
	Type string  `json:"type,omitempty"`
	Name *string `json:"name,omitempty"`
}

// APIResponse represents the standard API response format
type APIResponse struct {
	Success bool                   `json:"success"`
	Message string                 `json:"message"`
	Data    map[string]interface{} `json:"data,omitempty"`
}

// Source represents a content source
type Source struct {
	ID          string     `json:"id"`
	URL         string     `json:"url"`
	Type        string     `json:"type"`
	Name        *string    `json:"name,omitempty"`
	Active      bool       `json:"active"`
	LastFetched *time.Time `json:"last_fetched,omitempty"`
	ErrorCount  int        `json:"error_count"`
	LastError   *string    `json:"last_error,omitempty"`
}

// SourceListResponse represents the response from GET /api/sources
type SourceListResponse struct {
	Sources []Source `json:"sources"`
	Total   int      `json:"total"`
}

// ContentItem represents a content item from the API
type ContentItem struct {
	ID                  string          `json:"id"`
	ExternalID          string          `json:"external_id"`
	SourceID            string          `json:"source_id"`
	Title               string          `json:"title"`
	URL                 string          `json:"url"`
	Content             string          `json:"content"`
	Summary             string          `json:"summary"`
	PublishedAt         apiTime         `json:"published_at"`
	FetchedAt           apiTime         `json:"fetched_at"`
	Read                bool            `json:"read"`
	Favorited           bool            `json:"favorited"`
	InterestingOverride bool            `json:"interesting_override"`
	ArchivedAt          *apiTime        `json:"archived_at"`
	Priority            *string         `json:"priority"`
	Analysis            json.RawMessage `json:"analysis"` // JSON object from API
	SourceType          string          `json:"source_type"`
	SourceName          string          `json:"source_name"`
}

// apiTime wraps time.Time to handle API's space-separated ISO8601 format
type apiTime struct {
	time.Time
}

// UnmarshalJSON parses API timestamps in "2025-11-05 17:42:11.630705+00:00" format
func (t *apiTime) UnmarshalJSON(b []byte) error {
	s := string(b)

	// Handle null
	if s == "null" {
		t.Time = time.Time{}
		return nil
	}

	// Remove quotes
	if len(s) < 2 {
		return fmt.Errorf("invalid time string: %s", s)
	}
	s = s[1 : len(s)-1]

	// Try formats in order: with timezone, with microseconds no tz, without both
	formats := []string{
		"2006-01-02 15:04:05.999999-07:00", // With microseconds and timezone
		"2006-01-02 15:04:05-07:00",        // With timezone, no microseconds
		"2006-01-02 15:04:05.999999",       // With microseconds, no timezone (fetched_at)
		"2006-01-02 15:04:05",              // No microseconds, no timezone
	}

	var parsed time.Time
	var err error
	for _, format := range formats {
		parsed, err = time.Parse(format, s)
		if err == nil {
			t.Time = parsed
			return nil
		}
	}

	return fmt.Errorf("failed to parse time %q: %w", s, err)
}

// EntriesResponse represents the response from GET /api/entries
type EntriesResponse struct {
	Items          []ContentItem          `json:"items"`
	Total          int                    `json:"total"`
	FiltersApplied map[string]interface{} `json:"filters_applied"`
}

// NewClient creates a new API client with config loading (local mode)
func NewClient() (*APIClient, error) {
	return NewClientWithURL("")
}

// NewClientWithURL creates a new API client with optional custom base URL (remote mode)
func NewClientWithURL(baseURL string) (*APIClient, error) {
	// Load configuration using the config package
	cfg, err := config.LoadConfig()
	if err != nil {
		return nil, fmt.Errorf("failed to load config: %w", err)
	}

	// Determine if we're in remote mode and get appropriate URL/key
	// Priority: provided URL > global remote URL > config remote URL > localhost
	isRemote := false
	if baseURL != "" {
		isRemote = true
	} else if GetRemoteURL() != "" {
		baseURL = GetRemoteURL()
		isRemote = true
	} else if cfg.HasRemoteConfig() {
		baseURL = cfg.GetRemoteURL()
		isRemote = true
	} else {
		baseURL = "http://localhost:8989"
	}

	// Get API key based on mode
	var apiKey string
	if isRemote {
		apiKey = cfg.GetRemoteKey()
		if apiKey == "" {
			return nil, fmt.Errorf("remote mode requires [remote].key in config.toml")
		}
	} else {
		apiKey = cfg.API.Key
		if apiKey == "" {
			return nil, fmt.Errorf("API key not found in config")
		}
	}

	return &APIClient{
		baseURL:    baseURL,
		apiKey:     apiKey,
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}, nil
}

// AddSource adds a new content source via the API
func (c *APIClient) AddSource(request SourceRequest) (*APIResponse, error) {
	// Marshal request to JSON
	jsonData, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Create HTTP request
	req, err := http.NewRequest("POST", c.baseURL+"/api/sources", bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 422 {
		return &apiResp, fmt.Errorf("validation error: %s", apiResp.Message)
	}
	if resp.StatusCode >= 400 {
		return &apiResp, fmt.Errorf("API error: %s", apiResp.Message)
	}

	return &apiResp, nil
}

// DeleteSource removes a content source via the API
func (c *APIClient) DeleteSource(sourceID string) (*APIResponse, error) {
	// Create HTTP request
	req, err := http.NewRequest("DELETE", c.baseURL+"/api/sources/"+sourceID, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 404 {
		return &apiResp, fmt.Errorf("source not found")
	}
	if resp.StatusCode >= 400 {
		return &apiResp, fmt.Errorf("API error: %s", apiResp.Message)
	}

	return &apiResp, nil
}

// UpdateSource updates a content source via the API
func (c *APIClient) UpdateSource(sourceID string, request SourceRequest) (*APIResponse, error) {
	// Marshal request to JSON
	jsonData, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Create HTTP request
	req, err := http.NewRequest("PATCH", c.baseURL+"/api/sources/"+sourceID, bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 404 {
		return &apiResp, fmt.Errorf("source not found")
	}
	if resp.StatusCode >= 400 {
		return &apiResp, fmt.Errorf("validation error: %s", apiResp.Message)
	}

	return &apiResp, nil
}

// PauseSource pauses a content source (sets inactive)
func (c *APIClient) PauseSource(sourceID string) (*APIResponse, error) {
	// Create HTTP request
	req, err := http.NewRequest("PATCH", c.baseURL+"/api/sources/"+sourceID+"/pause", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for API-level errors
	if !apiResp.Success {
		return &apiResp, fmt.Errorf("%s", apiResp.Message)
	}

	return &apiResp, nil
}

// ResumeSource resumes a paused content source (sets active)
func (c *APIClient) ResumeSource(sourceID string) (*APIResponse, error) {
	// Create HTTP request
	req, err := http.NewRequest("PATCH", c.baseURL+"/api/sources/"+sourceID+"/resume", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for API-level errors
	if !apiResp.Success {
		return &apiResp, fmt.Errorf("%s", apiResp.Message)
	}

	return &apiResp, nil
}

// GetSources retrieves all content sources from the API
func (c *APIClient) GetSources() (*SourceListResponse, error) {
	// Create HTTP request
	req, err := http.NewRequest("GET", c.baseURL+"/api/sources", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode >= 400 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("API error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("API error: status %d", resp.StatusCode)
	}

	// Parse the wrapped response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w (body: %s)", err, string(body))
	}

	// Check if operation was successful
	if !apiResp.Success {
		return nil, fmt.Errorf("API error: %s", apiResp.Message)
	}

	// Extract the sources from the data field
	var sourceList SourceListResponse
	if data, ok := apiResp.Data["sources"].([]interface{}); ok {
		// Convert []interface{} to []Source
		sources := make([]Source, 0, len(data))
		for _, item := range data {
			// Marshal and unmarshal to convert map to Source struct
			jsonBytes, err := json.Marshal(item)
			if err != nil {
				continue
			}
			var source Source
			if err := json.Unmarshal(jsonBytes, &source); err != nil {
				continue
			}
			sources = append(sources, source)
		}
		sourceList.Sources = sources
	}

	// Extract total count
	if total, ok := apiResp.Data["total"].(float64); ok {
		sourceList.Total = int(total)
	}

	return &sourceList, nil
}

// ContentUpdateRequest represents a request to update content properties
type ContentUpdateRequest struct {
	Read                *bool `json:"read,omitempty"`
	Favorited           *bool `json:"favorited,omitempty"`
	InterestingOverride *bool `json:"interesting_override,omitempty"`
}

// UpdateContent updates content properties (read/favorited status)
func (c *APIClient) UpdateContent(contentID string, request ContentUpdateRequest) (*APIResponse, error) {
	// Marshal request to JSON
	jsonData, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Create HTTP request
	req, err := http.NewRequest("PATCH", c.baseURL+"/api/entries/"+contentID, bytes.NewBuffer(jsonData))
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 404 {
		return &apiResp, fmt.Errorf("content not found")
	}
	if resp.StatusCode == 422 {
		return &apiResp, fmt.Errorf("validation error: %s", apiResp.Message)
	}
	if resp.StatusCode >= 400 {
		return &apiResp, fmt.Errorf("API error: %s", apiResp.Message)
	}

	return &apiResp, nil
}

// FetchEntries retrieves all content items from the API
func (c *APIClient) FetchEntries() ([]ContentItem, error) {
	return c.fetchEntriesWithParams("limit=10000")
}

// FetchEntriesSince retrieves content items created/modified after the given timestamp
func (c *APIClient) FetchEntriesSince(since time.Time) ([]ContentItem, error) {
	// Format timestamp as ISO8601 with nanosecond precision
	// RFC3339Nano preserves microseconds to prevent re-fetching same items
	sinceParam := since.Format(time.RFC3339Nano)
	return c.fetchEntriesWithParams("limit=10000&since=" + sinceParam)
}

// fetchEntriesWithParams is the common implementation for fetching entries
func (c *APIClient) fetchEntriesWithParams(params string) ([]ContentItem, error) {
	// Build URL with optional parameters
	url := c.baseURL + "/api/entries"
	if params != "" {
		url += "?" + params
	}

	// Create HTTP request
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Check for HTTP errors
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode >= 400 {
		return nil, fmt.Errorf("API error (status %d): %s", resp.StatusCode, string(body))
	}

	// Parse response - API returns {success, message, data: {items: [...], total: N}}
	var apiResp struct {
		Success bool            `json:"success"`
		Message string          `json:"message"`
		Data    EntriesResponse `json:"data"`
	}
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	if !apiResp.Success {
		return nil, fmt.Errorf("API error: %s", apiResp.Message)
	}

	return apiResp.Data.Items, nil
}

// PruneCount gets the count of unprioritized items that would be pruned
func (c *APIClient) PruneCount(days *int) (int, error) {
	// Build URL with optional days parameter
	url := c.baseURL + "/api/prune/count"
	if days != nil {
		url = fmt.Sprintf("%s?days=%d", url, *days)
	}

	// Create HTTP request
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return 0, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return 0, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp struct {
		Success bool   `json:"success"`
		Message string `json:"message"`
		Data    struct {
			Count      int  `json:"count"`
			DaysFilter *int `json:"days_filter"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &apiResp); err != nil {
		return 0, fmt.Errorf("failed to parse response: %w", err)
	}

	if !apiResp.Success {
		return 0, fmt.Errorf("%s", apiResp.Message)
	}

	return apiResp.Data.Count, nil
}

// PruneUnprioritized deletes unprioritized content items
func (c *APIClient) PruneUnprioritized(days *int) (int, error) {
	// Build URL with optional days parameter
	url := c.baseURL + "/api/prune"
	if days != nil {
		url = fmt.Sprintf("%s?days=%d", url, *days)
	}

	// Create HTTP request
	req, err := http.NewRequest("POST", url, nil)
	if err != nil {
		return 0, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Send request
	resp, err := c.httpClient.Do(req)
	if err != nil {
		return 0, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, fmt.Errorf("failed to read response: %w", err)
	}

	// Parse response
	var apiResp struct {
		Success bool   `json:"success"`
		Message string `json:"message"`
		Data    struct {
			Deleted    int  `json:"deleted"`
			DaysFilter *int `json:"days_filter"`
		} `json:"data"`
	}

	if err := json.Unmarshal(body, &apiResp); err != nil {
		return 0, fmt.Errorf("failed to parse response: %w", err)
	}

	if !apiResp.Success {
		return 0, fmt.Errorf("%s", apiResp.Message)
	}

	return apiResp.Data.Deleted, nil
}

// AudioBriefingResponse represents the response from POST /api/audio/briefings
type AudioBriefingResponse struct {
	FilePath          string `json:"file_path"`
	Filename          string `json:"filename"`
	DurationEstimate  string `json:"duration_estimate"`
	GeneratedAt       string `json:"generated_at"`
	Provider          string `json:"provider"`
	HighPriorityCount int    `json:"high_priority_count"`
}

// GenerateAudioBriefing generates an audio briefing from HIGH priority content
func (c *APIClient) GenerateAudioBriefing() (*AudioBriefingResponse, error) {
	// Create HTTP request - no body needed for POST
	req, err := http.NewRequest("POST", c.baseURL+"/api/audio/briefings", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Use longer timeout for audio generation (60 seconds)
	client := &http.Client{Timeout: 60 * time.Second}

	// Send request
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 422 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("validation error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("validation error: check if HIGH priority content exists")
	}
	if resp.StatusCode == 500 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("server error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("server error: audio generation failed")
	}
	if resp.StatusCode >= 400 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("API error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("API error: status %d", resp.StatusCode)
	}

	// Parse the wrapped response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check if operation was successful
	if !apiResp.Success {
		return nil, fmt.Errorf("API error: %s", apiResp.Message)
	}

	// Extract the audio briefing data from the data field
	var audioResp AudioBriefingResponse
	if data, ok := apiResp.Data["file_path"].(string); ok {
		audioResp.FilePath = data
	}
	if data, ok := apiResp.Data["filename"].(string); ok {
		audioResp.Filename = data
	}
	if data, ok := apiResp.Data["duration_estimate"].(string); ok {
		audioResp.DurationEstimate = data
	}
	if data, ok := apiResp.Data["generated_at"].(string); ok {
		audioResp.GeneratedAt = data
	}
	if data, ok := apiResp.Data["provider"].(string); ok {
		audioResp.Provider = data
	}
	if data, ok := apiResp.Data["high_priority_count"].(float64); ok {
		audioResp.HighPriorityCount = int(data)
	}

	return &audioResp, nil
}

// TopicSuggestion represents a suggested topic for context.md
type TopicSuggestion struct {
	Topic         string  `json:"topic"`
	Section       string  `json:"section"`        // "high", "medium", or "low"
	Action        string  `json:"action"`         // "expand", "narrow", "add", "split"
	ExistingTopic *string `json:"existing_topic"` // null if action=add
	GapAnalysis   string  `json:"gap_analysis"`
	Rationale     string  `json:"rationale"`
}

// ContextSuggestionsResponse contains LLM-generated topic suggestions
type ContextSuggestionsResponse struct {
	SuggestedTopics []TopicSuggestion `json:"suggested_topics"`
}

// GetContextSuggestions analyzes flagged items and suggests topics for context.md
func (c *APIClient) GetContextSuggestions() (*ContextSuggestionsResponse, error) {
	// Create HTTP request - no body needed for POST
	req, err := http.NewRequest("POST", c.baseURL+"/api/context", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	// Set headers
	req.Header.Set("X-API-Key", c.apiKey)

	// Use longer timeout for LLM analysis (30 seconds)
	client := &http.Client{Timeout: 30 * time.Second}

	// Send request
	resp, err := client.Do(req)
	if err != nil {
		return nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	// Read response body
	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("failed to read response: %w", err)
	}

	// Check for specific HTTP status codes
	if resp.StatusCode == 403 {
		return nil, fmt.Errorf("authentication failed: invalid API key")
	}
	if resp.StatusCode == 422 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("validation error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("validation error: flag some items first using 'i' key")
	}
	if resp.StatusCode == 500 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("server error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("server error: context analysis failed")
	}
	if resp.StatusCode >= 400 {
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			return nil, fmt.Errorf("API error: %s", apiResp.Message)
		}
		return nil, fmt.Errorf("API error: status %d", resp.StatusCode)
	}

	// Parse the wrapped response
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return nil, fmt.Errorf("failed to parse response: %w", err)
	}

	// Check if operation was successful
	if !apiResp.Success {
		return nil, fmt.Errorf("API error: %s", apiResp.Message)
	}

	// Extract suggested_topics from Data map
	suggestedTopicsData, ok := apiResp.Data["suggested_topics"]
	if !ok {
		return nil, fmt.Errorf("response missing suggested_topics field")
	}

	// Marshal and unmarshal to convert to proper type
	suggestedTopicsJSON, err := json.Marshal(suggestedTopicsData)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal suggested_topics: %w", err)
	}

	var contextResp ContextSuggestionsResponse
	if err := json.Unmarshal(suggestedTopicsJSON, &contextResp.SuggestedTopics); err != nil {
		return nil, fmt.Errorf("failed to unmarshal suggested_topics: %w", err)
	}

	return &contextResp, nil
}
