package api

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
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
	UserFeedback        string          `json:"user_feedback"`
	ArchivedAt          *apiTime        `json:"archived_at"`
	Priority            *string         `json:"priority"`
	Analysis            json.RawMessage `json:"analysis"` // JSON object from API
	SourceType          string          `json:"source_type"`
	SourceName          string          `json:"source_name"`
}

// apiTime wraps time.Time to handle the API's RFC3339 wire format
// (2006-01-02T15:04:05Z07:00). The wire contract is documented in
// architecture/boundaries.md "API ↔ Consumers (datetime wire format)".
type apiTime struct {
	time.Time
}

// UnmarshalJSON parses API timestamps as RFC3339. The daemon's Pydantic
// json_encoders normalize every datetime field on the response side; consumers
// parse with the matching layout — no fallback list.
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

	parsed, err := time.Parse(time.RFC3339, s)
	if err != nil {
		return fmt.Errorf("failed to parse time %q: %w", s, err)
	}
	t.Time = parsed
	return nil
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

	// Use transport-level timeouts instead of total client timeout.
	// Total timeout doesn't work for large responses over slow links (e.g., 80MB over Tailscale).
	transport := &http.Transport{
		DialContext: (&net.Dialer{
			Timeout:   30 * time.Second, // Connection timeout
			KeepAlive: 30 * time.Second,
		}).DialContext,
		TLSHandshakeTimeout:   15 * time.Second,
		ResponseHeaderTimeout: 30 * time.Second, // Time to first byte
		IdleConnTimeout:       90 * time.Second,
	}

	return &APIClient{
		baseURL:    baseURL,
		apiKey:     apiKey,
		httpClient: &http.Client{Transport: transport}, // No total timeout - body can take as long as needed
	}, nil
}

// doRequest builds an *http.Request for method+path against c.baseURL, sets
// X-API-Key (and Content-Type when body is non-nil), sends it, and reads the
// response body. It returns the raw status code and bytes; every caller keeps
// its own status-code branching and JSON decoding, and every caller now acts
// on the status this returns (docs/work/dedup-triage.md cluster 1; SC-1 fixed
// the four methods that used to ignore it — see successOnlyResult and
// decodeSuccessOnlyData). When timeout > 0 the request is sent through a
// one-off *http.Client with that timeout instead of c.httpClient, matching
// the pre-extraction behavior of the three callers that need a longer
// deadline than c.httpClient's tuned transport provides — c.httpClient's
// connection pooling is intentionally not reused for them, not lost by
// accident.
func (c *APIClient) doRequest(method, path string, body []byte, timeout time.Duration) (int, []byte, error) {
	var reqBody io.Reader
	if body != nil {
		reqBody = bytes.NewBuffer(body)
	}

	req, err := http.NewRequest(method, c.baseURL+path, reqBody)
	if err != nil {
		return 0, nil, fmt.Errorf("failed to create request: %w", err)
	}

	req.Header.Set("X-API-Key", c.apiKey)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}

	httpClient := c.httpClient
	if timeout > 0 {
		httpClient = &http.Client{Timeout: timeout}
	}

	resp, err := httpClient.Do(req)
	if err != nil {
		return 0, nil, fmt.Errorf("network error: %w", err)
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return 0, nil, fmt.Errorf("failed to read response: %w", err)
	}

	return resp.StatusCode, respBody, nil
}

// decodeAPIResponse unmarshals the standard {success, message, data} envelope.
// Every method that decodes into the plain APIResponse type (as opposed to a
// method-specific anonymous struct) does this exact unmarshal-and-wrap step —
// a second literally-identical fact within cluster 1, distinct from doRequest
// itself. Callers still decide their own zero-value return and, where one
// exists, their own wrapped message on failure (GetSources's "(body: %s)"
// suffix keeps its inline form for that reason).
func decodeAPIResponse(body []byte) (APIResponse, error) {
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err != nil {
		return apiResp, fmt.Errorf("failed to parse response: %w", err)
	}
	return apiResp, nil
}

// authFailedIfForbidden reports the one status-code check whose message never
// varies by caller: every method reports the same "authentication failed"
// error on 403.
func authFailedIfForbidden(status int) error {
	if status == 403 {
		return fmt.Errorf("authentication failed: invalid API key")
	}
	return nil
}

// decodeAndCheckAuth decodes body and applies authFailedIfForbidden in one
// step — the two-call sequence every status-checking, plain-APIResponse
// method above makes back to back, immediately after doRequest returns.
func decodeAndCheckAuth(status int, body []byte) (APIResponse, error) {
	apiResp, err := decodeAPIResponse(body)
	if err != nil {
		return apiResp, err
	}
	if err := authFailedIfForbidden(status); err != nil {
		return apiResp, err
	}
	return apiResp, nil
}

// sendJSON marshals request and sends it through doRequest — the "marshal,
// check the error, then call doRequest" preamble every JSON-bodied method
// above shares, identically down to the wrapped marshal-error message.
func (c *APIClient) sendJSON(method, path string, request interface{}, timeout time.Duration) (int, []byte, error) {
	jsonData, err := json.Marshal(request)
	if err != nil {
		return 0, nil, fmt.Errorf("failed to marshal request: %w", err)
	}
	return c.doRequest(method, path, jsonData, timeout)
}

// decodeMessageOr tries to decode body as an APIResponse and, if that
// succeeds, returns "prefix: apiResp.Message"; otherwise it returns the
// caller's own literal fallback. GetSources, GenerateAudioBriefing,
// ExtractEntry, and GetContextSuggestions all make this exact "try to give a
// specific message, else fall back" decision on their non-2xx branches —
// only the prefix and fallback text differ per status code and per endpoint,
// and stay parameters rather than being merged away.
func decodeMessageOr(body []byte, prefix, fallback string) error {
	var apiResp APIResponse
	if err := json.Unmarshal(body, &apiResp); err == nil {
		return fmt.Errorf("%s: %s", prefix, apiResp.Message)
	}
	return fmt.Errorf("%s", fallback)
}

// apiErrorOrStatus is decodeMessageOr specialized for the generic >=400
// fallback every status-checking method reaches after its own specific
// status branches: "API error: <message>" when the body decodes, else
// "API error: status <code>". This exact pair repeats in GetSources,
// GenerateAudioBriefing, ExtractEntry, and GetContextSuggestions.
func apiErrorOrStatus(status int, body []byte) error {
	return decodeMessageOr(body, "API error", fmt.Sprintf("API error: status %d", status))
}

// decodeOrStatusError applies the {403, 422, 500, >=400, decode+Success}
// decision chain that GenerateAudioBriefing and GetContextSuggestions both
// make in full before extracting their own payload — identical status-by-
// status except the 422/500 fallback text, which stays a parameter.
func decodeOrStatusError(status int, body []byte, validation422Fallback, serverError500Fallback string) (APIResponse, error) {
	if err := authFailedIfForbidden(status); err != nil {
		return APIResponse{}, err
	}
	if status == 422 {
		return APIResponse{}, decodeMessageOr(body, "validation error", validation422Fallback)
	}
	if status == 500 {
		return APIResponse{}, decodeMessageOr(body, "server error", serverError500Fallback)
	}
	if status >= 400 {
		return APIResponse{}, apiErrorOrStatus(status, body)
	}

	apiResp, err := decodeAPIResponse(body)
	if err != nil {
		return apiResp, err
	}
	if !apiResp.Success {
		return apiResp, fmt.Errorf("API error: %s", apiResp.Message)
	}
	return apiResp, nil
}

// sourceOpResult applies the {403, 404, >=400} decision DeleteSource and
// UpdateSource both make against a /api/sources/{id} response: byte-identical
// except which single word ("API error" vs "validation error") introduces
// the fallback message — kept as fallbackVerb rather than merged away.
func sourceOpResult(status int, apiResp APIResponse, fallbackVerb string) (*APIResponse, error) {
	if err := authFailedIfForbidden(status); err != nil {
		return nil, err
	}
	if status == 404 {
		return &apiResp, fmt.Errorf("source not found")
	}
	if status >= 400 {
		return &apiResp, fmt.Errorf("%s: %s", fallbackVerb, apiResp.Message)
	}
	return &apiResp, nil
}

// successOnlyResult decodes body and reports an error when status is a
// non-2xx status or apiResp.Success is false. PauseSource and ResumeSource
// used to decide on apiResp.Success alone, silently accepting a non-2xx
// status whose body happened to say success:true — one of the five drift
// fixes named in the work order's "why" (work-order.json: "four TUI client
// methods skip the HTTP status check the other nine make"). SC-1 requires
// these methods return an error on a non-2xx status like the other nine;
// this is that fix, applied through the same shared helper shape as before.
func successOnlyResult(status int, body []byte) (*APIResponse, error) {
	apiResp, err := decodeAndCheckAuth(status, body)
	if err != nil {
		return nil, err
	}
	if status >= 400 || !apiResp.Success {
		return &apiResp, fmt.Errorf("%s", apiResp.Message)
	}
	return &apiResp, nil
}

// pruneCountData and pruneDeleteData are PruneCount's and PruneUnprioritized's
// distinct data shapes ("count" vs "deleted") — kept as separate types
// because the fact they carry genuinely differs; only the envelope around
// them (decodeSuccessOnlyData below) is the repeated fact.
type pruneCountData struct {
	Count      int  `json:"count"`
	DaysFilter *int `json:"days_filter"`
}

type pruneDeleteData struct {
	Deleted    int  `json:"deleted"`
	DaysFilter *int `json:"days_filter"`
}

// decodeSuccessOnlyData parses a {success, message, data} envelope into T and
// reports an error when status is non-2xx or apiResp.Success is false —
// PruneCount and PruneUnprioritized's generic counterpart to
// successOnlyResult, for callers whose data shape isn't APIResponse's untyped
// map. Same SC-1 fix: these two used to decide on apiResp.Success alone.
func decodeSuccessOnlyData[T any](status int, body []byte) (T, error) {
	var apiResp struct {
		Success bool   `json:"success"`
		Message string `json:"message"`
		Data    T      `json:"data"`
	}
	if err := json.Unmarshal(body, &apiResp); err != nil {
		var zero T
		return zero, fmt.Errorf("failed to parse response: %w", err)
	}
	if err := authFailedIfForbidden(status); err != nil {
		var zero T
		return zero, err
	}
	if status >= 400 || !apiResp.Success {
		var zero T
		return zero, fmt.Errorf("%s", apiResp.Message)
	}
	return apiResp.Data, nil
}

// AddSource adds a new content source via the API
func (c *APIClient) AddSource(request SourceRequest) (*APIResponse, error) {
	status, body, err := c.sendJSON("POST", "/api/sources", request, 0)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeAndCheckAuth(status, body)
	if err != nil {
		return nil, err
	}

	// Check for specific HTTP status codes
	if status == 422 {
		return &apiResp, fmt.Errorf("validation error: %s", apiResp.Message)
	}
	if status >= 400 {
		return &apiResp, fmt.Errorf("API error: %s", apiResp.Message)
	}

	return &apiResp, nil
}

// DeleteSource removes a content source via the API
func (c *APIClient) DeleteSource(sourceID string) (*APIResponse, error) {
	status, body, err := c.doRequest("DELETE", "/api/sources/"+sourceID, nil, 0)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeAPIResponse(body)
	if err != nil {
		return nil, err
	}

	return sourceOpResult(status, apiResp, "API error")
}

// UpdateSource updates a content source via the API
func (c *APIClient) UpdateSource(sourceID string, request SourceRequest) (*APIResponse, error) {
	status, body, err := c.sendJSON("PATCH", "/api/sources/"+sourceID, request, 0)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeAPIResponse(body)
	if err != nil {
		return nil, err
	}

	return sourceOpResult(status, apiResp, "validation error")
}

// PauseSource pauses a content source (sets inactive)
func (c *APIClient) PauseSource(sourceID string) (*APIResponse, error) {
	status, body, err := c.doRequest("PATCH", "/api/sources/"+sourceID+"/pause", nil, 0)
	if err != nil {
		return nil, err
	}

	return successOnlyResult(status, body)
}

// ResumeSource resumes a paused content source (sets active)
func (c *APIClient) ResumeSource(sourceID string) (*APIResponse, error) {
	status, body, err := c.doRequest("PATCH", "/api/sources/"+sourceID+"/resume", nil, 0)
	if err != nil {
		return nil, err
	}

	return successOnlyResult(status, body)
}

// GetSources retrieves all content sources from the API
func (c *APIClient) GetSources() (*SourceListResponse, error) {
	status, body, err := c.doRequest("GET", "/api/sources", nil, 0)
	if err != nil {
		return nil, err
	}

	// Check for specific HTTP status codes
	if err := authFailedIfForbidden(status); err != nil {
		return nil, err
	}
	if status >= 400 {
		return nil, apiErrorOrStatus(status, body)
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
	Read                *bool   `json:"read,omitempty"`
	Favorited           *bool   `json:"favorited,omitempty"`
	InterestingOverride *bool   `json:"interesting_override,omitempty"`
	UserFeedback        *string `json:"user_feedback,omitempty"`
}

// UpdateContent updates content properties (read/favorited status)
func (c *APIClient) UpdateContent(contentID string, request ContentUpdateRequest) (*APIResponse, error) {
	status, body, err := c.sendJSON("PATCH", "/api/entries/"+contentID, request, 0)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeAndCheckAuth(status, body)
	if err != nil {
		return nil, err
	}

	// Check for specific HTTP status codes
	if status == 404 {
		return &apiResp, fmt.Errorf("content not found")
	}
	if status == 422 {
		return &apiResp, fmt.Errorf("validation error: %s", apiResp.Message)
	}
	if status >= 400 {
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
	// Build path with optional parameters
	path := "/api/entries"
	if params != "" {
		path += "?" + params
	}

	status, body, err := c.doRequest("GET", path, nil, 0)
	if err != nil {
		return nil, err
	}

	// Check for HTTP errors
	if err := authFailedIfForbidden(status); err != nil {
		return nil, err
	}
	if status >= 400 {
		return nil, fmt.Errorf("API error (status %d): %s", status, string(body))
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
	// Build path with optional days parameter
	path := "/api/prune/count"
	if days != nil {
		path = fmt.Sprintf("%s?days=%d", path, *days)
	}

	status, body, err := c.doRequest("GET", path, nil, 0)
	if err != nil {
		return 0, err
	}

	data, err := decodeSuccessOnlyData[pruneCountData](status, body)
	if err != nil {
		return 0, err
	}

	return data.Count, nil
}

// PruneUnprioritized deletes unprioritized content items
func (c *APIClient) PruneUnprioritized(days *int) (int, error) {
	// Build path with optional days parameter
	path := "/api/prune"
	if days != nil {
		path = fmt.Sprintf("%s?days=%d", path, *days)
	}

	status, body, err := c.doRequest("POST", path, nil, 0)
	if err != nil {
		return 0, err
	}

	data, err := decodeSuccessOnlyData[pruneDeleteData](status, body)
	if err != nil {
		return 0, err
	}

	return data.Deleted, nil
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
	// Use longer timeout for audio generation (60 seconds)
	status, body, err := c.doRequest("POST", "/api/audio/briefings", nil, 60*time.Second)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeOrStatusError(status, body,
		"validation error: check if HIGH priority content exists",
		"server error: audio generation failed")
	if err != nil {
		return nil, err
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

// ExtractEntry triggers on-demand deep extraction for a content entry.
// Returns the data field from the API response, which contains the
// deep_extraction object on success (idempotent: repeat calls return cached result).
func (c *APIClient) ExtractEntry(contentID string) (map[string]interface{}, error) {
	// Deep extraction can take 10-30 seconds (LLM call); use a longer timeout.
	status, body, err := c.doRequest("POST", "/api/entries/"+contentID+"/extract", nil, 60*time.Second)
	if err != nil {
		return nil, err
	}

	if err := authFailedIfForbidden(status); err != nil {
		return nil, err
	}
	if status == 404 {
		return nil, fmt.Errorf("entry not found")
	}
	if status == 503 {
		// Distinguish 503 sub-codes via data.reason so the user gets an actionable
		// message. Daemon attaches reason="not_configured" or reason="circuit_open"
		// (see api_errors.py ServiceUnavailableError). Falls back to the generic
		// message when the daemon predates this contract or the field is missing.
		var apiResp APIResponse
		if err := json.Unmarshal(body, &apiResp); err == nil {
			if reason, ok := apiResp.Data["reason"].(string); ok {
				switch reason {
				case "not_configured":
					return nil, fmt.Errorf("deep extraction not configured (set llm.deep_service in config.toml)")
				case "circuit_open":
					return nil, fmt.Errorf("deep extraction service unavailable, try again shortly")
				}
			}
		}
		return nil, fmt.Errorf("deep extraction unavailable (service not configured or circuit open)")
	}
	if status >= 400 {
		return nil, apiErrorOrStatus(status, body)
	}

	apiResp, err := decodeAPIResponse(body)
	if err != nil {
		return nil, err
	}
	if !apiResp.Success {
		return nil, fmt.Errorf("API error: %s", apiResp.Message)
	}
	return apiResp.Data, nil
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
	// Use longer timeout for LLM analysis (30 seconds)
	status, body, err := c.doRequest("POST", "/api/context", nil, 30*time.Second)
	if err != nil {
		return nil, err
	}

	apiResp, err := decodeOrStatusError(status, body,
		"validation error: flag some items first using 'i' key",
		"server error: context analysis failed")
	if err != nil {
		return nil, err
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
