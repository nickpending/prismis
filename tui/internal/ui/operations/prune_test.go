package operations

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/nickpending/prismis/internal/commands"
)

func TestNewClientOrPruneErrMsg_Failure(t *testing.T) {
	setupNoConfig(t)

	client, errMsg := newClientOrPruneErrMsg()
	if client != nil {
		t.Fatalf("expected nil client on config failure, got %+v", client)
	}
	if errMsg == nil {
		t.Fatal("expected a non-nil errMsg when config loading fails")
	}
	msg, ok := errMsg.(PruneResultMsg)
	if !ok {
		t.Fatalf("expected PruneResultMsg, got %T", errMsg)
	}
	if msg.Error == nil {
		t.Error("expected a wrapped Error on client-create failure")
	}
}

func TestGetPruneCount_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/prune/count" || r.Method != http.MethodGet {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"count":7,"days_filter":null}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := GetPruneCount(nil)()
	msg, ok := result.(PruneCountMsg)
	if !ok {
		t.Fatalf("expected PruneCountMsg, got %T", result)
	}
	if msg.Count != 7 {
		t.Errorf("expected Count=7, got %d", msg.Count)
	}
	if msg.ShowOnly {
		t.Error("GetPruneCount must not set ShowOnly itself")
	}
}

func TestGetPruneCount_Error(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":false,"message":"count failed"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := GetPruneCount(nil)()
	msg, ok := result.(PruneResultMsg)
	if !ok {
		t.Fatalf("expected PruneResultMsg, got %T", result)
	}
	if msg.Error == nil {
		t.Fatal("expected a non-nil Error")
	}
}

func TestExecutePrune_Success(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/prune/count" && r.Method == http.MethodGet:
			fmt.Fprint(w, `{"success":true,"message":"ok","data":{"count":5,"days_filter":null}}`)
		case r.URL.Path == "/api/prune" && r.Method == http.MethodPost:
			fmt.Fprint(w, `{"success":true,"message":"ok","data":{"deleted":5,"days_filter":null}}`)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := ExecutePrune(nil)()
	msg, ok := result.(PruneResultMsg)
	if !ok {
		t.Fatalf("expected PruneResultMsg, got %T", result)
	}
	if msg.Error != nil {
		t.Fatalf("unexpected error: %v", msg.Error)
	}
	if msg.Count != 5 || msg.Deleted != 5 {
		t.Errorf("expected Count=5 Deleted=5, got Count=%d Deleted=%d", msg.Count, msg.Deleted)
	}
}

// TestHandlePruneCommand_CountOnly_Delegates proves the CountOnly branch
// delegates to GetPruneCount rather than re-deriving the count itself (the
// cluster-9 consolidation), by asserting on the one field GetPruneCount never
// sets on its own: ShowOnly.
func TestHandlePruneCommand_CountOnly_Delegates(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/prune/count" || r.Method != http.MethodGet {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"count":3,"days_filter":null}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := HandlePruneCommand(commands.PruneMsg{CountOnly: true})()
	msg, ok := result.(PruneCountMsg)
	if !ok {
		t.Fatalf("expected PruneCountMsg, got %T", result)
	}
	if msg.Count != 3 {
		t.Errorf("expected Count=3, got %d", msg.Count)
	}
	if !msg.ShowOnly {
		t.Error("expected ShowOnly=true for a CountOnly request")
	}
}

// TestHandlePruneCommand_CountOnly_ErrorPassthrough proves the delegation
// passes a PruneResultMsg error through unchanged rather than dropping it.
func TestHandlePruneCommand_CountOnly_ErrorPassthrough(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":false,"message":"count failed"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := HandlePruneCommand(commands.PruneMsg{CountOnly: true})()
	msg, ok := result.(PruneResultMsg)
	if !ok {
		t.Fatalf("expected PruneResultMsg (error passthrough), got %T", result)
	}
	if msg.Error == nil {
		t.Fatal("expected a non-nil Error")
	}
}

func TestHandlePruneCommand_Force_ExecutesImmediately(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch {
		case r.URL.Path == "/api/prune/count" && r.Method == http.MethodGet:
			fmt.Fprint(w, `{"success":true,"message":"ok","data":{"count":2,"days_filter":null}}`)
		case r.URL.Path == "/api/prune" && r.Method == http.MethodPost:
			fmt.Fprint(w, `{"success":true,"message":"ok","data":{"deleted":2,"days_filter":null}}`)
		default:
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := HandlePruneCommand(commands.PruneMsg{Force: true})()
	msg, ok := result.(PruneResultMsg)
	if !ok {
		t.Fatalf("expected PruneResultMsg, got %T", result)
	}
	if msg.Deleted != 2 {
		t.Errorf("expected Deleted=2, got %d", msg.Deleted)
	}
}

func TestHandlePruneCommand_Confirm_GetsCountFirst(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/prune/count" || r.Method != http.MethodGet {
			t.Fatalf("unexpected request: %s %s (expected only a count call before confirmation)", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"count":9,"days_filter":null}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := HandlePruneCommand(commands.PruneMsg{})()
	msg, ok := result.(PruneCountMsg)
	if !ok {
		t.Fatalf("expected PruneCountMsg, got %T", result)
	}
	if msg.Count != 9 {
		t.Errorf("expected Count=9, got %d", msg.Count)
	}
	if msg.ShowOnly {
		t.Error("expected ShowOnly=false when confirmation is required")
	}
}
