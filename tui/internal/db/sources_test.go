package db

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// sourcesDB builds a real SQLite database holding the daemon's own sources and content
// tables, taken from its schema.sql, and points the package's pool at it.
func sourcesDB(t *testing.T) *sql.DB {
	t.Helper()
	resetDBForTest(t)
	schema, err := os.ReadFile(filepath.Join("..", "..", "..", "daemon", "src", "prismis_daemon", "schema.sql"))
	if err != nil {
		t.Fatalf("read daemon schema: %v", err)
	}
	dbPath := filepath.Join(t.TempDir(), "sources.db")
	raw, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { raw.Close() })
	// schema.sql also creates a vec0 virtual table this driver cannot load, so take
	// the two statements out of the daemon's file rather than copying their columns.
	for _, table := range []string{"sources", "content"} {
		text := string(schema)
		start := strings.Index(text, "CREATE TABLE IF NOT EXISTS "+table+" (")
		if start < 0 {
			t.Fatalf("daemon schema.sql has no %s table", table)
		}
		end := strings.Index(text[start:], ");")
		if end < 0 {
			t.Fatalf("%s statement is not terminated", table)
		}
		if _, err := raw.Exec(text[start : start+end+2]); err != nil {
			t.Fatalf("apply daemon %s DDL: %v", table, err)
		}
	}
	original := dbPathFunc
	dbPathFunc = func() (string, error) { return dbPath, nil }
	t.Cleanup(func() {
		dbPathFunc = original
		resetDBForTest(t)
	})
	return raw
}

func TestGetSourcesWithCountsReadsTheNaughtyFlag(t *testing.T) {
	raw := sourcesDB(t)
	if _, err := raw.Exec(`INSERT INTO sources (id, url, type, name, naughty) VALUES
		('marked', 'https://a.example/feed', 'rss', 'Marked', 1),
		('plain', 'https://b.example/feed', 'rss', 'Plain', 0)`); err != nil {
		t.Fatalf("insert sources: %v", err)
	}

	sources, err := GetSourcesWithCounts()
	if err != nil {
		t.Fatalf("GetSourcesWithCounts: %v", err)
	}

	got := map[string]bool{}
	for _, s := range sources {
		got[s.ID] = s.Naughty
	}
	if len(got) != 2 || !got["marked"] || got["plain"] {
		t.Fatalf("want marked=true plain=false, got %v", got)
	}
}
