package api

import (
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// Helper to create a test client with proper config
func createTestClient(t *testing.T) *APIClient {
	// Check if we have a test config or daemon running
	if os.Getenv("PRISMIS_TEST_API_KEY") == "" {
		// t.Skip does not return - it unwinds via runtime.Goexit, so callers never
		// observe a nil client and must not branch on one.
		t.Skip("Set PRISMIS_TEST_API_KEY to run integration tests")
	}

	return &APIClient{
		baseURL:    "http://localhost:8989",
		apiKey:     os.Getenv("PRISMIS_TEST_API_KEY"),
		httpClient: &http.Client{Timeout: 10 * time.Second},
	}
}

func TestClientConfig(t *testing.T) {
	// Test with missing config - should return error
	// Set XDG_CONFIG_HOME to non-existent directory
	oldEnv := os.Getenv("XDG_CONFIG_HOME")
	os.Setenv("XDG_CONFIG_HOME", "/tmp/non-existent-test-dir")
	defer os.Setenv("XDG_CONFIG_HOME", oldEnv)

	_, err := NewClient()
	if err == nil {
		t.Fatal("Expected error when config file is missing")
	}
}

func TestClientConfigWithFile(t *testing.T) {
	// Create temporary config directory
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "prismis")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("Failed to create config dir: %v", err)
	}

	// Write test config
	configPath := filepath.Join(configDir, "config.toml")
	configContent := `[api]
key = "test-api-key-123"
`
	if err := os.WriteFile(configPath, []byte(configContent), 0644); err != nil {
		t.Fatalf("Failed to write config: %v", err)
	}

	// Override XDG_CONFIG_HOME
	oldEnv := os.Getenv("XDG_CONFIG_HOME")
	os.Setenv("XDG_CONFIG_HOME", tmpDir)
	defer os.Setenv("XDG_CONFIG_HOME", oldEnv)

	// Test loading config
	client, err := NewClient()
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}
	if client.apiKey != "test-api-key-123" {
		t.Errorf("Expected test-api-key-123, got %s", client.apiKey)
	}
}

func TestAddSource(t *testing.T) {
	// This test requires the daemon to be running
	// Create test config
	client := createTestClient(t)
	// Try to add a source
	req := SourceRequest{
		URL:  "https://example.com/feed.xml",
		Type: "rss",
	}

	resp, err := client.AddSource(req)
	if err != nil {
		// Check if it's a network error (daemon not running)
		if resp == nil {
			t.Skipf("Daemon not running: %v", err)
		}
		// Could be validation error which is expected
		t.Logf("AddSource error (expected if invalid feed): %v", err)
	} else {
		t.Logf("AddSource response: success=%v, message=%s", resp.Success, resp.Message)
	}
}

func TestDeleteSource(t *testing.T) {
	// This test requires the daemon to be running
	client := createTestClient(t)
	// Try to delete a non-existent source
	resp, err := client.DeleteSource("non-existent-id")
	if err != nil {
		// Expected error for non-existent source
		if resp == nil {
			t.Skipf("Daemon not running: %v", err)
		}
		t.Logf("DeleteSource error (expected): %v", err)
	} else {
		t.Logf("DeleteSource response: success=%v, message=%s", resp.Success, resp.Message)
	}
}

func TestGetSources(t *testing.T) {
	// This test requires the daemon to be running
	client := createTestClient(t)
	// Try to get sources
	sources, err := client.GetSources()
	if err != nil {
		t.Skipf("Daemon not running: %v", err)
	}

	t.Logf("GetSources: found %d sources", sources.Total)
	for _, source := range sources.Sources {
		t.Logf("  - %s (%s): %s", source.ID, source.Type, source.URL)
	}
}

// Test helper to run all integration tests with a real daemon
func TestIntegrationWithDaemon(t *testing.T) {
	// This test shows how to test with a real daemon
	t.Skip("Run this test manually with daemon running: cd daemon && PRISMIS_API_KEY=test-key uv run python -m prismis_daemon")

	// When daemon is running, all methods should work
	client, err := NewClient()
	if err != nil {
		t.Fatalf("Failed to create client: %v", err)
	}

	// Test AddSource
	req := SourceRequest{
		URL:  "https://simonwillison.net/atom/everything/",
		Type: "rss",
	}
	resp, err := client.AddSource(req)
	if err != nil {
		t.Fatalf("AddSource failed: %v", err)
	}
	t.Logf("Added source: %s", resp.Message)

	// Test GetSources
	sources, err := client.GetSources()
	if err != nil {
		t.Fatalf("GetSources failed: %v", err)
	}
	t.Logf("Found %d sources", sources.Total)

	// Test DeleteSource (if we have sources)
	if len(sources.Sources) > 0 {
		sourceID := sources.Sources[0].ID
		resp, err := client.DeleteSource(sourceID)
		if err != nil {
			t.Fatalf("DeleteSource failed: %v", err)
		}
		t.Logf("Deleted source: %s", resp.Message)
	}
}

// INVARIANT TEST: API key must never appear in error messages or logs
func TestAPIKeyNeverExposed(t *testing.T) {
	// Create client with a known API key
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "prismis")
	os.MkdirAll(configDir, 0755)

	secretKey := "super-secret-api-key-12345"
	configContent := `[api]
key = "` + secretKey + `"
`
	configPath := filepath.Join(configDir, "config.toml")
	os.WriteFile(configPath, []byte(configContent), 0644)

	oldEnv := os.Getenv("XDG_CONFIG_HOME")
	os.Setenv("XDG_CONFIG_HOME", tmpDir)
	defer os.Setenv("XDG_CONFIG_HOME", oldEnv)

	client, _ := NewClient()

	// Test with invalid endpoint to trigger error
	client.baseURL = "http://localhost:99999" // Invalid port

	// Try operations that could leak API key in errors
	_, err := client.GetSources()
	if err != nil && containsString(err.Error(), secretKey) {
		t.Fatalf("API key exposed in error: %v", err)
	}

	_, err = client.AddSource(SourceRequest{URL: "test", Type: "rss"})
	if err != nil && containsString(err.Error(), secretKey) {
		t.Fatalf("API key exposed in error: %v", err)
	}

	_, err = client.DeleteSource("test-id")
	if err != nil && containsString(err.Error(), secretKey) {
		t.Fatalf("API key exposed in error: %v", err)
	}
}

// INVARIANT TEST: Delete operations must be idempotent
func TestDeleteIdempotency(t *testing.T) {
	client := createTestClient(t)
	// Delete non-existent source twice - should not fail fatally
	nonExistentID := "definitely-does-not-exist-12345"

	// First delete
	resp1, err1 := client.DeleteSource(nonExistentID)
	if err1 == nil {
		t.Logf("First delete succeeded (unexpected): %v", resp1)
	} else {
		t.Logf("First delete error (expected): %v", err1)
	}

	// Second delete - must not cause fatal error
	resp2, err2 := client.DeleteSource(nonExistentID)
	if err2 == nil {
		t.Logf("Second delete succeeded (unexpected): %v", resp2)
	} else {
		t.Logf("Second delete error (expected): %v", err2)
	}

	// Client should still be functional
	_, err := client.GetSources()
	if err != nil {
		t.Fatalf("Client broken after idempotent delete: %v", err)
	}
}

// INVARIANT TEST: Config path must follow XDG spec exactly
func TestConfigXDGSpec(t *testing.T) {
	// Test with XDG_CONFIG_HOME set
	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)
	defer os.Unsetenv("XDG_CONFIG_HOME")

	expectedPath := filepath.Join(tmpDir, "prismis", "config.toml")
	os.MkdirAll(filepath.Dir(expectedPath), 0755)
	os.WriteFile(expectedPath, []byte(`[api]
key = "test"
`), 0644)

	_, err := NewClient()
	if err != nil {
		t.Errorf("Failed to load config from XDG path: %v", err)
	}

	// Test without XDG_CONFIG_HOME (should use ~/.config)
	os.Unsetenv("XDG_CONFIG_HOME")
	home, _ := os.UserHomeDir()
	defaultPath := filepath.Join(home, ".config", "prismis", "config.toml")

	// Just verify the path would be correct (don't actually write to user's home)
	if !filepath.IsAbs(defaultPath) {
		t.Errorf("Default config path not absolute: %s", defaultPath)
	}
}

// INVARIANT TEST: Malformed JSON must not panic
func TestMalformedJSONHandling(t *testing.T) {
	// This test would need a mock server to return bad JSON
	// Since we can't easily mock the daemon, we'll test JSON parsing directly

	// Test malformed response parsing
	malformedJSON := []string{
		`{"sources": [}`, // Broken array
		`{sources: []}`,  // Missing quotes
		`null`,           // Null response
		``,               // Empty response
		`{{{`,            // Completely broken
	}

	for _, badJSON := range malformedJSON {
		func() {
			defer func() {
				if r := recover(); r != nil {
					t.Errorf("Panic on malformed JSON '%s': %v", badJSON, r)
				}
			}()

			// This would normally be internal, but we're testing robustness
			var resp SourceListResponse
			json.Unmarshal([]byte(badJSON), &resp)
			// Should not panic
		}()
	}
}

// FAILURE TEST: Daemon unavailable must report clearly
func TestDaemonUnavailable(t *testing.T) {
	// Create client pointing to definitely unavailable daemon
	client := &APIClient{
		baseURL:    "http://localhost:44444", // Unlikely port
		apiKey:     "test",
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}

	// Test all methods report daemon unavailable clearly
	_, err := client.GetSources()
	if err == nil {
		t.Fatal("Expected error when daemon unavailable")
	}
	if !containsString(err.Error(), "network") && !containsString(err.Error(), "connection") {
		t.Errorf("Error doesn't clearly indicate network issue: %v", err)
	}

	_, err = client.AddSource(SourceRequest{URL: "test", Type: "rss"})
	if err == nil {
		t.Fatal("Expected error when daemon unavailable")
	}

	_, err = client.DeleteSource("test")
	if err == nil {
		t.Fatal("Expected error when daemon unavailable")
	}
}

// FAILURE TEST: Invalid API key must not leak the key
func TestInvalidAPIKeyNoLeak(t *testing.T) {
	client := createTestClient(t)
	// Use wrong API key
	wrongKey := "wrong-key-should-not-appear-in-errors"
	client.apiKey = wrongKey

	// Try operations with wrong key
	_, err := client.GetSources()
	if err != nil && containsString(err.Error(), wrongKey) {
		t.Fatalf("Wrong API key exposed in error: %v", err)
	}

	_, err = client.AddSource(SourceRequest{URL: "test", Type: "rss"})
	if err != nil && containsString(err.Error(), wrongKey) {
		t.Fatalf("Wrong API key exposed in error: %v", err)
	}
}

// FAILURE TEST: Network timeout must leave client usable
// Previously this pointed at http://localhost:8989 with a 1ms timeout and skipped when no
// error came back. Both halves were wrong: with no daemon running it got connection-refused
// rather than a timeout, so it "passed" without ever exercising one, and when the assertion
// it exists to make did not hold it converted that into a skip. A server under this test's
// own control makes the timeout real and the outcome deterministic.
func TestNetworkTimeoutRecovery(t *testing.T) {
	var slow atomic.Bool
	slow.Store(true)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if slow.Load() {
			time.Sleep(500 * time.Millisecond) // outlive the client deadline below
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"sources":[],"total":0}}`))
	}))
	defer server.Close()

	client := &APIClient{
		baseURL:    server.URL,
		apiKey:     "test",
		httpClient: &http.Client{Timeout: 50 * time.Millisecond},
	}

	// The request must time out - the server holds it open past the deadline.
	if _, err := client.GetSources(); err == nil {
		t.Fatal("expected a timeout from GetSources, got success")
	}

	// INVARIANT: the client survives the timeout and works on the next call.
	slow.Store(false)
	client.httpClient.Timeout = 10 * time.Second
	sources, err := client.GetSources()
	if err != nil {
		t.Fatalf("client unusable after a timeout: %v", err)
	}
	if sources == nil {
		t.Fatal("expected a decoded response after recovery, got nil")
	}
}

// Helper function to check if string contains substring
func containsString(s, substr string) bool {
	return strings.Contains(s, substr)
}

// ---------------------------------------------------------------------------
// INV-API-TS-2: apiTime.UnmarshalJSON must use exactly one layout (RFC3339).
// Parse failures must fail loud — no fallback layouts.
// ---------------------------------------------------------------------------

// TestAPITimeUnmarshalJSON_RFC3339Accepted verifies that valid RFC3339 strings
// (the wire contract) parse successfully. Covers the happy-path shapes that the
// daemon's _rfc3339() helper can produce: offset+00:00, Z suffix, fractional seconds.
func TestAPITimeUnmarshalJSON_RFC3339Accepted(t *testing.T) {
	cases := []struct {
		name  string
		input string // JSON-encoded (with quotes)
	}{
		{"with offset", `"2026-05-05T23:22:34+00:00"`},
		{"with Z suffix", `"2026-04-30T15:49:40Z"`},
		{"with fractional seconds and offset", `"2026-05-05T23:22:34.289113+00:00"`},
		{"with fractional seconds and Z", `"2026-05-05T23:14:53.680336Z"`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var at apiTime
			if err := json.Unmarshal([]byte(tc.input), &at); err != nil {
				t.Errorf("RFC3339 string %s must parse without error; got: %v", tc.input, err)
			}
			if at.Time.IsZero() {
				t.Errorf("Parsed time must not be zero for input %s", tc.input)
			}
		})
	}
}

// TestAPITimeUnmarshalJSON_SpaceSeparatorRejected verifies that the pre-fix
// space-separator formats are now rejected. Before task 2.7, the parser had a
// 4-layout fallback list that silently accepted these. The collapsed single-layout
// parser must fail loud on any non-RFC3339 input — that is what INV-API-TS-2 requires.
func TestAPITimeUnmarshalJSON_SpaceSeparatorRejected(t *testing.T) {
	cases := []struct {
		name  string
		input string // JSON-encoded (with quotes)
	}{
		{"space-sep naive", `"2026-01-06 02:14:05.692944"`},
		{"space-sep with offset", `"2025-11-24 00:00:00+00:00"`},
		{"space-sep with UTC label", `"2026-05-05 23:14:53.680336"`},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var at apiTime
			if err := json.Unmarshal([]byte(tc.input), &at); err == nil {
				t.Errorf(
					"Space-separator string %s must be rejected by the RFC3339-only parser; got nil error",
					tc.input,
				)
			}
		})
	}
}

// TestAPITimeUnmarshalJSON_NullHandled verifies that a JSON null value produces
// a zero time.Time without error — matching the existing null branch in the code.
func TestAPITimeUnmarshalJSON_NullHandled(t *testing.T) {
	var at apiTime
	if err := json.Unmarshal([]byte("null"), &at); err != nil {
		t.Errorf("null must unmarshal without error; got: %v", err)
	}
	if !at.Time.IsZero() {
		t.Errorf("null must produce zero time; got: %v", at.Time)
	}
}

// TestAPITimeUnmarshalJSON_SingleParseCall_SC26 is a structural invariant test
// for SC-26 / INV-API-TS-2: the source file must contain exactly one time.Parse
// call (the RFC3339 layout) and must not contain any space-separator format string.
// This catches any regression that re-adds a fallback layout list.
func TestAPITimeUnmarshalJSON_SingleParseCall_SC26(t *testing.T) {
	// Read the source file from the same directory as this test.
	// client_test.go is package api, alongside client.go.
	src, err := os.ReadFile("client.go")
	if err != nil {
		t.Fatalf("Failed to read client.go: %v", err)
	}
	content := string(src)

	// Exactly one time.Parse call
	parseCount := strings.Count(content, "time.Parse(")
	if parseCount != 1 {
		t.Errorf(
			"SC-26 violation: expected exactly 1 time.Parse call in client.go, found %d. "+
				"INV-API-TS-2 requires a single RFC3339 layout with no fallback list.",
			parseCount,
		)
	}

	// RFC3339 layout present
	if !strings.Contains(content, "time.RFC3339") {
		t.Errorf(
			"SC-26 violation: client.go must reference time.RFC3339 as the parser layout. " +
				"INV-API-TS-2 requires the RFC3339 contract to be explicit.",
		)
	}

	// No space-separator format strings (the pre-fix fallback layouts)
	if strings.Contains(content, "2006-01-02 15:04:05") {
		t.Errorf(
			"SC-26 violation: client.go must not contain any space-separator format string. " +
				"The pre-fix fallback list has been re-introduced.",
		)
	}
}

// ---------------------------------------------------------------------------
// Cluster 1 dedup (doRequest, docs/work/dedup-triage.md section 1): closes the
// triage's documented test gap. Every method here previously had no direct
// httptest-backed test. PauseSource/ResumeSource/PruneCount/PruneUnprioritized
// used to check only apiResp.Success, never resp.StatusCode — the exact drift
// SC-1 names in the work order's "why" ("four TUI client methods skip the
// HTTP status check the other nine make") and requires fixed: "PauseSource,
// ResumeSource, PruneCount and PruneUnprioritized return an error on a
// non-2xx status like the other nine" (work-order.json SC-1). The
// "StatusError" tests prove they error when the daemon reports failure
// (status and success both indicate it), and the
// "StatusErrorEvenWhenBodySaysSuccess" tests prove the fix itself: a non-2xx
// status now produces an error even when the body claims success:true.
// ---------------------------------------------------------------------------

func TestUpdateSourceRequest(t *testing.T) {
	var gotMethod, gotPath, gotContentType, gotAPIKey string
	var gotBody []byte
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotContentType = r.Header.Get("Content-Type")
		gotAPIKey = r.Header.Get("X-API-Key")
		gotBody, _ = io.ReadAll(r.Body)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"updated"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "secret-key", httpClient: &http.Client{Timeout: 5 * time.Second}}
	name := "New Name"
	resp, err := client.UpdateSource("src-1", SourceRequest{URL: "https://example.com", Type: "rss", Name: &name})
	if err != nil {
		t.Fatalf("UpdateSource failed: %v", err)
	}
	if !resp.Success {
		t.Fatal("expected a successful response")
	}
	if gotMethod != http.MethodPatch {
		t.Errorf("expected PATCH, got %s", gotMethod)
	}
	if gotPath != "/api/sources/src-1" {
		t.Errorf("expected /api/sources/src-1, got %s", gotPath)
	}
	if gotContentType != "application/json" {
		t.Errorf("expected application/json content-type, got %q", gotContentType)
	}
	if gotAPIKey != "secret-key" {
		t.Errorf("expected X-API-Key header to be sent, got %q", gotAPIKey)
	}
	var sent SourceRequest
	if err := json.Unmarshal(gotBody, &sent); err != nil {
		t.Fatalf("failed to decode sent body: %v", err)
	}
	if sent.URL != "https://example.com" {
		t.Errorf("expected sent URL to round-trip, got %q", sent.URL)
	}
}

func TestUpdateSourceStatusError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusNotFound)
		_, _ = w.Write([]byte(`{"success":false,"message":"no such source"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.UpdateSource("missing", SourceRequest{URL: "https://example.com", Type: "rss"})
	if err == nil {
		t.Fatal("expected an error for a 404 response")
	}
	if !containsString(err.Error(), "source not found") {
		t.Errorf("expected 'source not found', got: %v", err)
	}
}

func TestUpdateContentRequest(t *testing.T) {
	var gotMethod, gotPath, gotContentType string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		gotContentType = r.Header.Get("Content-Type")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"updated"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	read := true
	_, err := client.UpdateContent("content-1", ContentUpdateRequest{Read: &read})
	if err != nil {
		t.Fatalf("UpdateContent failed: %v", err)
	}
	if gotMethod != http.MethodPatch {
		t.Errorf("expected PATCH, got %s", gotMethod)
	}
	if gotPath != "/api/entries/content-1" {
		t.Errorf("expected /api/entries/content-1, got %s", gotPath)
	}
	if gotContentType != "application/json" {
		t.Errorf("expected application/json content-type, got %q", gotContentType)
	}
}

func TestUpdateContentStatusError(t *testing.T) {
	cases := []struct {
		name       string
		status     int
		wantSubstr string
	}{
		{"not found", http.StatusNotFound, "content not found"},
		{"validation", http.StatusUnprocessableEntity, "validation error"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(tc.status)
				_, _ = w.Write([]byte(`{"success":false,"message":"bad request"}`))
			}))
			defer server.Close()

			client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
			read := true
			_, err := client.UpdateContent("content-1", ContentUpdateRequest{Read: &read})
			if err == nil {
				t.Fatalf("expected an error for status %d", tc.status)
			}
			if !containsString(err.Error(), tc.wantSubstr) {
				t.Errorf("expected error to contain %q, got: %v", tc.wantSubstr, err)
			}
		})
	}
}

func TestFetchEntriesRequest(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.RequestURI()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"items":[],"total":0,"filters_applied":{}}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	items, err := client.FetchEntries()
	if err != nil {
		t.Fatalf("FetchEntries failed: %v", err)
	}
	if items == nil {
		t.Fatal("expected a non-nil slice")
	}
	if gotPath != "/api/entries?limit=10000" {
		t.Errorf("expected /api/entries?limit=10000, got %s", gotPath)
	}
}

func TestFetchEntriesSinceRequest(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.RequestURI()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"items":[],"total":0,"filters_applied":{}}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	since := time.Date(2026, 1, 2, 3, 4, 5, 0, time.UTC)
	if _, err := client.FetchEntriesSince(since); err != nil {
		t.Fatalf("FetchEntriesSince failed: %v", err)
	}
	wantParam := "since=" + since.Format(time.RFC3339Nano)
	if !strings.Contains(gotPath, wantParam) {
		t.Errorf("expected path to contain %q, got %s", wantParam, gotPath)
	}
}

func TestPauseSourceRequest(t *testing.T) {
	var gotMethod, gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"paused"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	resp, err := client.PauseSource("src-1")
	if err != nil {
		t.Fatalf("PauseSource failed: %v", err)
	}
	if !resp.Success {
		t.Fatal("expected a successful response")
	}
	if gotMethod != http.MethodPatch {
		t.Errorf("expected PATCH, got %s", gotMethod)
	}
	if gotPath != "/api/sources/src-1/pause" {
		t.Errorf("expected /api/sources/src-1/pause, got %s", gotPath)
	}
}

func TestPauseSourceStatusError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":false,"message":"pause failed"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PauseSource("src-1")
	if err == nil {
		t.Fatal("expected an error for a failed pause")
	}
	if !containsString(err.Error(), "pause failed") {
		t.Errorf("expected error to contain the API message, got: %v", err)
	}
}

// TestPauseSourceStatusErrorEvenWhenBodySaysSuccess proves the SC-1 fix
// (work-order.json's "why": "four TUI client methods skip the HTTP status
// check the other nine make"): PauseSource must now error on a non-2xx
// status even when the body claims success:true — it must not decide on
// apiResp.Success alone anymore, the way it used to.
func TestPauseSourceStatusErrorEvenWhenBodySaysSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":true,"message":"paused anyway"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PauseSource("src-1")
	if err == nil {
		t.Fatal("expected PauseSource to error on a non-2xx status regardless of apiResp.Success")
	}
}

func TestResumeSourceRequest(t *testing.T) {
	var gotMethod, gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.Path
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"resumed"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	resp, err := client.ResumeSource("src-1")
	if err != nil {
		t.Fatalf("ResumeSource failed: %v", err)
	}
	if !resp.Success {
		t.Fatal("expected a successful response")
	}
	if gotMethod != http.MethodPatch {
		t.Errorf("expected PATCH, got %s", gotMethod)
	}
	if gotPath != "/api/sources/src-1/resume" {
		t.Errorf("expected /api/sources/src-1/resume, got %s", gotPath)
	}
}

func TestResumeSourceStatusError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":false,"message":"resume failed"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.ResumeSource("src-1")
	if err == nil {
		t.Fatal("expected an error for a failed resume")
	}
	if !containsString(err.Error(), "resume failed") {
		t.Errorf("expected error to contain the API message, got: %v", err)
	}
}

// TestResumeSourceStatusErrorEvenWhenBodySaysSuccess is ResumeSource's half
// of the SC-1 fix — see TestPauseSourceStatusErrorEvenWhenBodySaysSuccess.
func TestResumeSourceStatusErrorEvenWhenBodySaysSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":true,"message":"resumed anyway"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.ResumeSource("src-1")
	if err == nil {
		t.Fatal("expected ResumeSource to error on a non-2xx status regardless of apiResp.Success")
	}
}

func TestPruneCountRequest(t *testing.T) {
	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.RequestURI()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"count":7,"days_filter":null}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	days := 30
	count, err := client.PruneCount(&days)
	if err != nil {
		t.Fatalf("PruneCount failed: %v", err)
	}
	if count != 7 {
		t.Errorf("expected count 7, got %d", count)
	}
	if gotPath != "/api/prune/count?days=30" {
		t.Errorf("expected /api/prune/count?days=30, got %s", gotPath)
	}
}

func TestPruneCountStatusError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":false,"message":"count failed"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PruneCount(nil)
	if err == nil {
		t.Fatal("expected an error for a failed prune count")
	}
	if !containsString(err.Error(), "count failed") {
		t.Errorf("expected error to contain the API message, got: %v", err)
	}
}

// TestPruneCountStatusErrorEvenWhenBodySaysSuccess is PruneCount's half of
// the SC-1 fix — see TestPauseSourceStatusErrorEvenWhenBodySaysSuccess.
func TestPruneCountStatusErrorEvenWhenBodySaysSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"count":3,"days_filter":null}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PruneCount(nil)
	if err == nil {
		t.Fatal("expected PruneCount to error on a non-2xx status regardless of apiResp.Success")
	}
}

func TestPruneUnprioritizedRequest(t *testing.T) {
	var gotMethod, gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotMethod = r.Method
		gotPath = r.URL.RequestURI()
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"deleted":5,"days_filter":null}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	days := 7
	deleted, err := client.PruneUnprioritized(&days)
	if err != nil {
		t.Fatalf("PruneUnprioritized failed: %v", err)
	}
	if deleted != 5 {
		t.Errorf("expected 5 deleted, got %d", deleted)
	}
	if gotMethod != http.MethodPost {
		t.Errorf("expected POST, got %s", gotMethod)
	}
	if gotPath != "/api/prune?days=7" {
		t.Errorf("expected /api/prune?days=7, got %s", gotPath)
	}
}

func TestPruneUnprioritizedStatusError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":false,"message":"prune failed"}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PruneUnprioritized(nil)
	if err == nil {
		t.Fatal("expected an error for a failed prune")
	}
	if !containsString(err.Error(), "prune failed") {
		t.Errorf("expected error to contain the API message, got: %v", err)
	}
}

// TestPruneUnprioritizedStatusErrorEvenWhenBodySaysSuccess is
// PruneUnprioritized's half of the SC-1 fix — see
// TestPauseSourceStatusErrorEvenWhenBodySaysSuccess.
func TestPruneUnprioritizedStatusErrorEvenWhenBodySaysSuccess(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"deleted":2,"days_filter":null}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Second}}
	_, err := client.PruneUnprioritized(nil)
	if err == nil {
		t.Fatal("expected PruneUnprioritized to error on a non-2xx status regardless of apiResp.Success")
	}
}

// TestGenerateAudioBriefingUsesOwnTimeout, TestExtractEntryUsesOwnTimeout and
// TestGetContextSuggestionsUsesOwnTimeout pin the triage's other named
// behavior risk for cluster 1: these three methods build a fresh, longer-
// timeout *http.Client per call instead of using c.httpClient — losing its
// tuned Transport (connection pooling) on purpose, not by accident. Each
// server sleeps longer than c.httpClient's own timeout; the call only
// succeeds if the method used its own longer-lived client for this request.
func TestGenerateAudioBriefingUsesOwnTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(50 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"file_path":"/tmp/a.mp3","filename":"a.mp3","duration_estimate":"5m","generated_at":"now","provider":"test","high_priority_count":1}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Millisecond}}
	resp, err := client.GenerateAudioBriefing()
	if err != nil {
		t.Fatalf("GenerateAudioBriefing failed: %v", err)
	}
	if resp.Filename != "a.mp3" {
		t.Errorf("expected filename a.mp3, got %q", resp.Filename)
	}
}

func TestExtractEntryUsesOwnTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(50 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"status":"extracted"}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Millisecond}}
	data, err := client.ExtractEntry("content-1")
	if err != nil {
		t.Fatalf("ExtractEntry failed: %v", err)
	}
	if data["status"] != "extracted" {
		t.Errorf("expected status extracted, got %v", data["status"])
	}
}

func TestGetContextSuggestionsUsesOwnTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(50 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"success":true,"message":"ok","data":{"suggested_topics":[{"topic":"AI","section":"high","action":"add","existing_topic":null,"gap_analysis":"gap","rationale":"why"}]}}`))
	}))
	defer server.Close()

	client := &APIClient{baseURL: server.URL, apiKey: "test", httpClient: &http.Client{Timeout: 5 * time.Millisecond}}
	resp, err := client.GetContextSuggestions()
	if err != nil {
		t.Fatalf("GetContextSuggestions failed: %v", err)
	}
	if len(resp.SuggestedTopics) != 1 || resp.SuggestedTopics[0].Topic != "AI" {
		t.Errorf("unexpected suggested topics: %+v", resp.SuggestedTopics)
	}
}
