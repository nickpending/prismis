package ui

import (
	"strings"
	"testing"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/x/ansi"
)

// TestSourcesScreenSizesLikeTheHelpScreen (C5): at 188x51 the sources screen and the help
// screen render the same width - the help screen's, 75 percent of 188 (141) plus its
// two border columns - and the sources screen's list fills more than 30 rows with the
// cursor on the first source.
func TestSourcesScreenSizesLikeTheHelpScreen(t *testing.T) {
	const want = 141 + 2
	sources := openScreen(t, 188, 51, hostSet(t))

	help := NewModel()
	help = step(t, help, tea.WindowSizeMsg{Width: 188, Height: 51})
	help = step(t, help, itemsLoadedMsg{})
	help = step(t, help, key("?"))
	if !help.helpModal.IsVisible() {
		t.Fatal("? did not open the help screen")
	}

	width := func(box []string) int { return ansi.StringWidth(strings.TrimSpace(box[0])) }
	sourcesBox, helpBox := screenBox(t, sources.View()), screenBox(t, help.View())
	if got := width(helpBox); got != want {
		t.Errorf("help screen is %d columns wide, want %d", got, want)
	}
	if got := width(sourcesBox); got != want {
		t.Errorf("sources screen is %d columns wide, want the help screen's %d", got, want)
	}

	// The list's rows run from below the title and its blank row to above the status
	// bar, its blank rows and the bottom padding row.
	listRows := 0
	for _, row := range sourcesBox[4 : len(sourcesBox)-3] {
		if strings.TrimSpace(boxText(row)) != "" {
			listRows++
		}
	}
	if listRows <= 30 {
		t.Errorf("sources screen's list draws %d rows, want more than 30; box:\n%s", listRows, strings.Join(sourcesBox, "\n"))
	}
}

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
