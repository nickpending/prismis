package ui

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path/filepath"
)

// viewFileName is the persisted view's file name under $XDG_STATE_HOME/prismis.
const viewFileName = "tui-view.json"

// sourceTypes are the values the source type filter cycles through.
var sourceTypes = []string{"all", "rss", "reddit", "youtube", "file"}

// priorityStates are the values Model.priority takes. "high", "medium" and "low" are
// floors: each shows its own priority and everything above it.
var priorityStates = []string{"high", "medium", "low", "unprioritized", "favorites"}

// priorityRank orders the priorities a floor compares; an unprioritized item ranks 0
// and sits below every floor.
func priorityRank(priority string) int {
	switch priority {
	case "high":
		return 3
	case "medium":
		return 2
	case "low":
		return 1
	}
	return 0
}

// viewState is the part of the model the operator builds with the view keys and expects
// to find again at the next launch.
type viewState struct {
	Priority   string
	ShowAll    bool
	Archived   bool
	Upvoted    bool
	SortNewest bool
	SourceType string
	Kind       string
}

// viewFile is viewState as stored. Fields are pointers so a field the file omits is told
// apart from one it sets to its zero value.
type viewFile struct {
	Priority   *string `json:"priority"`
	ShowAll    *bool   `json:"show_all"`
	Archived   *bool   `json:"archived"`
	Upvoted    *bool   `json:"upvoted"`
	Sort       *string `json:"sort"`
	SourceType *string `json:"source_type"`
	Kind       *string `json:"kind"`
}

// defaultViewState is the view a first launch, and R, start from.
func defaultViewState() viewState {
	return viewState{
		Priority:   "low",
		SortNewest: true,
		SourceType: "all",
		Kind:       "all",
	}
}

// viewState reads the seven persisted fields off the model.
func (m Model) viewState() viewState {
	return viewState{
		Priority:   m.priority,
		ShowAll:    m.showAll,
		Archived:   m.showArchived,
		Upvoted:    m.showInteresting,
		SortNewest: m.sortNewest,
		SourceType: m.filterType,
		Kind:       m.kindFilter,
	}
}

// applyViewState writes the seven persisted fields onto the model and derives the
// unprioritized flag from the priority, so a restored 0 view shows its items.
func (m *Model) applyViewState(v viewState) {
	m.priority = v.Priority
	m.showAll = v.ShowAll
	m.showArchived = v.Archived
	m.showInteresting = v.Upvoted
	m.sortNewest = v.SortNewest
	m.filterType = v.SourceType
	m.kindFilter = v.Kind
	m.showUnprioritized = v.Priority == "unprioritized"
}

func containsString(list []string, s string) bool {
	for _, v := range list {
		if v == s {
			return true
		}
	}
	return false
}

// viewStatePath resolves $XDG_STATE_HOME/prismis/tui-view.json, defaulting to
// ~/.local/state, the way getDefaultDBPath resolves the data directory.
func viewStatePath() (string, error) {
	stateHome := os.Getenv("XDG_STATE_HOME")
	if stateHome == "" {
		homeDir, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("failed to get home directory: %w", err)
		}
		stateHome = filepath.Join(homeDir, ".local", "state")
	}
	return filepath.Join(stateHome, "prismis", viewFileName), nil
}

// loadViewState reads the saved view. A missing file is a first launch: defaults and a
// nil error. A file that cannot be read or parsed gives defaults and the error. A field
// with an unknown value falls back to its default; the kind is not judged here, since
// the data it must exist in has not loaded yet.
func loadViewState(path string) (viewState, error) {
	v := defaultViewState()
	data, err := os.ReadFile(path)
	if errors.Is(err, fs.ErrNotExist) {
		return v, nil
	}
	if err != nil {
		return defaultViewState(), err
	}
	var f viewFile
	if err := json.Unmarshal(data, &f); err != nil {
		return defaultViewState(), fmt.Errorf("invalid JSON: %w", err)
	}
	if f.Priority != nil && containsString(priorityStates, *f.Priority) {
		v.Priority = *f.Priority
	}
	if f.ShowAll != nil {
		v.ShowAll = *f.ShowAll
	}
	if f.Archived != nil {
		v.Archived = *f.Archived
	}
	if f.Upvoted != nil {
		v.Upvoted = *f.Upvoted
	}
	if f.Sort != nil {
		switch *f.Sort {
		case "newest":
			v.SortNewest = true
		case "oldest":
			v.SortNewest = false
		}
	}
	if f.SourceType != nil && containsString(sourceTypes, *f.SourceType) {
		v.SourceType = *f.SourceType
	}
	if f.Kind != nil && *f.Kind != "" {
		v.Kind = *f.Kind
	}
	return v, nil
}

// saveViewState writes the view to a temporary file in the same directory and renames
// it over path, so a crash mid-write never leaves a torn file and the file is never
// opened for writing in place.
func saveViewState(path string, v viewState) error {
	sort := "oldest"
	if v.SortNewest {
		sort = "newest"
	}
	data, err := json.MarshalIndent(viewFile{
		Priority:   &v.Priority,
		ShowAll:    &v.ShowAll,
		Archived:   &v.Archived,
		Upvoted:    &v.Upvoted,
		Sort:       &sort,
		SourceType: &v.SourceType,
		Kind:       &v.Kind,
	}, "", "  ")
	if err != nil {
		return err
	}
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(dir, viewFileName+".*.tmp")
	if err != nil {
		return err
	}
	tmpName := tmp.Name()
	if _, err := tmp.Write(append(data, '\n')); err != nil {
		tmp.Close()
		os.Remove(tmpName)
		return err
	}
	if err := tmp.Close(); err != nil {
		os.Remove(tmpName)
		return err
	}
	if err := os.Rename(tmpName, path); err != nil {
		os.Remove(tmpName)
		return err
	}
	return nil
}

// loadInitialView applies the saved view to a freshly built model and arms saving. A
// file that exists but cannot be used leaves a status message naming it.
func (m *Model) loadInitialView() {
	path, err := viewStatePath()
	if err != nil {
		m.statusMessage = fmt.Sprintf("View not restored: %v", err)
		return
	}
	v, err := loadViewState(path)
	if err != nil {
		m.statusMessage = fmt.Sprintf("Could not read %s: %v (using defaults)", path, err)
	}
	m.applyViewState(v)
	m.viewPath = path
	m.lastSavedView = v
}

// saveViewIfChanged persists the view when it differs from the last one written. A model
// not built by newModel has no path and never writes. A failed save is reported in the
// status line, once per change, and never interrupts the session.
func (m *Model) saveViewIfChanged() {
	if m.viewPath == "" {
		return
	}
	v := m.viewState()
	if v == m.lastSavedView {
		return
	}
	m.lastSavedView = v
	if err := saveViewState(m.viewPath, v); err != nil {
		m.statusMessage = fmt.Sprintf("Could not save %s: %v", m.viewPath, err)
	}
}
