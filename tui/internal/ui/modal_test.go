package ui

import (
	"strings"
	"testing"
)

// TestOverlayModal_CentersModalOverBackground proves overlayModal's centering math on a
// synthetic background+modal pair: the modal view must appear at the computed row/column
// and the background line outside the modal must stay dimmed (blanked), while the header
// line (index 0) stays untouched - this is the shared compositing extracted from
// Modal.ViewWithOverlay and SourceModal.ViewWithOverlay (dedup-triage.md cluster 6).
func TestOverlayModal_CentersModalOverBackground(t *testing.T) {
	background := strings.Join([]string{
		"HEADER-UNCHANGED",
		"background line 1",
		"background line 2",
		"background line 3",
		"background line 4",
		"background line 5",
	}, "\n")
	modalView := "MODAL-LINE"

	termWidth, termHeight := 20, 6
	modalWidth := len("MODAL-LINE")
	minStartY := 0

	got := overlayModal(background, modalView, termWidth, termHeight, modalWidth, minStartY)
	lines := strings.Split(got, "\n")

	if lines[0] != "HEADER-UNCHANGED" {
		t.Errorf("expected header line untouched, got: %q", lines[0])
	}

	wantStartY := modalMax(minStartY, (termHeight-1)/2) // modal is 1 line tall
	wantStartX := modalMax(0, (termWidth-modalWidth)/2)
	wantLine := strings.Repeat(" ", wantStartX) + modalView

	if lines[wantStartY] != wantLine {
		t.Errorf("expected modal line at row %d to be %q, got %q", wantStartY, wantLine, lines[wantStartY])
	}

	// Every other body line (not the header, not the modal row) must be blanked
	// (dimmed) to termWidth spaces, proving the background dimming still runs.
	for i, line := range lines {
		if i == 0 || i == wantStartY {
			continue
		}
		if line != strings.Repeat(" ", termWidth) {
			t.Errorf("expected background line %d dimmed to %d spaces, got %q", i, termWidth, line)
		}
	}
}

// TestOverlayModal_RespectsMinStartY proves the minStartY floor (SourceModal passes 1 to
// avoid overlapping the header; Modal passes 0) is a parameter of the shared function, not
// a constant baked into one caller - dedup-triage.md flags this as the behavior difference
// that must survive consolidation.
func TestOverlayModal_RespectsMinStartY(t *testing.T) {
	background := strings.Join([]string{"HEADER", "line1", "line2"}, "\n")
	modalView := "M"

	// With a tall terminal, (termHeight-modalHeight)/2 would place the modal at row 0;
	// minStartY=1 must push it down to row 1 instead.
	got := overlayModal(background, modalView, 10, 3, 1, 1)
	lines := strings.Split(got, "\n")

	if strings.TrimRight(lines[0], " ") == modalView {
		t.Errorf("expected modal not placed on header row 0, got: %q", lines[0])
	}
	if !strings.Contains(lines[1], modalView) {
		t.Errorf("expected modal placed on row 1 (the minStartY floor), got: %q", lines[1])
	}
}
