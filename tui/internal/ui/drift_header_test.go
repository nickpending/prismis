package ui

import (
	"database/sql"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis/internal/db"
)

const (
	flaggedWarning = "⚠ Drift: 62% of the last 24 hours' items are unprioritized, usual is 54%"
	thinWarning    = "⚠ Drift: too few items to measure, usual is ?"
)

var ansiRE = regexp.MustCompile(`\x1b\[[0-9;]*m`)

// listModel is a list-view Model wide enough that the flagged header does not truncate.
// showArchived is set so the header's archived count never reads the developer's real
// database.
func listModel(remoteURL string) Model {
	return Model{
		items:           []db.ContentItem{},
		view:            "list",
		viewport:        viewport.New(80, 20),
		theme:           CleanCyberTheme,
		sourceModal:     NewSourceModal(),
		helpModal:       NewHelpModal(),
		commandMode:     NewCommandMode(),
		focusedPane:     "content",
		priority:        "low",
		filterType:      "all",
		kindFilter:      "all",
		showArchived:    true,
		flashItem:       -1,
		width:           200,
		height:          30,
		remoteURL:       remoteURL,
		sourcesViewport: viewport.New(20, 10),
	}
}

// headerOf renders the list view and returns its first line, ANSI-stripped, plus the full
// view. The clock segment is the only part that moves between renders.
func headerOf(m Model) (string, string) {
	view := RenderList(m)
	first, _, _ := strings.Cut(view, "\n")
	return ansiRE.ReplaceAllString(first, ""), view
}

var clockRE = regexp.MustCompile(`\d\d:\d\d `)

// withoutClock drops the HH:MM so two renders a minute apart still compare.
func withoutClock(header string) string {
	return clockRE.ReplaceAllString(header, "")
}

func flagged(share, baseline *float64) *db.DriftCheck {
	return &db.DriftCheck{Flagged: true, Share: share, Baseline: baseline}
}

// driftServer answers /api/entries with an empty feed and /api/drift with the given
// status and body.
func driftServer(t *testing.T, driftStatus int, driftBody string) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/drift" {
			w.WriteHeader(driftStatus)
			fmt.Fprint(w, driftBody)
			return
		}
		fmt.Fprint(w, `{"success":true,"message":"","data":{"items":[],"total":0}}`)
	}))
	t.Cleanup(server.Close)
	return server
}

const (
	flaggedBody   = `{"success":true,"message":"ok","data":{"checked_at":"x","share":0.62,"baseline":0.54,"flagged":true,"reason":null}}`
	unflaggedBody = `{"success":true,"message":"ok","data":{"checked_at":"x","share":0.5,"baseline":0.54,"flagged":false,"reason":null}}`
	thinBody      = `{"success":true,"message":"ok","data":{"checked_at":"x","share":null,"baseline":null,"flagged":true,"reason":"thin"}}`
	nullBody      = `{"success":true,"message":"none","data":null}`
)

// loaded runs one refresh (the load path under test) and applies its result to m.
func loaded(t *testing.T, m Model, load func(Model) any) Model {
	t.Helper()
	msg := load(m)
	got, ok := msg.(itemsLoadedMsg)
	if !ok {
		t.Fatalf("expected itemsLoadedMsg, got %T", msg)
	}
	if got.err != nil {
		t.Fatalf("a drift read must never fail the item load: %v", got.err)
	}
	next, _ := m.update(got)
	nm, ok := next.(Model)
	if !ok {
		t.Fatalf("update returned %T", next)
	}
	return nm
}

func refreshLoad(m Model) any { return m.buildRefreshCmd(false)() }
func initialLoad(m Model) any { return fetchItemsWithState(m, true)() }

func TestRemoteHeaderCarriesTheDriftWarning(t *testing.T) {
	remoteTestConfig(t)

	for _, load := range []struct {
		name string
		fn   func(Model) any
	}{{"buildRefreshCmd", refreshLoad}, {"initial load", initialLoad}} {
		t.Run(load.name, func(t *testing.T) {
			server := driftServer(t, 200, flaggedBody)
			m := loaded(t, listModel(server.URL), load.fn)
			header, view := headerOf(m)
			if !strings.Contains(header, flaggedWarning) {
				t.Fatalf("header %q lacks the warning", header)
			}
			if strings.Index(header, flaggedWarning) > strings.Index(header, "◆") {
				t.Errorf("warning must sit left of the view-state and clock segment: %q", header)
			}
			plainRows := strings.Count(RenderList(listModel(server.URL)), "\n")
			if rows := strings.Count(view, "\n"); rows != plainRows {
				t.Errorf("a flagged header must not change the row count: %d vs %d", rows, plainRows)
			}
		})
	}
}

func TestRemoteThinWindowWording(t *testing.T) {
	remoteTestConfig(t)
	server := driftServer(t, 200, thinBody)
	m := loaded(t, listModel(server.URL), refreshLoad)
	header, _ := headerOf(m)
	if !strings.Contains(header, thinWarning) {
		t.Fatalf("header %q lacks the thin-window warning", header)
	}
}

func TestRemoteHeaderUnchangedWhenNotFlaggedNullOrFailing(t *testing.T) {
	remoteTestConfig(t)
	want, _ := headerOf(listModel("http://unused"))
	for name, tc := range map[string]struct {
		status int
		body   string
	}{
		"unflagged": {200, unflaggedBody},
		"null data": {200, nullBody},
		"500":       {500, `{"detail":"boom"}`},
		"403":       {403, `{"detail":"no"}`},
	} {
		t.Run(name, func(t *testing.T) {
			server := driftServer(t, tc.status, tc.body)
			m := loaded(t, listModel(server.URL), refreshLoad)
			header, _ := headerOf(m)
			if withoutClock(header) != withoutClock(want) {
				t.Fatalf("header changed:\n got %q\nwant %q", header, want)
			}
		})
	}
}

// A check that stops flagging clears on the next refresh, and a refresh that reads
// nothing about drift (a re-filter of cached data) does not clear a held warning.
func TestWarningClearsOnTheRefreshAfterTheCheckStopsFlagging(t *testing.T) {
	remoteTestConfig(t)
	want, _ := headerOf(listModel("http://unused"))

	body := flaggedBody
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/drift" {
			fmt.Fprint(w, body)
			return
		}
		fmt.Fprint(w, `{"success":true,"message":"","data":{"items":[],"total":0}}`)
	}))
	t.Cleanup(server.Close)

	m := loaded(t, listModel(server.URL), refreshLoad)
	if header, _ := headerOf(m); !strings.Contains(header, flaggedWarning) {
		t.Fatalf("setup: warning missing: %q", header)
	}

	// A cache re-filter carries no drift read and must leave the held check alone.
	refiltered := fetchItemsWithState(m, false)()
	next, _ := m.update(refiltered)
	if header, _ := headerOf(next.(Model)); !strings.Contains(header, flaggedWarning) {
		t.Errorf("a re-filter must not clear the warning: %q", header)
	}

	body = unflaggedBody
	m = loaded(t, m, refreshLoad)
	header, _ := headerOf(m)
	if strings.Contains(header, "Drift") {
		t.Fatalf("warning must clear on the first refresh after an unflagged check: %q", header)
	}
	if withoutClock(header) != withoutClock(want) {
		t.Errorf("cleared header must equal today's:\n got %q\nwant %q", header, want)
	}
}

// localDB points the db package at a real SQLite file holding the tables the local
// refresh reads, with drift_checks taken from the daemon's schema.sql.
func localDB(t *testing.T) *sql.DB {
	t.Helper()
	dataHome := t.TempDir()
	t.Setenv("XDG_DATA_HOME", dataHome)
	if err := os.MkdirAll(filepath.Join(dataHome, "prismis"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := db.CloseDB(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = db.CloseDB() })

	schema, err := os.ReadFile(filepath.Join("..", "..", "..", "daemon", "src", "prismis_daemon", "schema.sql"))
	if err != nil {
		t.Fatalf("read daemon schema: %v", err)
	}
	text := string(schema)
	start := strings.Index(text, "CREATE TABLE IF NOT EXISTS drift_checks")
	end := strings.Index(text[start:], ");")
	if start < 0 || end < 0 {
		t.Fatal("daemon schema.sql has no complete drift_checks statement")
	}

	raw, err := sql.Open("sqlite3", filepath.Join(dataHome, "prismis", "prismis.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { raw.Close() })
	for _, stmt := range []string{
		`CREATE TABLE sources (id TEXT PRIMARY KEY, name TEXT, type TEXT, url TEXT NOT NULL, active BOOLEAN DEFAULT 1)`,
		`CREATE TABLE content (id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(id), title TEXT NOT NULL,
			url TEXT NOT NULL, content TEXT, summary TEXT, analysis TEXT, priority TEXT, published_at TIMESTAMP,
			read BOOLEAN DEFAULT 0, favorited BOOLEAN DEFAULT 0, interesting_override BOOLEAN DEFAULT 0,
			user_feedback TEXT, archived_at TIMESTAMP)`,
		text[start : start+end+2],
	} {
		if _, err := raw.Exec(stmt); err != nil {
			t.Fatalf("create schema: %v", err)
		}
	}
	return raw
}

func storeDrift(t *testing.T, raw *sql.DB, flaggedRow int, share, baseline any) {
	t.Helper()
	if _, err := raw.Exec(`INSERT INTO drift_checks
		(checked_at, window_items, window_null, share, baseline, baseline_windows, deviation, threshold, flagged, reason)
		VALUES ('2026-10-16T00:00:00+00:00', 40, 25, ?, ?, 7, NULL, 0.2, ?, NULL)`,
		share, baseline, flaggedRow); err != nil {
		t.Fatalf("insert drift check: %v", err)
	}
}

func TestLocalHeaderCarriesAndClearsTheDriftWarning(t *testing.T) {
	raw := localDB(t)
	want, _ := headerOf(listModel(""))

	// Empty table: today's header.
	m := loaded(t, listModel(""), refreshLoad)
	if header, _ := headerOf(m); withoutClock(header) != withoutClock(want) {
		t.Fatalf("empty drift_checks must render today's header:\n got %q\nwant %q", header, want)
	}

	storeDrift(t, raw, 0, 0.5, 0.54)
	storeDrift(t, raw, 1, 0.62, 0.54)
	for name, load := range map[string]func(Model) any{"refresh": refreshLoad, "initial": initialLoad} {
		m = loaded(t, listModel(""), load)
		if header, _ := headerOf(m); !strings.Contains(header, flaggedWarning) {
			t.Fatalf("%s: header %q lacks the warning", name, header)
		}
	}

	storeDrift(t, raw, 0, 0.5, 0.54)
	m = loaded(t, m, refreshLoad)
	header, _ := headerOf(m)
	if strings.Contains(header, "Drift") || withoutClock(header) != withoutClock(want) {
		t.Fatalf("cleared header must equal today's:\n got %q\nwant %q", header, want)
	}
}

func TestDriftWarningTextWording(t *testing.T) {
	for name, tc := range map[string]struct {
		check *db.DriftCheck
		want  string
	}{
		"nil":           {nil, ""},
		"unflagged":     {&db.DriftCheck{Share: new(0.62), Baseline: new(0.54)}, ""},
		"flagged":       {flagged(new(0.62), new(0.54)), flaggedWarning},
		"thin":          {flagged(nil, nil), thinWarning},
		"missing base":  {flagged(new(0.62), nil), "⚠ Drift: 62% of the last 24 hours' items are unprioritized, usual is ?"},
		"missing share": {flagged(nil, new(0.54)), "⚠ Drift: too few items to measure, usual is 54%"},
	} {
		t.Run(name, func(t *testing.T) {
			if got := driftWarningText(tc.check); got != tc.want {
				t.Errorf("got %q want %q", got, tc.want)
			}
		})
	}
}

func TestNarrowTerminalKeepsTheWarningAndFiveRows(t *testing.T) {
	m := listModel("")
	m.width = 90
	m.drift = flagged(new(0.62), new(0.54))
	header, view := headerOf(m)
	if !strings.Contains(header, flaggedWarning) {
		t.Fatalf("a narrow header must keep the warning: %q", header)
	}
	plain := listModel("")
	plain.width = 90
	_, plainView := headerOf(plain)
	if strings.Count(view, "\n") != strings.Count(plainView, "\n") {
		t.Errorf("a flagged header must not add rows")
	}
}
