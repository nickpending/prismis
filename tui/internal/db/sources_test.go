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
	return sourcesDBFrom(t, filepath.Join("..", "..", "..", "daemon", "src", "prismis_daemon", "schema.sql"))
}

// sourcesDBBeforeHealth is sourcesDB at the daemon's schema from before the source
// health columns: what a TUI reads when it is upgraded before its daemon restarts.
func sourcesDBBeforeHealth(t *testing.T) *sql.DB {
	t.Helper()
	return sourcesDBFrom(t, filepath.Join("..", "..", "..", "daemon", "tests", "fixtures", "schema_before_source_health.sql"))
}

// sourcesDBFrom builds sourcesDB from the schema file at schemaPath.
func sourcesDBFrom(t *testing.T, schemaPath string) *sql.DB {
	t.Helper()
	resetDBForTest(t)
	schema, err := os.ReadFile(schemaPath)
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

// TestGetSourcesWithCountsReadsTheStoredHealth: every health field the daemon stores
// reaches Source as stored, and SourcesNeedingAttention counts only flagged rows.
func TestGetSourcesWithCountsReadsTheStoredHealth(t *testing.T) {
	raw := sourcesDB(t)
	if _, err := raw.Exec(`INSERT INTO sources
		(id, url, type, name, error_count, last_error, health, attention, entries_served,
		 newest_item_at, next_fetch_at, kept_health, kept_at, created_at) VALUES
		('dead', 'https://a.example/feed', 'rss', 'Dead', 5, 'HTTP 404', 'broken', 1, NULL,
		 '2026-07-01T00:00:00+00:00', '2026-11-08T10:00:00+00:00', NULL, NULL, '2026-01-01T00:00:00+00:00'),
		('hollow', 'https://b.example/feed', 'rss', 'Hollow', 0, NULL, 'empty', 1, 0,
		 NULL, NULL, NULL, NULL, '2026-01-02T00:00:00+00:00'),
		('quiet', 'https://c.example/feed', 'rss', 'Quiet', 0, NULL, 'dormant', 0, 8,
		 '2026-01-03T00:00:00+00:00', NULL, 'dormant', '2026-11-01T00:00:00+00:00', '2026-01-03T00:00:00+00:00')`); err != nil {
		t.Fatalf("insert sources: %v", err)
	}

	sources, err := GetSourcesWithCounts()
	if err != nil {
		t.Fatalf("GetSourcesWithCounts: %v", err)
	}
	byID := map[string]Source{}
	for _, s := range sources {
		byID[s.ID] = s
	}
	dead, hollow, quiet := byID["dead"], byID["hollow"], byID["quiet"]
	if dead.Health != "broken" || !dead.Attention || dead.LastError != "HTTP 404" || dead.ErrorCount != 5 ||
		dead.NextFetchAt == nil || dead.NextFetchAt.Format("2006-01-02T15:04") != "2026-11-08T10:00" ||
		dead.EntriesServed != nil {
		t.Errorf("broken source read wrong: %+v", dead)
	}
	if hollow.Health != "empty" || hollow.EntriesServed == nil || *hollow.EntriesServed != 0 {
		t.Errorf("empty source read wrong: %+v", hollow)
	}
	if !quiet.Kept() || quiet.Attention || quiet.KeptAt == nil || quiet.NewestItemAt == nil ||
		quiet.NewestItemAt.Format("2006-01-02") != "2026-01-03" {
		t.Errorf("kept dormant source read wrong: %+v", quiet)
	}

	counts, err := SourcesNeedingAttention()
	if err != nil {
		t.Fatalf("SourcesNeedingAttention: %v", err)
	}
	if *counts != (SourceAttention{Broken: 1, Empty: 1}) {
		t.Errorf("want 1 broken and 1 empty, got %+v", *counts)
	}
}

// TestADatabaseBeforeHealthStillReads: a TUI upgraded before its daemon restarted reads
// a database without the health columns. The sources screen still lists every source,
// with no health, and the main screen's attention read finds nothing, without failing.
func TestADatabaseBeforeHealthStillReads(t *testing.T) {
	raw := sourcesDBBeforeHealth(t)
	if _, err := raw.Exec(`INSERT INTO sources (id, url, type, name, error_count) VALUES
		('one', 'https://a.example/feed', 'rss', 'One', 0),
		('two', 'https://b.example/feed', 'rss', 'Two', 7)`); err != nil {
		t.Fatalf("insert sources: %v", err)
	}

	sources, err := GetSourcesWithCounts()
	if err != nil {
		t.Fatalf("GetSourcesWithCounts on a pre-health database: %v", err)
	}
	if len(sources) != 2 {
		t.Fatalf("want both sources, got %+v", sources)
	}
	for _, s := range sources {
		if s.Health != "" || s.Attention {
			t.Errorf("a pre-health row must read unclassified: %+v", s)
		}
	}

	counts, err := SourcesNeedingAttention()
	if err != nil {
		t.Fatalf("SourcesNeedingAttention on a pre-health database: %v", err)
	}
	if counts.Any() {
		t.Errorf("a pre-health database has no flagged source, got %+v", *counts)
	}
}
