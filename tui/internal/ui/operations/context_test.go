package operations

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/nickpending/prismis/internal/api"
)

func TestFormatTopicSection(t *testing.T) {
	existing := "Existing Topic"
	topics := []api.TopicSuggestion{
		{
			Topic:         "New Topic",
			Action:        "add",
			ExistingTopic: nil,
			GapAnalysis:   "no coverage today",
			Rationale:     "seen often in flagged items",
		},
		{
			Topic:         "Expand Topic",
			Action:        "expand",
			ExistingTopic: &existing,
			GapAnalysis:   "too narrow",
			Rationale:     "related items keep getting missed",
		},
	}

	var b strings.Builder
	formatTopicSection(&b, "## High Priority Topics\n\n", topics)

	want := "## High Priority Topics\n\n" +
		"- New Topic\n" +
		"  **Action:** add\n" +
		"  **Gap:** no coverage today\n" +
		"  *seen often in flagged items*\n\n" +
		"- Expand Topic\n" +
		"  **Action:** expand | **Existing:** Existing Topic\n" +
		"  **Gap:** too narrow\n" +
		"  *related items keep getting missed*\n\n"

	if got := b.String(); got != want {
		t.Errorf("formatTopicSection output mismatch\n got: %q\nwant: %q", got, want)
	}
}

func TestFormatTopicSection_Empty(t *testing.T) {
	var b strings.Builder
	formatTopicSection(&b, "## Low Priority Topics\n\n", nil)

	want := "## Low Priority Topics\n\n"
	if got := b.String(); got != want {
		t.Errorf("expected just the heading for no topics, got %q", got)
	}
}

func TestGetContextSuggestions_ValidationError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/api/context" || r.Method != http.MethodPost {
			t.Fatalf("unexpected request: %s %s", r.Method, r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusUnprocessableEntity)
		fmt.Fprint(w, `{"success":false,"message":"no flagged items"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := GetContextSuggestions()()
	msg, ok := result.(ContextSuggestionsMsg)
	if !ok {
		t.Fatalf("expected ContextSuggestionsMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false on a 422 validation error")
	}
	if msg.Error == nil || !strings.Contains(msg.Error.Error(), "no flagged items") {
		t.Errorf("expected the daemon's validation message in the error, got %v", msg.Error)
	}
}

func TestGetContextSuggestions_ServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusInternalServerError)
		fmt.Fprint(w, `{"success":false,"message":"llm call failed"}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := GetContextSuggestions()()
	msg, ok := result.(ContextSuggestionsMsg)
	if !ok {
		t.Fatalf("expected ContextSuggestionsMsg, got %T", result)
	}
	if msg.Success {
		t.Error("expected Success=false on a 500 server error")
	}
	if msg.Error == nil || !strings.Contains(msg.Error.Error(), "llm call failed") {
		t.Errorf("expected the daemon's server-error message in the error, got %v", msg.Error)
	}
}

func TestGetContextSuggestions_GroupsTopicsIntoSections(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{"suggested_topics":[
			{"topic":"High One","section":"high","action":"add","existing_topic":null,"gap_analysis":"g1","rationale":"r1"},
			{"topic":"Medium One","section":"medium","action":"expand","existing_topic":null,"gap_analysis":"g2","rationale":"r2"},
			{"topic":"Low One","section":"low","action":"narrow","existing_topic":null,"gap_analysis":"g3","rationale":"r3"}
		]}}`)
	}))
	defer server.Close()
	setupRemoteConfig(t, server.URL)

	result := GetContextSuggestions()()
	msg, ok := result.(ContextSuggestionsMsg)
	if !ok {
		t.Fatalf("expected ContextSuggestionsMsg, got %T", result)
	}
	if msg.Count != 3 {
		t.Errorf("expected Count=3, got %d", msg.Count)
	}
	// The clipboard write may fail in a headless test environment (no
	// pbcopy/xclip/xsel); Success tracks that, so assert on the formatted
	// text itself rather than the clipboard-dependent Success flag.
	for _, want := range []string{
		"## High Priority Topics",
		"- High One",
		"## Medium Priority Topics",
		"- Medium One",
		"## Low Priority Topics",
		"- Low One",
	} {
		if !strings.Contains(msg.Suggestions, want) {
			t.Errorf("expected formatted suggestions to contain %q, got:\n%s", want, msg.Suggestions)
		}
	}
}
