package ui

import (
	"fmt"
	"strconv"
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/charmbracelet/lipgloss"
	"github.com/muesli/termenv"
	"github.com/nickpending/prismis/internal/commands"
	"github.com/nickpending/prismis/internal/db"
)

// Every surface draws in the active theme, never in CleanCyberTheme by default (gh #20).
// Tests run without a terminal, where lipgloss strips colour, so each one forces a
// true-colour profile and reads the escape sequences a theme's colours produce.

func forceTrueColor(t *testing.T) {
	t.Helper()
	prev := lipgloss.ColorProfile()
	lipgloss.SetColorProfile(termenv.TrueColor)
	t.Cleanup(func() { lipgloss.SetColorProfile(prev) })
}

// foreground is the SGR sequence lipgloss writes for c as a text colour.
func foreground(t *testing.T, c lipgloss.Color) string {
	t.Helper()
	hex := strings.TrimPrefix(string(c), "#")
	v, err := strconv.ParseUint(hex, 16, 32)
	if err != nil || len(hex) != 6 {
		t.Fatalf("not a #rrggbb colour: %q", c)
	}
	return fmt.Sprintf("38;2;%d;%d;%dm", v>>16, (v>>8)&0xff, v&0xff)
}

// cyberColorsIn names every CleanCyberTheme text colour that appears in out.
func cyberColorsIn(t *testing.T, out string) []string {
	t.Helper()
	c := CleanCyberTheme
	named := map[string]lipgloss.Color{
		"Cyan": c.Cyan, "Purple": c.Purple, "VibrantPurple": c.VibrantPurple, "Green": c.Green,
		"Red": c.Red, "Orange": c.Orange, "Gray": c.Gray, "DarkGray": c.DarkGray, "White": c.White,
	}
	var found []string
	for name, col := range named {
		if strings.Contains(out, foreground(t, col)) {
			found = append(found, name)
		}
	}
	return found
}

func TestReaderBodyRedrawsInTheNewThemeOnThemeSwitch(t *testing.T) {
	forceTrueColor(t)
	items := []db.ContentItem{{ID: "1", Title: "Article", Content: "## Summary\n- a point\nBody text."}}
	m := Model{
		items: items, view: "reader", width: 100, height: 30,
		viewport: viewport.New(100, 30), theme: CleanCyberTheme,
	}
	m.updateReaderContent()
	if !strings.Contains(m.viewport.View(), foreground(t, CleanCyberTheme.Cyan)) {
		t.Fatalf("precondition: the body should start in CleanCyber cyan, got %q", m.viewport.View())
	}

	next, _ := m.Update(commands.ThemeMsg{})
	m = next.(Model)

	if m.theme.Name != MonokaiProTheme.Name {
		t.Fatalf("expected the switch to land on %s, got %s", MonokaiProTheme.Name, m.theme.Name)
	}
	body := m.viewport.View()
	if !strings.Contains(body, foreground(t, MonokaiProTheme.Cyan)) {
		t.Errorf("open article's headers and bullets should be in Monokai cyan after :theme, got %q", body)
	}
	if found := cyberColorsIn(t, body); len(found) > 0 {
		t.Errorf("open article still carries CleanCyber colours %v after :theme", found)
	}
}

func TestThemeSwitchKeepsAnEmptyReaderEmpty(t *testing.T) {
	m := Model{
		items: []db.ContentItem{{ID: "1", Title: "Gone", Content: "## Summary\nThe old article."}},
		view:  "reader", width: 100, height: 30,
		viewport: viewport.New(100, 30), theme: CleanCyberTheme,
	}
	m.updateReaderContent()
	m.items = nil
	m.updateReaderContent()

	next, _ := m.Update(commands.ThemeMsg{})
	body := next.(Model).viewport.View()
	if strings.Contains(body, "The old article.") || !strings.Contains(body, "No content selected") {
		t.Errorf("a theme switch with nothing selected should keep showing the empty reader, got %q", body)
	}
}

func TestReaderBodyOpensInTheActiveTheme(t *testing.T) {
	forceTrueColor(t)
	items := []db.ContentItem{{ID: "1", Title: "Article", Content: "## Summary\n- a point\n> a quote"}}
	m := Model{
		items: items, view: "reader", width: 100, height: 30,
		viewport: viewport.New(100, 30), theme: MonokaiProTheme,
	}
	m.updateReaderContent()

	body := m.viewport.View()
	if !strings.Contains(body, foreground(t, MonokaiProTheme.Cyan)) {
		t.Errorf("article opened under Monokai should use Monokai cyan, got %q", body)
	}
	if found := cyberColorsIn(t, body); len(found) > 0 {
		t.Errorf("article opened under Monokai carries CleanCyber colours %v", found)
	}
}

func TestSourceModalDrawsEveryModeInTheThemeItIsGiven(t *testing.T) {
	forceTrueColor(t)
	sources := []db.Source{
		{ID: "1", Name: "Healthy", Type: "rss", Active: true},
		{ID: "2", Name: "Paused", Type: "reddit", Active: false},
		{ID: "3", Name: "Failing", Type: "youtube", Active: true, ErrorCount: 5},
	}
	for _, mode := range []string{"list", "add", "edit", "confirm_remove"} {
		t.Run(mode, func(t *testing.T) {
			modal := NewSourceModal()
			modal.LoadSources(sources)
			modal.visible = true
			modal.mode = mode
			modal.UpdateContent()

			view := modal.View(MonokaiProTheme)
			if found := cyberColorsIn(t, view); len(found) > 0 {
				t.Errorf("%s mode under Monokai carries CleanCyber colours %v", mode, found)
			}
			if mode == "list" && !strings.Contains(view, foreground(t, MonokaiProTheme.Green)) {
				t.Errorf("healthy source dot should be Monokai green, got %q", view)
			}
		})
	}
}

func TestLoadingAndErrorLinesUseTheActiveTheme(t *testing.T) {
	forceTrueColor(t)
	loading := Model{loading: true, theme: MonokaiProTheme}
	if out := loading.View(); !strings.Contains(out, foreground(t, MonokaiProTheme.Cyan)) {
		t.Errorf("loading line should be Monokai cyan, got %q", out)
	}
	failed := Model{err: fmt.Errorf("daemon unreachable"), theme: MonokaiProTheme}
	if out := failed.View(); !strings.Contains(out, foreground(t, MonokaiProTheme.Red)) {
		t.Errorf("error line should be Monokai red, got %q", out)
	}
}
