package ui

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/charmbracelet/bubbles/viewport"
	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"
	"github.com/charmbracelet/x/ansi"
	"github.com/muesli/termenv"
	"github.com/nickpending/prismis/internal/db"
	"github.com/nickpending/prismis/internal/ui/operations"
)

// healthSources holds one source per mark, each with more than three failures, where
// the old rules coloured a source by its error count and its last fetch's age.
func healthSources() []db.Source {
	next := time.Date(2026, 11, 8, 10, 0, 0, 0, time.UTC)
	newest := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC)
	keptAt := time.Date(2026, 11, 1, 0, 0, 0, 0, time.UTC)
	zero := 0
	return []db.Source{
		{ID: "h", Name: "Healthy Feed", Type: "rss", Active: true, Health: "healthy", ErrorCount: 4},
		{ID: "b", Name: "Broken Feed", Type: "rss", Active: true, Health: "broken", Attention: true, ErrorCount: 5,
			LastError: "HTTP 404\x1b[31m Not\r\nFound\u009b", NextFetchAt: &next},
		{ID: "e", Name: "Empty Feed", Type: "rss", Active: true, Health: "empty", Attention: true, ErrorCount: 4,
			EntriesServed: &zero},
		{ID: "d", Name: "Dormant Feed", Type: "rss", Active: true, Health: "dormant", Attention: true, ErrorCount: 4,
			NewestItemAt: &newest},
		{ID: "k", Name: "Kept Feed", Type: "rss", Active: true, Health: "dormant", KeptHealth: "dormant", ErrorCount: 4,
			NewestItemAt: &newest, KeptAt: &keptAt},
		{ID: "p", Name: "Paused Feed", Type: "rss", Active: false, Health: "broken", ErrorCount: 4},
	}
}

// markAndNext finds the line naming name, and returns the mark drawn before the name
// and the line after it. A box border around the line is not part of it.
func markAndNext(t *testing.T, rendered, name string) (string, string) {
	t.Helper()
	lines := strings.Split(rendered, "\n")
	for i, line := range lines {
		if !strings.Contains(line, name) {
			continue
		}
		fields := strings.Fields(strings.TrimPrefix(strings.Trim(line, " │"), "▸"))
		next := ""
		if i+1 < len(lines) {
			next = lines[i+1]
		}
		return fields[0], next
	}
	t.Fatalf("%q is not rendered in:\n%s", name, rendered)
	return "", ""
}

// TestSourceMarksAndEvidenceFollowTheStoredHealth: the sources screen and the main
// view's source list draw the same mark for each source, read from its health, keep
// and paused state alone; the sources screen's evidence line says why, on one line
// with no terminal control from the stored error text.
func TestSourceMarksAndEvidenceFollowTheStoredHealth(t *testing.T) {
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.Ascii)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })

	sources := healthSources()
	modal := NewSourceModal()
	modal.SetSize(188, 51)
	modal.Show()
	modal.LoadSources(sources)
	screen := modal.View(CleanCyberTheme)
	main := Model{sources: sources, sourcesViewport: viewport.New(60, 30), theme: CleanCyberTheme}
	list := main.buildSourcesContent(CleanCyberTheme)

	want := map[string]string{
		"Healthy Feed": "●", "Broken Feed": "✗", "Empty Feed": "◇",
		"Dormant Feed": "◐", "Kept Feed": "□", "Paused Feed": "○",
	}
	evidence := map[string]string{}
	for name, mark := range want {
		onScreen, next := markAndNext(t, screen, name)
		inList, _ := markAndNext(t, list, name)
		if onScreen != mark || inList != mark {
			t.Errorf("%s: want mark %q in both renderers, got %q on the sources screen and %q in the main list",
				name, mark, onScreen, inList)
		}
		evidence[name] = next
	}

	broken := evidence["Broken Feed"]
	for _, part := range []string{"5 failures", "HTTP 404", "Not Found",
		"next try " + time.Date(2026, 11, 8, 10, 0, 0, 0, time.UTC).Local().Format("2006-01-02 15:04")} {
		if !strings.Contains(broken, part) {
			t.Errorf("broken evidence %q lacks %q", broken, part)
		}
	}
	for _, r := range broken {
		if r < 0x20 || r == 0x7f || (r >= 0x80 && r <= 0x9f) {
			t.Errorf("broken evidence %q carries control %U", broken, r)
		}
	}
	if !strings.Contains(evidence["Empty Feed"], "served 0 entries") {
		t.Errorf("empty evidence %q lacks the served count", evidence["Empty Feed"])
	}
	newest := time.Date(2026, 6, 1, 0, 0, 0, 0, time.UTC).Local().Format("2006-01-02")
	if !strings.Contains(evidence["Dormant Feed"], "newest item "+newest) {
		t.Errorf("dormant evidence %q lacks its newest item date", evidence["Dormant Feed"])
	}
	if !strings.Contains(evidence["Kept Feed"], "kept (dormant)") {
		t.Errorf("kept evidence %q lacks its kept state", evidence["Kept Feed"])
	}
}

// remoteConfigAt points api.NewClient at serverURL through a temp XDG_CONFIG_HOME.
func remoteConfigAt(t *testing.T, serverURL string) {
	t.Helper()
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "prismis"), 0o755); err != nil {
		t.Fatal(err)
	}
	config := fmt.Sprintf("[remote]\nurl = %q\nkey = \"test-key\"\n", serverURL)
	if err := os.WriteFile(filepath.Join(dir, "prismis", "config.toml"), []byte(config), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv("XDG_CONFIG_HOME", dir)
}

// TestSourceModal_KKeepsAFlaggedSourceAndUnkeepsAKeptOne: K on a source that raises the
// warning asks the daemon to keep it, K on a kept source asks it to unkeep, and the
// list's help line names K.
func TestSourceModal_KKeepsAFlaggedSourceAndUnkeepsAKeptOne(t *testing.T) {
	const flaggedID = "11111111-1111-1111-1111-111111111111"
	const keptID = "22222222-2222-2222-2222-222222222222"
	var mu sync.Mutex
	var requests []string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		mu.Lock()
		requests = append(requests, r.Method+" "+r.URL.Path)
		mu.Unlock()
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"success":true,"message":"ok","data":{}}`)
	}))
	t.Cleanup(server.Close)
	remoteConfigAt(t, server.URL)

	modal := NewSourceModal()
	modal.SetSize(120, 40)
	modal.visible = true
	modal.mode = "list"
	modal.LoadSources([]db.Source{
		{ID: flaggedID, Name: "Flagged", Type: "rss", Active: true, Health: "empty", Attention: true},
		{ID: keptID, Name: "Kept", Type: "rss", Active: true, Health: "dormant", KeptHealth: "dormant"},
	})
	if !strings.Contains(modal.View(CleanCyberTheme), "[K]eep") {
		t.Errorf("the list's help line must name K")
	}

	press := func(m SourceModal, key rune) (SourceModal, tea.Cmd) {
		return m.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{key}})
	}
	modal, cmd := press(modal, 'K')
	if cmd == nil {
		t.Fatal("K on a flagged source sent nothing")
	}
	cmd()
	modal, _ = press(modal, 'j')
	_, cmd = press(modal, 'K')
	if cmd == nil {
		t.Fatal("K on a kept source sent nothing")
	}
	cmd()

	mu.Lock()
	defer mu.Unlock()
	want := []string{"PATCH /api/sources/" + flaggedID + "/keep", "PATCH /api/sources/" + keptID + "/unkeep"}
	if strings.Join(requests, "\n") != strings.Join(want, "\n") {
		t.Errorf("want requests %q, got %q", want, requests)
	}
}

func TestSourceModal_LoadSources_UpdatesContent(t *testing.T) {
	// Create a new source modal
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	// Initial sources
	initialSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
		{ID: "2", Name: "Source 2", Type: "reddit", Active: true},
		{ID: "3", Name: "Source 3", Type: "youtube", Active: true},
	}

	// Load initial sources
	modal.LoadSources(initialSources)

	// Get initial content - what SourceModal.View actually renders, not the dead
	// Modal.content field (source_modal.go's View() never reads it in list mode).
	initialContent := modal.View(CleanCyberTheme)
	if !strings.Contains(initialContent, "Source 1") {
		t.Errorf("Expected content to contain 'Source 1', got: %s", initialContent)
	}
	if !strings.Contains(initialContent, "Source 2") {
		t.Errorf("Expected content to contain 'Source 2', got: %s", initialContent)
	}
	if !strings.Contains(initialContent, "Source 3") {
		t.Errorf("Expected content to contain 'Source 3', got: %s", initialContent)
	}

	// Simulate deletion - remove Source 2
	updatedSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
		{ID: "3", Name: "Source 3", Type: "youtube", Active: true},
	}

	// Load updated sources
	modal.LoadSources(updatedSources)

	// Get updated content
	updatedContent := modal.View(CleanCyberTheme)

	// Verify content was updated
	if initialContent == updatedContent {
		t.Error("Content should have changed after loading new sources")
	}
	if !strings.Contains(updatedContent, "Source 1") {
		t.Errorf("Expected updated content to contain 'Source 1', got: %s", updatedContent)
	}
	if strings.Contains(updatedContent, "Source 2") {
		t.Errorf("Deleted source 'Source 2' should not appear in content, got: %s", updatedContent)
	}
	if !strings.Contains(updatedContent, "Source 3") {
		t.Errorf("Expected updated content to contain 'Source 3', got: %s", updatedContent)
	}

	// Verify cursor adjustment
	if modal.cursor > len(updatedSources)-1 {
		t.Errorf("Cursor should be within bounds, got cursor=%d for %d sources", modal.cursor, len(updatedSources))
	}
}

func TestSourceModal_LoadSources_EmptyList(t *testing.T) {
	// Create a new source modal
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	// Initial sources
	initialSources := []db.Source{
		{ID: "1", Name: "Source 1", Type: "rss", Active: true},
	}

	// Load initial sources
	modal.LoadSources(initialSources)
	if !strings.Contains(modal.View(CleanCyberTheme), "Source 1") {
		t.Errorf("Expected content to contain 'Source 1', got: %s", modal.View(CleanCyberTheme))
	}

	// Load empty sources (all deleted)
	modal.LoadSources([]db.Source{})

	// Verify content shows "No sources configured"
	if !strings.Contains(modal.View(CleanCyberTheme), "No sources configured") {
		t.Errorf("Expected content to show 'No sources configured', got: %s", modal.View(CleanCyberTheme))
	}
	if strings.Contains(modal.View(CleanCyberTheme), "Source 1") {
		t.Errorf("Source 1 should not appear after loading empty list, got: %s", modal.View(CleanCyberTheme))
	}
}

func TestSourceModal_ErrorMessageDisplay(t *testing.T) {
	// One existing source mirrors the real trigger for errorMsg: a pause/remove/edit
	// failing on an existing source (model.go's operations.SourceOperationMsg handler).
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"
	modal.LoadSources([]db.Source{{ID: "1", Name: "Source 1", Type: "rss", Active: true}})
	modal.errorMsg = "Subreddit r/ai does not exist"

	// Update content
	modal.UpdateContent()

	// Verify error message appears in what the modal actually displays.
	if !strings.Contains(modal.View(CleanCyberTheme), "Subreddit r/ai does not exist") {
		t.Errorf("Expected error message in content, got: %s", modal.View(CleanCyberTheme))
	}
}

// TestSourceModal_MarksANaughtySource: a source the daemon fetches with the browser
// user-agent carries `[browser UA]` after its name; an unmarked one does not.
func TestSourceModal_MarksANaughtySource(t *testing.T) {
	modal := NewSourceModal()
	modal.SetSize(120, 40)
	modal.visible = true
	modal.mode = "list"
	modal.LoadSources([]db.Source{
		{ID: "1", Name: "Marked Feed", Type: "rss", Active: true, Naughty: true},
		{ID: "2", Name: "Plain Feed", Type: "rss", Active: true},
	})

	lines := map[string]string{}
	for line := range strings.SplitSeq(modal.View(CleanCyberTheme), "\n") {
		for _, name := range []string{"Marked Feed", "Plain Feed"} {
			if strings.Contains(line, name) {
				lines[name] = line
			}
		}
	}
	marked, plain := lines["Marked Feed"], lines["Plain Feed"]
	if marked == "" || plain == "" {
		t.Fatalf("both sources must render; got %q", lines)
	}
	if !strings.Contains(marked, "Marked Feed [browser UA]") {
		t.Errorf("marked line lacks the marker after its name: %q", marked)
	}
	if strings.Contains(plain, "[browser UA]") {
		t.Errorf("unmarked line carries the marker: %q", plain)
	}
}

// TestSourceModal_ErrorVisibleWhateverTheListLength covers gh #87: the list-mode error
// line used to be the last line inside the scrolled viewport, so with no sources (the
// "No sources configured" filler) or more sources than the viewport holds it fell below
// the visible window and the user never saw why their action failed.
func TestSourceModal_ErrorVisibleWhateverTheListLength(t *testing.T) {
	many := make([]db.Source, 12)
	for i := range many {
		many[i] = db.Source{ID: fmt.Sprintf("%d", i), Name: fmt.Sprintf("Source %d", i), Type: "rss", Active: true}
	}
	for name, sources := range map[string][]db.Source{"no sources": {}, "twelve sources": many} {
		t.Run(name, func(t *testing.T) {
			modal := NewSourceModal()
			modal.SetSize(120, 40)
			modal.visible = true
			modal.mode = "list"
			modal.LoadSources(sources)
			modal.errorMsg = "Subreddit r/ai does not exist"
			modal.UpdateContent()

			if view := modal.View(CleanCyberTheme); !strings.Contains(view, "Subreddit r/ai does not exist") {
				t.Errorf("error line not visible with %s; view:\n%s", name, view)
			}
		})
	}
}

// TestSourceModal_Update_AddMode_TabSwitchesActiveFieldAndFocus drives SourceModal.Update
// with real tea.KeyMsg values through the "add" mode's tab handling, which now delegates to
// the consolidated handleFieldTab() (dedup-triage.md cluster 5b). A regression that broke the
// url<->name swap or the focus/blur pairing in either direction would fail this test.
func TestSourceModal_Update_AddMode_TabSwitchesActiveFieldAndFocus(t *testing.T) {
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	// Enter add mode the real way: press "a" from list mode.
	updated, _ := modal.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'a'}})
	if updated.mode != "add" {
		t.Fatalf("expected mode 'add' after pressing 'a', got %q", updated.mode)
	}
	if updated.activeField != "url" || !updated.urlInput.Focused() || updated.nameInput.Focused() {
		t.Fatalf("expected url field focused after entering add mode, got activeField=%q urlFocused=%v nameFocused=%v",
			updated.activeField, updated.urlInput.Focused(), updated.nameInput.Focused())
	}

	// Tab: handleFieldTab must move focus url -> name.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyTab})
	if updated.activeField != "name" {
		t.Errorf("expected activeField 'name' after tab, got %q", updated.activeField)
	}
	if updated.urlInput.Focused() {
		t.Errorf("expected urlInput blurred after tab to name")
	}
	if !updated.nameInput.Focused() {
		t.Errorf("expected nameInput focused after tab to name")
	}

	// Tab again: handleFieldTab must move focus back to url.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyTab})
	if updated.activeField != "url" {
		t.Errorf("expected activeField 'url' after second tab, got %q", updated.activeField)
	}
	if !updated.urlInput.Focused() || updated.nameInput.Focused() {
		t.Errorf("expected urlInput focused and nameInput blurred after second tab")
	}
}

// TestSourceModal_Update_AddMode_TextInputPassthroughRoutesToActiveField proves
// updateActiveTextInput (the consolidated default-case passthrough, cluster 5b) routes typed
// keys to whichever field tab last activated, not always the same one - a routing bug here
// would let a user's URL keystrokes silently land in the name field or vice versa.
func TestSourceModal_Update_AddMode_TextInputPassthroughRoutesToActiveField(t *testing.T) {
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	updated, _ := modal.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'a'}})

	// activeField starts at "url" - typing must reach urlInput, not nameInput.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'h', 't', 't', 'p'}})
	if updated.urlInput.Value() != "http" {
		t.Errorf("expected urlInput to receive typed runes while active, got %q", updated.urlInput.Value())
	}
	if updated.nameInput.Value() != "" {
		t.Errorf("expected nameInput untouched while url is active, got %q", updated.nameInput.Value())
	}

	// Tab to name, then type - must reach nameInput now, not urlInput.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyTab})
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'m', 'y'}})
	if updated.nameInput.Value() != "my" {
		t.Errorf("expected nameInput to receive typed runes after tab, got %q", updated.nameInput.Value())
	}
	if updated.urlInput.Value() != "http" {
		t.Errorf("expected urlInput unchanged after switching active field, got %q", updated.urlInput.Value())
	}
}

// TestSourceModal_Update_AddMode_EscClearsFormAndReturnsToList proves esc in "add" mode, which
// now delegates to clearFormAndReturnToList() (cluster 5b), clears both fields and the error
// message and returns to list mode with both inputs blurred.
func TestSourceModal_Update_AddMode_EscClearsFormAndReturnsToList(t *testing.T) {
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"

	updated, _ := modal.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'a'}})
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'h', 't', 't', 'p'}})
	updated.errorMsg = "URL is required"

	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyEscape})

	if updated.mode != "list" {
		t.Errorf("expected mode 'list' after esc, got %q", updated.mode)
	}
	if updated.urlInput.Value() != "" {
		t.Errorf("expected urlInput cleared after esc, got %q", updated.urlInput.Value())
	}
	if updated.nameInput.Value() != "" {
		t.Errorf("expected nameInput cleared after esc, got %q", updated.nameInput.Value())
	}
	if updated.errorMsg != "" {
		t.Errorf("expected errorMsg cleared after esc, got %q", updated.errorMsg)
	}
	if updated.urlInput.Focused() || updated.nameInput.Focused() {
		t.Errorf("expected both textinputs blurred after returning to list")
	}
}

// TestSourceModal_Update_EditMode_TabTextInputAndEsc drives SourceModal.Update through "edit"
// mode's tab, textinput-passthrough and esc handling in one sequence - the same three
// consolidated helpers as the add-mode tests above (handleFieldTab, updateActiveTextInput,
// clearFormAndReturnToList), proven from the edit side since edit pre-fills the form from an
// existing source rather than starting blank.
func TestSourceModal_Update_EditMode_TabTextInputAndEsc(t *testing.T) {
	modal := NewSourceModal()
	modal.visible = true
	modal.mode = "list"
	modal.LoadSources([]db.Source{
		{ID: "1", Name: "Existing", Type: "rss", URL: "http://existing.example", Active: true},
	})

	// Enter edit mode the real way: press enter with a source selected.
	updated, _ := modal.Update(tea.KeyMsg{Type: tea.KeyEnter})
	if updated.mode != "edit" {
		t.Fatalf("expected mode 'edit' after enter with a source selected, got %q", updated.mode)
	}
	if updated.urlInput.Value() != "http://existing.example" {
		t.Fatalf("expected urlInput prefilled with the source's URL, got %q", updated.urlInput.Value())
	}
	if !updated.urlInput.Focused() || updated.nameInput.Focused() {
		t.Fatalf("expected url field focused after entering edit mode")
	}

	// Tab: handleFieldTab must move focus url -> name.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyTab})
	if updated.activeField != "name" || updated.urlInput.Focused() || !updated.nameInput.Focused() {
		t.Errorf("expected activeField 'name' with nameInput focused after tab, got activeField=%q urlFocused=%v nameFocused=%v",
			updated.activeField, updated.urlInput.Focused(), updated.nameInput.Focused())
	}

	// Passthrough: typing while name is active must reach nameInput, not urlInput.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune{'!'}})
	if !strings.Contains(updated.nameInput.Value(), "!") {
		t.Errorf("expected nameInput to receive the typed rune while active, got %q", updated.nameInput.Value())
	}
	if updated.urlInput.Value() != "http://existing.example" {
		t.Errorf("expected urlInput unchanged while name is active, got %q", updated.urlInput.Value())
	}

	// Esc: clearFormAndReturnToList must clear the form and return to list.
	updated, _ = updated.Update(tea.KeyMsg{Type: tea.KeyEscape})
	if updated.mode != "list" {
		t.Errorf("expected mode 'list' after esc, got %q", updated.mode)
	}
	if updated.urlInput.Value() != "" || updated.nameInput.Value() != "" {
		t.Errorf("expected form cleared after esc, got url=%q name=%q", updated.urlInput.Value(), updated.nameInput.Value())
	}
	if updated.urlInput.Focused() || updated.nameInput.Focused() {
		t.Errorf("expected both textinputs blurred after returning to list")
	}
}

// real404Error is the error the RSS fetcher stored against a local server answering 404:
// the fixture's first line is the error as a JSON string, its second the error's length.
func real404Error(t *testing.T) string {
	t.Helper()
	raw, err := os.ReadFile(filepath.Join("testdata", "real-404-error.txt"))
	if err != nil {
		t.Fatal(err)
	}
	lines := strings.Split(strings.TrimSpace(string(raw)), "\n")
	if len(lines) != 2 {
		t.Fatalf("fixture holds %d lines, want the error and its length", len(lines))
	}
	var text string
	if err := json.Unmarshal([]byte(lines[0]), &text); err != nil {
		t.Fatal(err)
	}
	if n, err := strconv.Atoi(lines[1]); err != nil || utf8.RuneCountInString(text) != n {
		t.Fatalf("fixture error is %d runes, its length line reads %q", utf8.RuneCountInString(text), lines[1])
	}
	return text
}

// The host set's flagged sources, by index: among the first, middle and last ten.
const hostBroken = 42

var (
	hostEmpty   = []int{2, 24}
	hostDormant = []int{6, 20, 27, 45, 47}
)

// hostSet is the operator host's set at the walk: 49 sources, eight of them with an
// evidence line - NCC Group broken with the real 404 error, error_count 6 and a next try;
// two empty; five dormant.
func hostSet(t *testing.T) []db.Source {
	t.Helper()
	next := time.Date(2026, 11, 8, 10, 0, 0, 0, time.UTC)
	fetched := time.Date(2026, 11, 6, 9, 30, 0, 0, time.UTC)
	newest := time.Date(2026, 5, 1, 0, 0, 0, 0, time.UTC)
	zero := 0
	types := []string{"rss", "rss", "youtube", "rss", "reddit", "rss", "youtube", "file"}
	sources := make([]db.Source, 49)
	for i := range sources {
		sources[i] = db.Source{ID: fmt.Sprintf("src-%02d", i), Name: fmt.Sprintf("Host Source %02d", i),
			Type: types[i%len(types)], Active: true, Health: "healthy", UnreadCount: i % 7, LastFetched: &fetched}
	}
	for _, i := range hostEmpty {
		sources[i].Health, sources[i].Attention, sources[i].EntriesServed = "empty", true, &zero
	}
	for _, i := range hostDormant {
		sources[i].Health, sources[i].Attention, sources[i].NewestItemAt = "dormant", true, &newest
	}
	sources[hostBroken] = db.Source{ID: "src-ncc", Name: "NCC Group", Type: "rss", Active: true, Health: "broken",
		Attention: true, ErrorCount: 6, LastError: real404Error(t), NextFetchAt: &next}
	return sources
}

// hostEvidence is the indexes of the host set's sources that carry an evidence line.
func hostEvidence() []int {
	idx := append(append([]int{hostBroken}, hostEmpty...), hostDormant...)
	slices.Sort(idx)
	return idx
}

// asciiProfile renders without colour for the test, so a view's text is its bytes.
func asciiProfile(t *testing.T) {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.Ascii)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
}

// step sends msg through the real Model's Update.
func step(t *testing.T, m Model, msg tea.Msg) Model {
	t.Helper()
	next, _ := m.Update(msg)
	return next.(Model)
}

// key is the message the terminal sends for a typed key.
func key(k string) tea.KeyMsg {
	return tea.KeyMsg{Type: tea.KeyRunes, Runes: []rune(k)}
}

// openScreen is the real TUI Model sized by a width x height WindowSizeMsg, its items
// loaded, set loaded as its sources, and the sources screen opened by S.
func openScreen(t *testing.T, width, height int, set []db.Source) Model {
	t.Helper()
	asciiProfile(t)
	t.Setenv("XDG_STATE_HOME", t.TempDir())
	m := NewModel()
	m = step(t, m, tea.WindowSizeMsg{Width: width, Height: height})
	m = step(t, m, itemsLoadedMsg{})
	m = step(t, m, sourcesLoadedMsg{sources: set})
	m = step(t, m, key("S"))
	if !m.sourceModal.IsVisible() {
		t.Fatal("S did not open the sources screen")
	}
	return m
}

// screenBox is the rows of the modal box drawn in view, its border rows included. The
// view's first line is the main view's header, never part of a box.
func screenBox(t *testing.T, view string) []string {
	t.Helper()
	lines := strings.Split(view, "\n")
	top, bottom := -1, -1
	for i := 1; i < len(lines); i++ {
		if top < 0 && strings.Contains(lines[i], "╭") {
			top = i
		}
		if strings.Contains(lines[i], "╰") {
			bottom = i
		}
	}
	if top < 0 || bottom < top {
		t.Fatalf("no box in view:\n%s", view)
	}
	return lines[top : bottom+1]
}

// boxText is the text of a box row inside its border and the box's two-column padding.
func boxText(row string) string {
	left, right := strings.Index(row, "│"), strings.LastIndex(row, "│")
	if left < 0 || right <= left {
		return ""
	}
	return strings.TrimRight(strings.TrimPrefix(row[left+len("│"):right], "  "), " ")
}

// selectedRowFault is "" when view holds exactly one ▸ row and it names name, or what
// it holds instead.
func selectedRowFault(view, name string) string {
	var marked []string
	for line := range strings.SplitSeq(view, "\n") {
		if strings.Contains(line, "▸") {
			marked = append(marked, strings.TrimSpace(line))
		}
	}
	if len(marked) == 1 && strings.Contains(marked[0], name) {
		return ""
	}
	return fmt.Sprintf("want one ▸ row naming %q, got %q", name, marked)
}

// evidenceUnderSelected is the box rows under the ▸ row that are indented as evidence.
func evidenceUnderSelected(t *testing.T, view string) []string {
	t.Helper()
	rows := screenBox(t, view)
	for i, row := range rows {
		if !strings.Contains(row, "▸") {
			continue
		}
		var evidence []string
		for _, below := range rows[i+1:] {
			text := boxText(below)
			if !strings.HasPrefix(text, "    ") || strings.TrimSpace(text) == "" {
				break
			}
			evidence = append(evidence, text)
		}
		return evidence
	}
	return nil
}

// compact drops every whitespace rune, so text compares across the rows it wraps onto.
func compact(s string) string {
	return strings.Join(strings.Fields(s), "")
}

// TestSourcesScreen_SelectedRowInViewAtEveryStep (C1): at the walk's 188x51 with the host
// set, every view rendered while j walks the cursor to the last source and k walks it
// back holds the ▸ marker on the selected source's row.
func TestSourcesScreen_SelectedRowInViewAtEveryStep(t *testing.T) {
	set := hostSet(t)
	m := openScreen(t, 188, 51, set)

	views, faults := 0, 0
	check := func(want int, how string) {
		views++
		if m.sourceModal.cursor != want {
			t.Fatalf("%s: cursor at %d, want %d", how, m.sourceModal.cursor, want)
		}
		view := m.View()
		if fault := selectedRowFault(view, set[want].Name); fault != "" {
			faults++
			if faults == 1 {
				t.Errorf("%s: %s; view:\n%s", how, fault, view)
			}
		}
	}
	for i := 1; i < len(set); i++ {
		m = step(t, m, key("j"))
		check(i, fmt.Sprintf("j to source %d", i))
	}
	for i := len(set) - 2; i >= 0; i-- {
		m = step(t, m, key("k"))
		check(i, fmt.Sprintf("k to source %d", i))
	}
	if views != 96 {
		t.Errorf("rendered %d views, want 96", views)
	}
	if faults > 0 {
		t.Errorf("%d of %d views lack the selected source's ▸ row", faults, views)
	}
}

// TestSourcesScreen_SelectedEvidenceWhollyInView (C2): each source with evidence, reached
// by j from the top and by k from the bottom, has every row of its evidence in the view,
// under its own row.
func TestSourcesScreen_SelectedEvidenceWhollyInView(t *testing.T) {
	set := hostSet(t)
	m := openScreen(t, 188, 51, set)
	hasEvidence := map[int]bool{}
	for _, i := range hostEvidence() {
		hasEvidence[i] = true
	}

	stops := 0
	check := func(how string) {
		i := m.sourceModal.cursor
		if !hasEvidence[i] {
			return
		}
		stops++
		view := m.View()
		if fault := selectedRowFault(view, set[i].Name); fault != "" {
			t.Errorf("%s to %s: %s", how, set[i].Name, fault)
			return
		}
		got := compact(strings.Join(evidenceUnderSelected(t, view), ""))
		if want := compact(sourceEvidence(set[i])); got != want {
			t.Errorf("%s to %s: evidence in view is\n%q\nwant\n%q\nview:\n%s", how, set[i].Name, got, want, view)
		}
	}
	for range len(set) - 1 {
		m = step(t, m, key("j"))
		check("j")
	}
	for range len(set) - 1 {
		m = step(t, m, key("k"))
		check("k")
	}
	if stops != 16 {
		t.Errorf("stopped at %d sources with evidence, want 16", stops)
	}
}

// TestSourcesScreen_BrokenSourceShowsItsWholeError (C3): with the cursor on NCC Group,
// whose stored error is the real 404 text, the view shows the 404 and carries the whole
// error under the source's row, with no ESC, CR or LF from the error.
func TestSourcesScreen_BrokenSourceShowsItsWholeError(t *testing.T) {
	set := hostSet(t)
	m := openScreen(t, 188, 51, set)
	for range hostBroken {
		m = step(t, m, key("j"))
	}
	view := m.View()
	if fault := selectedRowFault(view, "NCC Group"); fault != "" {
		t.Fatalf("%s; view:\n%s", fault, view)
	}
	if !strings.Contains(view, "404 Not Found") {
		t.Errorf("view lacks '404 Not Found':\n%s", view)
	}
	if strings.ContainsAny(view, "\x1b\r") {
		t.Errorf("view carries an ESC or CR byte: %q", view)
	}
	evidence := evidenceUnderSelected(t, view)
	if got, want := compact(strings.Join(evidence, "")), compact(real404Error(t)); !strings.Contains(got, want) {
		t.Errorf("evidence in view\n%q\nlacks the whole error\n%q", got, want)
	}
	// The error's LF would start a row at the text after it; flattened, the row
	// breaks where the width falls.
	for _, row := range evidence {
		if strings.HasPrefix(strings.TrimSpace(row), "For more information") {
			t.Errorf("the error's line break starts an evidence row: %q", evidence)
		}
	}
}

// TestSourcesScreen_MessagesKeepTheScreenShape (C4): with the cursor on a healthy source
// near the bottom, K's warning, a status reply longer than the box and an error longer
// than the box each leave the selected row in view, the status bar on one row and the
// screen as many rows tall as before the message.
func TestSourcesScreen_MessagesKeepTheScreenShape(t *testing.T) {
	const healthy = 46
	set := hostSet(t)
	if set[healthy].Health != "healthy" {
		t.Fatalf("source %d is %s, want healthy", healthy, set[healthy].Health)
	}
	m := openScreen(t, 188, 51, set)
	for range healthy {
		m = step(t, m, key("j"))
	}
	before := m.View()
	beforeRows, beforeBox := len(strings.Split(before, "\n")), len(screenBox(t, before))
	if fault := selectedRowFault(before, set[healthy].Name); fault != "" {
		t.Fatalf("before any message: %s", fault)
	}

	long := func(head string) string {
		return head + strings.Repeat(" the reply runs on past the box's right edge", 6)
	}
	check := func(how, shows, statusHead string) {
		view := m.View()
		if !strings.Contains(view, shows) {
			t.Errorf("%s: view lacks %q", how, shows)
		}
		if fault := selectedRowFault(view, set[healthy].Name); fault != "" {
			t.Errorf("%s: %s; view:\n%s", how, fault, view)
		}
		if rows := len(strings.Split(view, "\n")); rows != beforeRows {
			t.Errorf("%s: view is %d rows, %d before the message", how, rows, beforeRows)
		}
		box := screenBox(t, view)
		if len(box) != beforeBox {
			t.Errorf("%s: sources screen is %d rows, %d before the message; view:\n%s", how, len(box), beforeBox, view)
		}
		// The status bar is the row above the bottom padding row, and the row above it is blank.
		if len(box) < 4 || !strings.Contains(box[len(box)-3], statusHead) || strings.TrimSpace(boxText(box[len(box)-4])) != "" {
			t.Errorf("%s: status bar %q is not one row above the box's bottom padding; view:\n%s", how, statusHead, view)
		}
	}

	m = step(t, m, key("K"))
	check("K on a healthy source", "Nothing to keep: this source raises no warning", "[a]dd")

	status := long("Source reply:")
	m = step(t, m, operations.SourceOperationMsg{Success: true, Message: status})
	check("a long status reply", "Source reply:", "Source reply:")

	failure := long("Source failure:")
	m = step(t, m, operations.SourceOperationMsg{Success: false, Message: failure})
	check("a long error", "Source failure:", "Source reply:")
}

// TestSourcesScreen_NarrowTerminalDrawsEveryRowOnce (C6): at 60x30, every view rendered
// while j walks the host set fits 60 columns, keeps the screen its height, and holds the
// selected source's ▸ row; on NCC Group it shows the 404.
func TestSourcesScreen_NarrowTerminalDrawsEveryRowOnce(t *testing.T) {
	// The help screen's rule at 60x30: 50 columns (its minimum) by 22 rows, plus the border.
	const boxRows = 24
	set := hostSet(t)
	m := openScreen(t, 60, 30, set)

	faults := 0
	fault := func(format string, args ...any) {
		faults++
		if faults <= 3 {
			t.Errorf(format, args...)
		}
	}
	for i := 1; i < len(set); i++ {
		m = step(t, m, key("j"))
		view := m.View()
		for line := range strings.SplitSeq(view, "\n") {
			if w := ansi.StringWidth(line); w > 60 {
				fault("source %d: a line is %d columns wide: %q", i, w, line)
			}
		}
		if rows := len(screenBox(t, view)); rows != boxRows {
			fault("source %d: sources screen is %d rows, want %d; view:\n%s", i, rows, boxRows, view)
		}
		if f := selectedRowFault(view, set[i].Name); f != "" {
			fault("source %d: %s; view:\n%s", i, f, view)
		}
		if i == hostBroken && !strings.Contains(view, "404 Not Found") {
			fault("NCC Group: view lacks '404 Not Found':\n%s", view)
		}
	}
	if faults > 0 {
		t.Errorf("%d faults", faults)
	}
}

// TestSourcesScreen_WheelMovesTheCursor (C7): from the first source, each wheel-down moves
// the cursor one source down and each wheel-up one source up, and every view holds the
// selected source's ▸ row.
func TestSourcesScreen_WheelMovesTheCursor(t *testing.T) {
	set := hostSet(t)
	m := openScreen(t, 188, 51, set)
	wheel := func(button tea.MouseButton, want int) {
		m = step(t, m, tea.MouseMsg{Button: button, Action: tea.MouseActionPress})
		if m.sourceModal.cursor != want {
			t.Fatalf("after %s: cursor at %d, want %d", tea.MouseEvent{Button: button}, m.sourceModal.cursor, want)
		}
		if f := selectedRowFault(m.View(), set[want].Name); f != "" {
			t.Errorf("after %s to %d: %s", tea.MouseEvent{Button: button}, want, f)
		}
	}
	for i := 1; i <= 40; i++ {
		wheel(tea.MouseButtonWheelDown, i)
	}
	for i := 39; i >= 0; i-- {
		wheel(tea.MouseButtonWheelUp, i)
	}
}

// TestSourcesScreen_ReloadReachesTheOpenScreen: sources the daemon returns while the
// sources screen is open are the ones it draws, so a kept source shows its keep at once.
func TestSourcesScreen_ReloadReachesTheOpenScreen(t *testing.T) {
	set := hostSet(t)
	m := openScreen(t, 188, 51, set)
	keptAt := time.Date(2026, 11, 7, 0, 0, 0, 0, time.UTC)
	reloaded := slices.Clone(set)
	reloaded[0].KeptHealth, reloaded[0].KeptAt = "healthy", &keptAt

	m = step(t, m, sourcesLoadedMsg{sources: reloaded})
	if mark, evidence := markAndNext(t, m.View(), set[0].Name); mark != "□" || !strings.Contains(evidence, "kept (healthy)") {
		t.Errorf("after the reload %s draws mark %q and evidence %q, want □ and its keep", set[0].Name, mark, evidence)
	}
}

// TestSourcesScreen_ColumnsAlignWhateverTheName: a long name of multi-byte runes is cut
// to the name column by display width, so the row stays valid text and its type column
// lines up with a plain name's.
func TestSourcesScreen_ColumnsAlignWhateverTheName(t *testing.T) {
	asciiProfile(t)
	wide := strings.Repeat("€", 30)
	modal := NewSourceModal()
	modal.SetSize(188, 51)
	modal.Show()
	modal.LoadSources([]db.Source{
		{ID: "1", Name: wide, Type: "rss", Active: true, Health: "healthy"},
		{ID: "2", Name: "Plain", Type: "rss", Active: true, Health: "healthy"},
	})
	view := modal.View(CleanCyberTheme)
	if !utf8.ValidString(view) {
		t.Errorf("view is not valid UTF-8: %q", view)
	}
	column := map[string]int{}
	for line := range strings.SplitSeq(view, "\n") {
		for _, name := range []string{"€€€", "Plain"} {
			if strings.Contains(line, name) {
				column[name] = ansi.StringWidth(line[:strings.Index(line, "RSS")])
			}
		}
	}
	if column["€€€"] == 0 || column["€€€"] != column["Plain"] {
		t.Errorf("type column at %d for the multi-byte name, %d for the plain one; view:\n%s",
			column["€€€"], column["Plain"], view)
	}
}
