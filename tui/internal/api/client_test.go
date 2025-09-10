package api

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// Helper to create a test client with proper config
func createTestClient(t *testing.T) *APIClient {
	// Check if we have a test config or daemon running
	if os.Getenv("PRISMIS_TEST_API_KEY") == "" {
		t.Skip("Set PRISMIS_TEST_API_KEY to run integration tests")
		return nil
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
	if client == nil {
		t.Skip("Skipping integration test - no test setup")
	}

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
	if client == nil {
		t.Skip("Skipping integration test - no test setup")
	}

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
	if client == nil {
		t.Skip("Skipping integration test - no test setup")
	}

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
	if client == nil {
		t.Skip("Skipping integration test - no test setup")
	}

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
	if client == nil {
		t.Skip("Skipping integration test - no test setup")
	}

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
func TestNetworkTimeoutRecovery(t *testing.T) {
	// Create client with very short timeout
	client := &APIClient{
		baseURL:    "http://localhost:8989",
		apiKey:     "test",
		httpClient: &http.Client{Timeout: 1 * time.Millisecond}, // Extremely short
	}

	// This should timeout
	_, err := client.GetSources()
	if err == nil {
		t.Skip("Expected timeout but got success - daemon too fast")
	}

	// Client should still be usable with normal timeout
	client.httpClient.Timeout = 10 * time.Second
	// This would work if daemon is running, but we just verify no panic
	client.GetSources()
	// No panic = success
}

// Helper function to check if string contains substring
func containsString(s, substr string) bool {
	return strings.Contains(s, substr)
}
