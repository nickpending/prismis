package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/db"
)

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
	// Create a new source modal with one existing source - this mirrors the real
	// trigger for errorMsg (a pause/remove/edit failing on an existing source,
	// model.go's operations.SourceOperationMsg handler), not an empty source list.
	// The source modal's viewport is a fixed 5 rows tall (SetSize hard-codes a
	// small modal size); a scenario with zero sources plus the "No sources
	// configured" filler pushes the error line past that window, so it would
	// never actually be visible - this test must not assert on that case.
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
