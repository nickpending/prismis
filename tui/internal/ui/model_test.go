package ui

import (
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"reflect"
	"runtime"
	"strings"
	"testing"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/commands"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

// remoteTestConfig points XDG_CONFIG_HOME at a temp config.toml with a
// [remote] key, which api.NewClientWithURL requires once a non-empty baseURL
// puts it in remote mode - even though the explicit baseURL argument (the
// httptest server's URL) overrides the config's own [remote].url. Restores
// the previous XDG_CONFIG_HOME via the returned func.
func remoteTestConfig(t *testing.T) {
	t.Helper()
	tmpDir := t.TempDir()
	configDir := filepath.Join(tmpDir, "prismis")
	if err := os.MkdirAll(configDir, 0755); err != nil {
		t.Fatalf("failed to create config dir: %v", err)
	}
	configContent := "[remote]\nurl = \"http://ignored\"\nkey = \"test-key\"\n"
	if err := os.WriteFile(filepath.Join(configDir, "config.toml"), []byte(configContent), 0644); err != nil {
		t.Fatalf("failed to write config: %v", err)
	}

	oldEnv := os.Getenv("XDG_CONFIG_HOME")
	os.Setenv("XDG_CONFIG_HOME", tmpDir)
	t.Cleanup(func() { os.Setenv("XDG_CONFIG_HOME", oldEnv) })
}

// emptyEntriesServer returns an httptest.Server that answers GET /api/entries
// with a valid, empty envelope - enough for fetchItemsRemote to succeed
// without asserting on request shape (that's cluster 1's concern).
func emptyEntriesServer(t *testing.T) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"","data":{"items":[],"total":0}}`)
	}))
	t.Cleanup(server.Close)
	return server
}

// TestBuildRefreshCmd_ThreadsIsAutoRefreshAndPreservesCursor verifies
// buildRefreshCmd (cluster 16) captures the current item's ID before the
// async fetch runs and threads isAutoRefresh through for both the
// manual-refresh and auto-refresh callers.
func TestBuildRefreshCmd_ThreadsIsAutoRefreshAndPreservesCursor(t *testing.T) {
	remoteTestConfig(t)
	server := emptyEntriesServer(t)

	for _, tc := range []struct {
		name          string
		isAutoRefresh bool
	}{
		{"manual refresh (RefreshMsg path)", false},
		{"auto refresh (autoRefreshMsg path)", true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			m := Model{
				remoteURL: server.URL,
				cursor:    1,
				items: []db.ContentItem{
					{ID: "item-a"},
					{ID: "item-b"},
				},
			}

			cmd := m.buildRefreshCmd(tc.isAutoRefresh)
			msg := cmd()

			result, ok := msg.(itemsLoadedMsg)
			if !ok {
				t.Fatalf("expected itemsLoadedMsg, got %T", msg)
			}
			if result.err != nil {
				t.Fatalf("expected no error, got %v", result.err)
			}
			if !result.preserveCursor {
				t.Error("expected preserveCursor to be true")
			}
			if result.targetItemID != "item-b" {
				t.Errorf("expected targetItemID 'item-b' (the cursor's item), got %q", result.targetItemID)
			}
			if result.isAutoRefresh != tc.isAutoRefresh {
				t.Errorf("expected isAutoRefresh=%v, got %v", tc.isAutoRefresh, result.isAutoRefresh)
			}
		})
	}
}

// TestBuildRefreshCmd_CursorOutOfBounds verifies an out-of-bounds cursor
// leaves targetItemID empty instead of panicking or reading stale data.
func TestBuildRefreshCmd_CursorOutOfBounds(t *testing.T) {
	remoteTestConfig(t)
	server := emptyEntriesServer(t)

	m := Model{remoteURL: server.URL, cursor: 5, items: nil}
	msg := m.buildRefreshCmd(false)()

	result, ok := msg.(itemsLoadedMsg)
	if !ok {
		t.Fatalf("expected itemsLoadedMsg, got %T", msg)
	}
	if result.targetItemID != "" {
		t.Errorf("expected empty targetItemID for out-of-bounds cursor, got %q", result.targetItemID)
	}
}

func TestNewModel(t *testing.T) {
	m := NewModel()

	if m.priority != "low" {
		t.Errorf("Expected initial priority to be 'low', got '%s'", m.priority)
	}

	if m.view != "list" {
		t.Errorf("Expected initial view to be 'list', got '%s'", m.view)
	}

	if m.cursor != 0 {
		t.Errorf("Expected initial cursor to be 0, got %d", m.cursor)
	}

	if !m.loading {
		t.Error("Expected initial loading state to be true")
	}

	if len(m.items) != 0 {
		t.Errorf("Expected empty items initially, got %d items", len(m.items))
	}
}

func TestModelUpdate(t *testing.T) {
	tests := []struct {
		name             string
		initialModel     Model
		msg              tea.Msg
		expectedCursor   int
		expectedQuit     bool
		expectedPriority string
		expectedLoading  bool
	}{
		{
			name: "Navigate down with j",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 1,
		},
		{
			name: "Navigate down with down arrow",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyDown},
			expectedCursor: 1,
		},
		{
			name: "Navigate up with k",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 1,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'k'}},
			expectedCursor: 0,
		},
		{
			name: "Don't navigate below 0",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'k'}},
			expectedCursor: 0,
		},
		{
			name: "Don't navigate past last item",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
				},
				cursor: 1,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'j'}},
			expectedCursor: 1,
		},
		{
			name: "Jump to top with g",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 2,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'g'}},
			expectedCursor: 0,
		},
		{
			name: "Jump to bottom with G",
			initialModel: Model{
				items: []db.ContentItem{
					{Title: "Item 1"},
					{Title: "Item 2"},
					{Title: "Item 3"},
				},
				cursor: 0,
			},
			msg:            tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'G'}},
			expectedCursor: 2,
		},
		{
			name:         "Quit with q",
			initialModel: Model{},
			msg:          tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'q'}},
			expectedQuit: true,
		},
		{
			name:         "Quit with ctrl+c",
			initialModel: Model{},
			msg:          tea.KeyMsg{Type: tea.KeyCtrlC},
			expectedQuit: true,
		},
		{
			name: "Switch to high priority",
			initialModel: Model{
				priority: "low",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'1'}},
			expectedPriority: "high",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to medium priority",
			initialModel: Model{
				priority: "low",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'2'}},
			expectedPriority: "medium",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to low priority",
			initialModel: Model{
				priority: "high",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'3'}},
			expectedPriority: "low",
			expectedLoading:  true,
			expectedCursor:   0,
		},
		{
			name: "Switch to all items",
			initialModel: Model{
				priority: "high",
				loading:  false,
			},
			msg:              tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'a'}},
			expectedPriority: "low",
			expectedLoading:  true,
			expectedCursor:   0,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// These keybindings dispatch on view and focusedPane (model.go:586-597,
			// 660-672). InitialModel starts at list/content (model.go:124,140); the
			// table omits them, so apply that starting state unless a case overrides it.
			if tt.initialModel.view == "" {
				tt.initialModel.view = "list"
			}
			if tt.initialModel.focusedPane == "" {
				tt.initialModel.focusedPane = "content"
			}

			updatedModel, cmd := tt.initialModel.Update(tt.msg)
			m := updatedModel.(Model)

			// Check if quit command was returned
			if tt.expectedQuit {
				if cmd == nil {
					t.Error("Expected quit command, got nil")
				}
				// Can't easily check if it's tea.Quit without exposing internals
				return
			}

			// Check cursor position
			if m.cursor != tt.expectedCursor {
				t.Errorf("Expected cursor %d, got %d", tt.expectedCursor, m.cursor)
			}

			// Check priority if it was expected to change
			if tt.expectedPriority != "" && m.priority != tt.expectedPriority {
				t.Errorf("Expected priority '%s', got '%s'", tt.expectedPriority, m.priority)
			}

			// Check loading state if priority changed
			if tt.expectedPriority != "" && m.loading != tt.expectedLoading {
				t.Errorf("Expected loading %v, got %v", tt.expectedLoading, m.loading)
			}
		})
	}
}

func TestModelUpdateItemsLoaded(t *testing.T) {
	m := Model{
		loading: true,
		cursor:  5, // Out of bounds cursor
	}

	testItems := []db.ContentItem{
		{Title: "Item 1", Priority: "high"},
		{Title: "Item 2", Priority: "medium"},
	}

	msg := itemsLoadedMsg{
		items: testItems,
		err:   nil,
	}

	updatedModel, _ := m.Update(msg)
	updated := updatedModel.(Model)

	if updated.loading {
		t.Error("Expected loading to be false after items loaded")
	}

	if len(updated.items) != 2 {
		t.Errorf("Expected 2 items, got %d", len(updated.items))
	}

	if updated.cursor != 0 {
		t.Error("Expected cursor to reset to 0 when out of bounds")
	}

	if updated.err != nil {
		t.Errorf("Expected no error, got %v", updated.err)
	}
}

// TestApplyFiltersClientSide_KindFilter verifies the kind filter narrows items to
// those whose analysis.kind matches, and that "all" leaves every item (classified or
// not) in place.
func TestApplyFiltersClientSide_KindFilter(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Title: "Release Item", Priority: "high", Analysis: `{"kind":"release"}`},
		{ID: "2", Title: "News Item", Priority: "high", Analysis: `{"kind":"news"}`},
		{ID: "3", Title: "Unclassified Item", Priority: "high", Analysis: `{"kind":null}`},
	}

	base := Model{priority: "low", showAll: true, showUnprioritized: true, filterType: "all"}

	all := base
	all.kindFilter = "all"
	if got := len(applyFiltersClientSide(items, all)); got != 3 {
		t.Errorf("kindFilter 'all' should keep every item, got %d", got)
	}

	release := base
	release.kindFilter = "release"
	filtered := applyFiltersClientSide(items, release)
	if len(filtered) != 1 || filtered[0].ID != "1" {
		t.Errorf("kindFilter 'release' should keep only item 1, got %+v", filtered)
	}

	// Unclassified items never match a specific kind filter
	unclassifiedFilter := base
	unclassifiedFilter.kindFilter = "release"
	for _, item := range applyFiltersClientSide(items, unclassifiedFilter) {
		if item.ID == "3" {
			t.Error("Unclassified item must not match a specific kindFilter")
		}
	}
}

// TestDistinctKindsFromItems verifies the sorted, deduplicated kind set mined from a
// slice of items (the remote-mode path, since it has no direct DB access).
func TestDistinctKindsFromItems(t *testing.T) {
	items := []db.ContentItem{
		{ID: "1", Analysis: `{"kind":"release"}`},
		{ID: "2", Analysis: `{"kind":"news"}`},
		{ID: "3", Analysis: `{"kind":"release"}`}, // duplicate
		{ID: "4", Analysis: `{"kind":null}`},      // unclassified - excluded
		{ID: "5", Analysis: ``},                   // no analysis - excluded
	}

	kinds := distinctKindsFromItems(items)
	expected := []string{"news", "release"}
	if len(kinds) != len(expected) {
		t.Fatalf("Expected %d kinds, got %d: %v", len(expected), len(kinds), kinds)
	}
	for i, k := range expected {
		if kinds[i] != k {
			t.Errorf("Expected kinds[%d] = %q, got %q (full: %v)", i, k, kinds[i], kinds)
		}
	}
}

// TestKindFilterCycling verifies pressing 'K' cycles kindFilter through "all" plus
// whatever kinds are present (m.availableKinds) - never a hardcoded copy of the ten.
func TestKindFilterCycling(t *testing.T) {
	m := Model{
		view:           "list",
		focusedPane:    "content",
		kindFilter:     "all",
		availableKinds: []string{"news", "release"},
	}

	updatedModel, _ := m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'K'}})
	m = updatedModel.(Model)
	if m.kindFilter != "news" {
		t.Errorf("Expected kindFilter 'news' after first cycle, got %q", m.kindFilter)
	}
	if !m.loading {
		t.Error("Expected loading to be true after cycling kind filter")
	}

	m.loading = false
	updatedModel, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'K'}})
	m = updatedModel.(Model)
	if m.kindFilter != "release" {
		t.Errorf("Expected kindFilter 'release' after second cycle, got %q", m.kindFilter)
	}

	m.loading = false
	updatedModel, _ = m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'K'}})
	m = updatedModel.(Model)
	if m.kindFilter != "all" {
		t.Errorf("Expected kindFilter to wrap back to 'all', got %q", m.kindFilter)
	}
}

// TestModelUpdate_KindMsg verifies commands.KindMsg (the :kind command) sets the kind
// filter and triggers a reload, mirroring how commands.ArchivedMsg is handled.
func TestModelUpdate_KindMsg(t *testing.T) {
	m := Model{view: "list", focusedPane: "content", kindFilter: "all"}

	updatedModel, cmd := m.Update(commands.KindMsg{Kind: "incident"})
	updated := updatedModel.(Model)

	if updated.kindFilter != "incident" {
		t.Errorf("Expected kindFilter 'incident', got %q", updated.kindFilter)
	}
	if !updated.loading {
		t.Error("Expected loading to be true after KindMsg")
	}
	if cmd == nil {
		t.Error("Expected a command to reload items after KindMsg")
	}
}

func TestModelView(t *testing.T) {
	tests := []struct {
		name     string
		model    Model
		contains []string
	}{
		{
			name: "Loading state",
			model: Model{
				loading:  true,
				priority: "high",
			},
			contains: []string{"Loading content..."},
		},
		{
			name: "Error state",
			model: Model{
				loading: false,
				err:     fmt.Errorf("Database error"),
			},
			contains: []string{"Error:", "Database error"},
		},
		{
			name: "Empty items",
			model: Model{
				loading:  false,
				priority: "high",
				items:    []db.ContentItem{},
			},
			contains: []string{"No unread items", "Press 'a' to add sources"},
		},
		{
			name: "Items with cursor",
			model: Model{
				loading:  false,
				priority: "low",
				cursor:   1,
				items: []db.ContentItem{
					{Title: "First Item", Priority: "high"},
					{Title: "Second Item", Priority: "medium"},
					{Title: "Third Item", Priority: "low"},
				},
			},
			contains: []string{
				"PRISMIS",
				"3 items",
				"First Item",
				// cursor marker sits on the second row
				"▸ ●  2. Second Item",
				"Third Item",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			view := tt.model.View()
			for _, expected := range tt.contains {
				if !contains(view, expected) {
					t.Errorf("Expected view to contain '%s', but it didn't.\nView: %s", expected, view)
				}
			}
		})
	}
}

// agentOutcomeLine is what the stub prismis-cli reports, the shape of a herdr launch.
const agentOutcomeLine = "opened prismis-abcd1234 in pane w9:p9"

// observedState is the Model a running Program reached after one message.
type observedState struct {
	msg   tea.Msg
	model Model
}

// observedModel runs the real Model inside a tea.Program and reports every state it
// reaches, so a test waits on an outcome instead of sleeping. Init returns nothing: the
// items a test gives the Model stay its items, with no fetch replacing them.
type observedModel struct {
	Model
	states chan observedState
}

func (o observedModel) Init() tea.Cmd { return nil }

func (o observedModel) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	next, cmd := o.Model.Update(msg)
	o.Model = next.(Model)
	o.states <- observedState{msg: msg, model: o.Model}
	return o, cmd
}

// agentProgram runs m in a real tea.Program whose input is the read end of an os.Pipe,
// which ExecProcess needs to hand the terminal to a child and take it back. It returns
// the pipe's write end, for keys, and the stream of states.
func agentProgram(t *testing.T, m Model) (*os.File, chan observedState) {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	states := make(chan observedState, 1024)
	p := tea.NewProgram(observedModel{Model: m, states: states}, tea.WithInput(r), tea.WithOutput(io.Discard))
	done := make(chan struct{})
	go func() {
		defer close(done)
		_, _ = p.Run()
	}()
	t.Cleanup(func() {
		p.Quit()
		<-done
		_ = w.Close()
		_ = r.Close()
	})
	return w, states
}

// waitFor returns the first state reached after a message that match accepts, and
// false when none arrives within 5 s.
func waitFor(states chan observedState, match func(tea.Msg) bool) (Model, bool) {
	deadline := time.After(5 * time.Second)
	for {
		select {
		case s := <-states:
			if match(s.msg) {
				return s.model, true
			}
		case <-deadline:
			return Model{}, false
		}
	}
}

func isAgentDone(msg tea.Msg) bool {
	_, ok := msg.(operations.AgentDoneMsg)
	return ok
}

// typeKeys writes each key to the Program's input in turn and waits for the Model to
// receive it before writing the next, as keys typed by hand arrive one at a time. It
// returns the state after the last key.
func typeKeys(t *testing.T, w *os.File, states chan observedState, keys ...string) Model {
	t.Helper()
	var got Model
	for _, key := range keys {
		if _, err := w.WriteString(key); err != nil {
			t.Fatal(err)
		}
		var ok bool
		got, ok = waitFor(states, func(msg tea.Msg) bool {
			_, isKey := msg.(tea.KeyMsg)
			return isKey
		})
		if !ok {
			t.Fatalf("key %q never reached the Model", key)
		}
	}
	return got
}

var colonAgent = []string{":", "a", "g", "e", "n", "t", "\r"}

// stubAgentCLI puts a stub prismis-cli alone on PATH. Each run appends its argv,
// NUL-separated and ended by a newline, to the returned record file, writes line to
// the path after --status-file, and exits with code. TMPDIR is a fresh directory, so
// the status file the TUI creates is the only file that can appear in it.
func stubAgentCLI(t *testing.T, line string, code int) (record, tmpDir string) {
	t.Helper()
	if runtime.GOOS == "windows" {
		t.Skip("shell-script stub needs a POSIX shell")
	}
	binDir := t.TempDir()
	tmpDir = t.TempDir()
	record = filepath.Join(t.TempDir(), "argv.record")
	script := "#!/bin/sh\n" +
		"printf '%s\\0' \"$@\" >> '" + record + "'\n" +
		"printf '\\n' >> '" + record + "'\n" +
		"prev=''\n" +
		"for a in \"$@\"; do\n" +
		"  if [ \"$prev\" = '--status-file' ]; then printf '%s\\n' '" + line + "' > \"$a\"; fi\n" +
		"  prev=\"$a\"\n" +
		"done\n" +
		fmt.Sprintf("exit %d\n", code)
	if err := os.WriteFile(filepath.Join(binDir, "prismis-cli"), []byte(script), 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv("PATH", binDir)
	t.Setenv("TMPDIR", tmpDir)
	return record, tmpDir
}

// recordedRuns returns the argv of every recorded stub run.
func recordedRuns(t *testing.T, record string) [][]string {
	t.Helper()
	data, err := os.ReadFile(record)
	if os.IsNotExist(err) {
		return nil
	}
	if err != nil {
		t.Fatal(err)
	}
	var runs [][]string
	for run := range strings.SplitSeq(strings.TrimSuffix(string(data), "\n"), "\n") {
		runs = append(runs, strings.Split(strings.TrimSuffix(run, "\x00"), "\x00"))
	}
	return runs
}

// agentTestModel is a remote-mode Model over ds with two items, the cursor on the
// first, and a working command mode for `:agent`.
func agentTestModel(ds *detailServer) Model {
	m := remoteModel(ds, slimItem(hydrateItemID), slimItem("item-2"))
	m.commandMode = NewCommandMode()
	return m
}

// TestOpenInAgent_HandsTheFocusedItemToPrismisCLI drives `A` and `:agent` through the
// real Model in a real Program and checks the argv the stub prismis-cli ran with, the
// status line the Program shows afterwards, and that the status file is gone.
func TestOpenInAgent_HandsTheFocusedItemToPrismisCLI(t *testing.T) {
	for _, tc := range []struct {
		name   string
		remote bool
		keys   []string
	}{
		{"A in remote mode", true, []string{"A"}},
		{":agent in remote mode", true, colonAgent},
		{"A in local mode", false, []string{"A"}},
		{":agent in local mode", false, colonAgent},
	} {
		t.Run(tc.name, func(t *testing.T) {
			remoteTestConfig(t)
			ds := newDetailServer(t)
			record, tmpDir := stubAgentCLI(t, agentOutcomeLine, 0)
			m := agentTestModel(ds)
			if !tc.remote {
				m.remoteURL = ""
			}
			w, states := agentProgram(t, m)

			typeKeys(t, w, states, tc.keys...)
			got, done := waitFor(states, isAgentDone)

			runs := recordedRuns(t, record)
			if len(runs) != 1 {
				t.Fatalf("prismis-cli ran %d times, want 1: %q", len(runs), runs)
			}
			if !done {
				t.Fatal("no AgentDoneMsg within 5 s of the run")
			}
			argv := runs[0]
			want := []string{"open", hydrateItemID, "--status-file"}
			if tc.remote {
				want = append([]string{"--remote", ds.URL}, want...)
			}
			if len(argv) != len(want)+1 || !reflect.DeepEqual(argv[:len(want)], want) {
				t.Fatalf("argv = %q, want %q followed by a status-file path", argv, want)
			}
			statusPath := argv[len(want)]
			if got.statusMessage != agentOutcomeLine {
				t.Errorf("status line = %q, want %q", got.statusMessage, agentOutcomeLine)
			}
			if !strings.Contains(got.View(), agentOutcomeLine) {
				t.Errorf("rendered view does not show the outcome line:\n%s", got.View())
			}
			if _, err := os.Stat(statusPath); !os.IsNotExist(err) {
				t.Errorf("status file %s still exists (stat err %v)", statusPath, err)
			}
			if left, _ := os.ReadDir(tmpDir); len(left) != 0 {
				t.Errorf("TMPDIR still holds %d file(s)", len(left))
			}
		})
	}
}

// TestOpenInAgent_ShowsTheCLIFailureAndKeepsTheTUIWorking checks that the CLI's own
// failure line reaches the status line, and that the TUI still answers keys after.
func TestOpenInAgent_ShowsTheCLIFailureAndKeepsTheTUIWorking(t *testing.T) {
	const failure = `No agent configured: set [agent] target = "claude", "pi" or "herdr" in config.toml`
	remoteTestConfig(t)
	ds := newDetailServer(t)
	record, _ := stubAgentCLI(t, failure, 1)
	w, states := agentProgram(t, agentTestModel(ds))

	typeKeys(t, w, states, "A")
	got, done := waitFor(states, isAgentDone)

	if runs := recordedRuns(t, record); len(runs) != 1 {
		t.Fatalf("prismis-cli ran %d times, want 1", len(runs))
	}
	if !done {
		t.Fatal("no AgentDoneMsg within 5 s of the run")
	}
	if got.statusMessage != failure {
		t.Errorf("status line = %q, want %q", got.statusMessage, failure)
	}

	got = typeKeys(t, w, states, "j")
	if got.cursor != 1 {
		t.Errorf("cursor after j = %d, want 1", got.cursor)
	}
}

// TestOpenInAgent_WithoutPrismisCLIRunsNothing checks that with no prismis-cli on PATH
// the TUI says so, and creates no status file for a run that cannot happen.
func TestOpenInAgent_WithoutPrismisCLIRunsNothing(t *testing.T) {
	remoteTestConfig(t)
	ds := newDetailServer(t)
	tmpDir := t.TempDir()
	t.Setenv("PATH", t.TempDir())
	t.Setenv("TMPDIR", tmpDir)
	w, states := agentProgram(t, agentTestModel(ds))

	typeKeys(t, w, states, "A")
	got, done := waitFor(states, isAgentDone)

	want := "prismis-cli is not installed; `A` needs it"
	if !done {
		t.Fatalf("no AgentDoneMsg within 5 s; want the status line %q", want)
	}
	if got.statusMessage != want {
		t.Errorf("status line = %q, want %q", got.statusMessage, want)
	}
	if left, _ := os.ReadDir(tmpDir); len(left) != 0 {
		t.Errorf("TMPDIR holds %d file(s); no status file should be created", len(left))
	}
}

// Helper function to check if string contains substring
func contains(s, substr string) bool {
	return len(substr) > 0 && len(s) >= len(substr) &&
		(s == substr || len(s) > len(substr) && containsHelper(s, substr))
}

func containsHelper(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
