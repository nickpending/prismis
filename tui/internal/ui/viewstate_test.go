package ui

import (
	"os"
	"path/filepath"
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/db"
)

// TestMain points XDG_STATE_HOME at a throwaway directory for the whole package so no
// test that builds a model through newModel reads or writes the operator's real view
// file.
func TestMain(m *testing.M) {
	dir, err := os.MkdirTemp("", "prismis-ui-state-")
	if err != nil {
		panic(err)
	}
	os.Setenv("XDG_STATE_HOME", dir)
	code := m.Run()
	os.RemoveAll(dir)
	os.Exit(code)
}

// pressKey sends one rune key to a model through Update, the seam the operator uses.
func pressKey(t *testing.T, m Model, key rune) Model {
	t.Helper()
	updated, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{key}})
	return updated.(Model)
}

// stateHome isolates XDG_STATE_HOME for one test and returns the view file path.
func stateHome(t *testing.T) string {
	t.Helper()
	dir := t.TempDir()
	t.Setenv("XDG_STATE_HOME", dir)
	return filepath.Join(dir, "prismis", "tui-view.json")
}

// TestPriorityKeys_FloorFilter verifies each priority key, entered through Update and
// filtered by applyFiltersClientSide, shows exactly the items at or above its floor
// (and for 0 only the unprioritized ones). Every key is pressed after 0 so a leftover
// unprioritized flag from the previous view cannot leak none-priority items in.
func TestPriorityKeys_FloorFilter(t *testing.T) {
	items := []db.ContentItem{
		{ID: "high", Priority: "high"},
		{ID: "medium", Priority: "medium"},
		{ID: "low", Priority: "low"},
		{ID: "none", Priority: ""},
	}
	tests := []struct {
		key  rune
		want []string
	}{
		{'1', []string{"high"}},
		{'2', []string{"high", "medium"}},
		{'3', []string{"high", "medium", "low"}},
		{'a', []string{"high", "medium", "low"}},
		{'0', []string{"none"}},
	}
	for _, tt := range tests {
		t.Run(string(tt.key), func(t *testing.T) {
			m := Model{view: "list", focusedPane: "content", showAll: true, sortNewest: true, filterType: "all", kindFilter: "all"}
			m = pressKey(t, m, '0')
			m = pressKey(t, m, tt.key)

			got := map[string]bool{}
			for _, it := range applyFiltersClientSide(items, m) {
				got[it.ID] = true
			}
			want := map[string]bool{}
			for _, id := range tt.want {
				want[id] = true
			}
			for _, it := range items {
				if got[it.ID] != want[it.ID] {
					t.Errorf("key %q: item %q visible=%v, want %v", tt.key, it.ID, got[it.ID], want[it.ID])
				}
			}
		})
	}
}

// TestPriorityHeader verifies the header names each floor and the unprioritized view
// by the key that selects it.
func TestPriorityHeader(t *testing.T) {
	tests := []struct {
		key  rune
		want string
	}{
		{'1', "Priority: HIGH"},
		{'2', "Priority: MEDIUM+"},
		{'3', "Priority: LOW+"},
		{'a', "Priority: LOW+"},
		{'0', "Priority: UNPRIORITIZED"},
	}
	for _, tt := range tests {
		t.Run(string(tt.key), func(t *testing.T) {
			m := Model{view: "list", focusedPane: "content"}
			m = pressKey(t, m, tt.key)
			header := buildViewStateString(m)
			if !strings.HasPrefix(header, tt.want+" ") && !strings.HasPrefix(header, tt.want+"|") {
				t.Errorf("key %q: header %q does not start with %q", tt.key, header, tt.want)
			}
		})
	}
}

// changedView is a model whose seven view fields all differ from the defaults.
func changedView() Model {
	return Model{
		view:            "list",
		focusedPane:     "content",
		priority:        "high",
		showAll:         true,
		showArchived:    true,
		showInteresting: true,
		sortNewest:      false,
		filterType:      "rss",
		kindFilter:      "news",
		availableKinds:  []string{"news"},
	}
}

// TestViewState_PersistsAcrossLaunch changes each view field through a key Update and
// verifies a model built afterwards from the same XDG_STATE_HOME starts with it, and
// that R saves the defaults.
func TestViewState_PersistsAcrossLaunch(t *testing.T) {
	path := stateHome(t)

	m := newModel("")
	m.loading = false
	m.availableKinds = []string{"news", "release"}
	for _, key := range []rune{'1', 'u', 'v', 'i', 'd', 's', 'K'} {
		m = pressKey(t, m, key)
	}
	want := m.viewState()
	if want == defaultViewState() {
		t.Fatalf("keys left the view at its defaults: %+v", want)
	}
	if want.Priority != "high" || !want.ShowAll || !want.Archived || !want.Upvoted || want.SortNewest || want.SourceType != "rss" || want.Kind != "news" {
		t.Fatalf("keys did not change all seven fields: %+v", want)
	}
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("tui-view.json was not written: %v", err)
	}

	relaunched := newModel("")
	if relaunched.viewState() != want {
		t.Errorf("relaunch view = %+v, want %+v", relaunched.viewState(), want)
	}
	if relaunched.showUnprioritized {
		t.Error("relaunch into a floor view left showUnprioritized set")
	}

	relaunched.loading = false
	reset := pressKey(t, relaunched, 'R')
	if reset.viewState() != defaultViewState() {
		t.Fatalf("R left view = %+v, want defaults", reset.viewState())
	}
	if again := newModel(""); again.viewState() != defaultViewState() {
		t.Errorf("after R the saved view = %+v, want defaults", again.viewState())
	}
}

// TestViewState_UnprioritizedRestoresShowFlag verifies a saved 0 view relaunches as the
// unprioritized view and still shows the none items.
func TestViewState_UnprioritizedRestoresShowFlag(t *testing.T) {
	stateHome(t)
	m := newModel("")
	m.loading = false
	pressKey(t, m, '0')

	again := newModel("")
	if again.priority != "unprioritized" || !again.showUnprioritized {
		t.Errorf("relaunch = priority %q showUnprioritized %v, want unprioritized view", again.priority, again.showUnprioritized)
	}
}

func writeViewFile(t *testing.T, path, body string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(body), 0o644); err != nil {
		t.Fatal(err)
	}
}

// TestViewState_MissingCorruptAndUnknown covers the three launch states.
func TestViewState_MissingCorruptAndUnknown(t *testing.T) {
	t.Run("missing file is silent defaults", func(t *testing.T) {
		stateHome(t)
		m := newModel("")
		if m.statusMessage != "" {
			t.Errorf("missing file produced status %q", m.statusMessage)
		}
		if m.viewState() != defaultViewState() {
			t.Errorf("view = %+v, want defaults", m.viewState())
		}
	})

	t.Run("corrupt file is named in the status", func(t *testing.T) {
		path := stateHome(t)
		writeViewFile(t, path, "{not json")
		m := newModel("")
		if !strings.Contains(m.statusMessage, "tui-view.json") {
			t.Errorf("status %q does not name tui-view.json", m.statusMessage)
		}
		if m.viewState() != defaultViewState() {
			t.Errorf("view = %+v, want defaults", m.viewState())
		}
	})

	t.Run("unreadable file is named in the status", func(t *testing.T) {
		path := stateHome(t)
		// A directory where the file should be cannot be read as a file.
		if err := os.MkdirAll(path, 0o755); err != nil {
			t.Fatal(err)
		}
		m := newModel("")
		if !strings.Contains(m.statusMessage, "tui-view.json") {
			t.Errorf("status %q does not name tui-view.json", m.statusMessage)
		}
	})

	t.Run("unknown values fall back per field, vanished kind falls back once kinds load", func(t *testing.T) {
		path := stateHome(t)
		writeViewFile(t, path, `{"priority":"bogus","show_all":true,"source_type":"gopher","sort":"sideways","kind":"gone"}`)
		m := newModel("")
		if m.statusMessage != "" {
			t.Errorf("unknown values produced status %q", m.statusMessage)
		}
		want := defaultViewState()
		want.ShowAll = true // the one valid field is kept
		want.Kind = "gone"  // kind is judged only once the data is known
		if m.viewState() != want {
			t.Fatalf("view = %+v, want %+v", m.viewState(), want)
		}

		loaded, cmd := m.Update(itemsLoadedMsg{availableKinds: []string{"news"}})
		got := loaded.(Model)
		if got.kindFilter != "all" {
			t.Errorf("kindFilter = %q after kinds load, want all", got.kindFilter)
		}
		if cmd == nil {
			t.Error("expected a reload command after the kind fell back")
		}
	})

	t.Run("a kind present in the data is kept", func(t *testing.T) {
		path := stateHome(t)
		writeViewFile(t, path, `{"kind":"news"}`)
		m := newModel("")
		loaded, _ := m.Update(itemsLoadedMsg{availableKinds: []string{"news"}})
		if got := loaded.(Model).kindFilter; got != "news" {
			t.Errorf("kindFilter = %q, want news", got)
		}
	})
}

// TestViewState_SaveReplacesThroughRename verifies the view file is never opened in
// place: a hard link taken to the old file keeps the old bytes after a save, which an
// in-place write would overwrite, and no temporary file is left behind.
func TestViewState_SaveReplacesThroughRename(t *testing.T) {
	path := stateHome(t)
	if err := saveViewState(path, defaultViewState()); err != nil {
		t.Fatal(err)
	}
	old, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	link := path + ".link"
	if err := os.Link(path, link); err != nil {
		t.Fatal(err)
	}

	changed := defaultViewState()
	changed.Priority = "high"
	if err := saveViewState(path, changed); err != nil {
		t.Fatal(err)
	}

	linked, err := os.ReadFile(link)
	if err != nil {
		t.Fatal(err)
	}
	if string(linked) != string(old) {
		t.Error("save rewrote the existing file in place")
	}
	now, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(now), `"high"`) {
		t.Errorf("saved file does not hold the new view: %s", now)
	}
	entries, err := os.ReadDir(filepath.Dir(path))
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range entries {
		if e.Name() != "tui-view.json" && e.Name() != "tui-view.json.link" {
			t.Errorf("stray file left behind: %s", e.Name())
		}
	}
}

// TestViewState_FailedSaveReports verifies a save that cannot be written shows a
// status message and does not break the session.
func TestViewState_FailedSaveReports(t *testing.T) {
	path := stateHome(t)
	// A regular file where the directory should be makes the save fail.
	if err := os.MkdirAll(filepath.Dir(filepath.Dir(path)), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Dir(path), []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	m := newModel("")
	m.loading = false
	m = pressKey(t, m, '1')
	if !strings.Contains(m.statusMessage, "tui-view.json") {
		t.Errorf("failed save status = %q, want it to name tui-view.json", m.statusMessage)
	}
	if m.priority != "high" {
		t.Errorf("failed save disturbed the session: priority = %q", m.priority)
	}
}

// TestViewState_LiteralModelNeverWrites verifies a model not built by newModel has no
// view file and never writes one.
func TestViewState_LiteralModelNeverWrites(t *testing.T) {
	path := stateHome(t)
	pressKey(t, changedView(), 'u')
	if _, err := os.Stat(path); err == nil {
		t.Error("a literal model wrote the view file")
	}
}
