package operations

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

// setupRemoteConfig points api.NewClient() at serverURL by writing a
// config.toml with a [remote] section into a temp XDG_CONFIG_HOME, which
// api.NewClientWithURL's caller (api.NewClient, called with "") resolves via
// cfg.HasRemoteConfig()/cfg.GetRemoteURL(). t.Setenv restores the previous
// XDG_CONFIG_HOME automatically when the test ends.
func setupRemoteConfig(t *testing.T, serverURL string) {
	t.Helper()
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "prismis")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("failed to create config dir: %v", err)
	}
	configContent := fmt.Sprintf("[remote]\nurl = %q\nkey = \"test-key\"\n", serverURL)
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config: %v", err)
	}
	t.Setenv("XDG_CONFIG_HOME", tmpDir)
}

// setupNoConfig points XDG_CONFIG_HOME at an empty directory with no
// config.toml, so api.NewClient() fails with "API key not found in config" -
// the failure newClientOrErrMsg wraps.
func setupNoConfig(t *testing.T) {
	t.Helper()
	t.Setenv("XDG_CONFIG_HOME", t.TempDir())
}

const testSourceID = "11111111-1111-1111-1111-111111111111"

func TestNewClientOrErrMsg_Failure(t *testing.T) {
	setupNoConfig(t)

	client, errMsg := newClientOrErrMsg()
	if client != nil {
		t.Fatalf("expected nil client on config failure, got %+v", client)
	}
	if errMsg == nil {
		t.Fatal("expected a non-nil errMsg when config loading fails")
	}
	msg, ok := errMsg.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", errMsg)
	}
	if msg.Success {
		t.Error("expected Success=false on client-create failure")
	}
	if msg.Error == nil {
		t.Error("expected a wrapped Error on client-create failure")
	}
	wantPrefix := "Failed to create API client: "
	if len(msg.Message) < len(wantPrefix) || msg.Message[:len(wantPrefix)] != wantPrefix {
		t.Errorf("expected message to start with %q, got %q", wantPrefix, msg.Message)
	}
}

func TestNewClientOrErrMsg_Success(t *testing.T) {
	setupRemoteConfig(t, "http://127.0.0.1:1")

	client, errMsg := newClientOrErrMsg()
	if errMsg != nil {
		t.Fatalf("expected nil errMsg on success, got %+v", errMsg)
	}
	if client == nil {
		t.Fatal("expected a non-nil client on success")
	}
}

func TestLookupOrErrMsg_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources" || r.Method != http.MethodGet {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"sources":[{"id":"src-1","url":"https://example.com/feed","type":"rss","name":"My Feed","active":true,"error_count":0}],"total":1}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	apiClient, errMsg := newClientOrErrMsg()
	if errMsg != nil {
		t.Fatalf("unexpected client-create failure: %+v", errMsg)
	}

	id, name, errMsg := lookupOrErrMsg("My Feed", apiClient)
	if errMsg != nil {
		t.Fatalf("expected a successful lookup, got errMsg: %+v", errMsg)
	}
	if id != "src-1" {
		t.Errorf("expected id 'src-1', got %q", id)
	}
	if name != "My Feed" {
		t.Errorf("expected name 'My Feed', got %q", name)
	}
}

func TestLookupOrErrMsg_NotFound(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"sources":[],"total":0}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	apiClient, errMsg := newClientOrErrMsg()
	if errMsg != nil {
		t.Fatalf("unexpected client-create failure: %+v", errMsg)
	}

	_, _, errMsg = lookupOrErrMsg("nonexistent-feed", apiClient)
	if errMsg == nil {
		t.Fatal("expected a not-found errMsg")
	}
	msg, ok := errMsg.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", errMsg)
	}
	if msg.Success {
		t.Error("expected Success=false for a lookup miss")
	}
	wantMsg := "source not found: nonexistent-feed"
	if msg.Message != wantMsg {
		t.Errorf("expected message %q, got %q", wantMsg, msg.Message)
	}
}

func TestAddSource_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources" || r.Method != http.MethodPost {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		if r.Header.Get("X-API-Key") != "test-key" {
			t.Errorf("expected X-API-Key header, got %q", r.Header.Get("X-API-Key"))
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"added","data":{"name":"Example Feed"}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := AddSource("https://example.com/feed.xml", "")()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "✓ Added rss source: Example Feed"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestAddSource_ValidationError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnprocessableEntity)
		fmt.Fprint(w, `{"success":false,"message":"URL is not a valid feed"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := AddSource("https://example.com/not-a-feed", "")()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false on validation error")
	}
	if msg.Message != "URL is not a valid feed" {
		t.Errorf("expected the daemon's validation message surfaced, got %q", msg.Message)
	}
}

func TestRemoveSource_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources/"+testSourceID || r.Method != http.MethodDelete {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"deleted"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := RemoveSource(testSourceID)()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "✓ Removed source: source"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestRemoveSource_LookupFailure(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"sources":[],"total":0}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := RemoveSource("does-not-exist")()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false when the lookup misses")
	}
	if msg.Message != "source not found: does-not-exist" {
		t.Errorf("unexpected message: %q", msg.Message)
	}
}

func TestPauseSource_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources/"+testSourceID+"/pause" || r.Method != http.MethodPatch {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"paused"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := PauseSource(testSourceID)()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "⏸ Paused source: source"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestResumeSource_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources/"+testSourceID+"/resume" || r.Method != http.MethodPatch {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"resumed"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := ResumeSource(testSourceID)()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "▶ Resumed source: source"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestUpdateSource_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/sources/"+testSourceID || r.Method != http.MethodPatch {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"updated","data":{"name":"Renamed Feed"}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := UpdateSource(testSourceID, map[string]interface{}{
		"url":  "https://example.com/feed.xml",
		"type": "rss",
		"name": "Renamed Feed",
	})()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "✓ Updated source: Renamed Feed"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestUpdateSource_MissingURL(t *testing.T) {
	setupRemoteConfig(t, "http://127.0.0.1:1")

	result := UpdateSource(testSourceID, map[string]interface{}{"type": "rss"})()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false when URL is missing")
	}
	if msg.Message != "URL is required for update" {
		t.Errorf("unexpected message: %q", msg.Message)
	}
}

func TestEditSourceName_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.URL.Path == "/api/sources" && r.Method == http.MethodGet:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprintf(w, `{"success":true,"message":"ok","data":{"sources":[{"id":%q,"url":"https://example.com/feed","type":"rss","active":true,"error_count":0}],"total":1}}`, testSourceID)
		case r.URL.Path == "/api/sources/"+testSourceID && r.Method == http.MethodPatch:
			w.Header().Set("Content-Type", "application/json")
			fmt.Fprint(w, `{"success":true,"message":"updated","data":{"name":"New Name"}}`)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := EditSourceName(testSourceID, "New Name")()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if !msg.Success {
		t.Errorf("expected Success=true, got message %q, err %v", msg.Message, msg.Error)
	}
	want := "✓ Updated source: New Name"
	if msg.Message != want {
		t.Errorf("expected message %q, got %q", want, msg.Message)
	}
}

func TestExportSources_NoSources(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"sources":[],"total":0}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := ExportSources()()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false with no sources")
	}
	if msg.Message != "No sources configured to export" {
		t.Errorf("unexpected message: %q", msg.Message)
	}
}

func TestExportSources_APIError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusForbidden)
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":false,"message":"forbidden"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := ExportSources()()
	msg, ok := result.(SourceOperationMsg)
	if !ok {
		t.Fatalf("expected SourceOperationMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false on a 403 from GetSources")
	}
	if msg.Message != "Authentication failed - check API key" {
		t.Errorf("unexpected message: %q", msg.Message)
	}
}
