package ui

import (
	"strings"
	"testing"
)

// TestHelpModal_ListsKindControls verifies the FILTERS & SORTING section documents both
// new kind-filter controls (the K hotkey and the :kind command) - the only in-app
// discovery path for either, since there's no completion for the :kind command's
// argument. A user who never learns "K" or ":kind" exist can never use them.
func TestHelpModal_ListsKindControls(t *testing.T) {
	modal := NewHelpModal()
	modal.Show()
	modal.SetSize(120, 40) // wide enough for the two-column layout

	output := modal.View(CleanCyberTheme)

	if !strings.Contains(output, "K") {
		t.Errorf("Expected help modal to mention the 'K' kind-filter hotkey. Got: %s", output)
	}
	if !strings.Contains(output, "Cycle kind filter") {
		t.Errorf("Expected help modal to describe the 'K' hotkey. Got: %s", output)
	}
	if !strings.Contains(output, ":kind") {
		t.Errorf("Expected help modal to mention the ':kind' command. Got: %s", output)
	}
}

// TestHelpModal_PriorityRow verifies 0, 1, 2, 3 and 4 share one key row, so the
// unprioritized view is discoverable beside the floors it complements.
func TestHelpModal_PriorityRow(t *testing.T) {
	modal := NewHelpModal()
	modal.Show()
	modal.SetSize(120, 40)

	output := modal.View(CleanCyberTheme)

	if !strings.Contains(output, "0/1/2/3/4") {
		t.Errorf("Expected help modal to list 0/1/2/3/4 in one row. Got: %s", output)
	}
}
