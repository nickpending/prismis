package ui

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/nickpending/prismis/internal/db"
)

// An open TUI used to ask the daemon for items fetched since its last look, so a row
// that changed without a re-fetch (a vote from the web view, a backfilled timestamp)
// never reached it until a restart. The sync now asks for rows changed since the
// marker, advances the marker from each row's updated_at, and re-merges the changed
// row over the cached one.
func TestRemoteSync_AsksForChangedSinceAndAdvancesMarkerFromUpdatedAt(t *testing.T) {
	remoteTestConfig(t)
	fetched := "2026-01-02T03:04:05+00:00"
	updated := "2026-01-20T10:00:00+00:00"
	var request string
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/api/entries" {
			request = r.URL.RequestURI()
		}
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"success":true,"message":"ok","data":{"items":[{"id":"a","title":"Changed","url":"https://example.com/a","summary":"s","analysis":{"kind":"news"},"priority":"high","published_at":"2026-01-02T00:00:00+00:00","fetched_at":%q,"updated_at":%q,"user_feedback":"up","source_name":"S"}],"total":1}}`, fetched, updated)
	}))
	defer srv.Close()

	lastSync := time.Date(2026, 1, 10, 0, 0, 0, 0, time.UTC)
	m := Model{
		remoteURL:  srv.URL,
		lastSync:   lastSync,
		itemsCache: []db.ContentItem{{ID: "a", Title: "Stale", Priority: "high"}},
	}

	loaded := fetchItemsRemote(m)
	if loaded.err != nil {
		t.Fatalf("fetchItemsRemote: %v", loaded.err)
	}
	if !strings.Contains(request, "changed_since="+lastSync.Format(time.RFC3339Nano)) {
		t.Fatalf("sync request %q does not ask for changed_since the last marker", request)
	}
	want, _ := time.Parse(time.RFC3339, updated)
	if !loaded.newLastSync.Equal(want) {
		t.Errorf("newLastSync = %s, want the row's updated_at %s", loaded.newLastSync, want)
	}
	if len(loaded.allItems) != 1 || loaded.allItems[0].Title != "Changed" || loaded.allItems[0].UserFeedback != "up" {
		t.Errorf("changed row was not merged over the cached one: %+v", loaded.allItems)
	}
}

// A daemon older than this change answers without updated_at; the marker then falls
// back to fetched_at so the sync still advances instead of re-asking from zero.
func TestRemoteSync_MarkerFallsBackToFetchedAtWithoutUpdatedAt(t *testing.T) {
	remoteTestConfig(t)
	fetched := "2026-01-02T03:04:05+00:00"
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprintf(w, `{"success":true,"message":"ok","data":{"items":[{"id":"a","title":"Old daemon","priority":"high","published_at":"2026-01-02T00:00:00+00:00","fetched_at":%q,"source_name":"S"}],"total":1}}`, fetched)
	}))
	defer srv.Close()

	loaded := fetchItemsRemote(Model{remoteURL: srv.URL, lastSync: time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)})
	if loaded.err != nil {
		t.Fatalf("fetchItemsRemote: %v", loaded.err)
	}
	want, _ := time.Parse(time.RFC3339, fetched)
	if !loaded.newLastSync.Equal(want) {
		t.Errorf("newLastSync = %s, want fetched_at %s", loaded.newLastSync, want)
	}
}
