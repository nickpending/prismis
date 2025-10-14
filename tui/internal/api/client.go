package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/nickpending/prismis/internal/config"
)

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

// NewClient creates a new API client with config loading
func NewClient() (*APIClient, error) {
	// Load configuration using the config package
	cfg, err := config.LoadConfig()
	if err != nil {
		return nil, fmt.Errorf("failed to load config: %w", err)
	}

	if cfg.API.Key == "" {
		return nil, fmt.Errorf("API key not found in config")
	}

	return &APIClient{
		baseURL:    "http://localhost:8989",
		apiKey:     cfg.API.Key,
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
	Read      *bool `json:"read,omitempty"`
	Favorited *bool `json:"favorited,omitempty"`
}

// UpdateContent updates content properties (read/favorited status)
func (c *APIClient) UpdateContent(contentID string, request ContentUpdateRequest) (*APIResponse, error) {
	// Marshal request to JSON
	jsonData, err := json.Marshal(request)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal request: %w", err)
	}

	// Create HTTP request
	req, err := http.NewRequest("PATCH", c.baseURL+"/api/content/"+contentID, bytes.NewBuffer(jsonData))
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
