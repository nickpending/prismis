package db

import (
	"database/sql"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// driftDB builds a real SQLite database from the daemon's own schema.sql and points the
// package's pool at it.
func driftDB(t *testing.T) *sql.DB {
	t.Helper()
	resetDBForTest(t)
	schema, err := os.ReadFile(filepath.Join("..", "..", "..", "daemon", "src", "prismis_daemon", "schema.sql"))
	if err != nil {
		t.Fatalf("read daemon schema: %v", err)
	}
	dbPath := filepath.Join(t.TempDir(), "drift.db")
	raw, err := sql.Open("sqlite3", dbPath)
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { raw.Close() })
	// schema.sql also creates a vec0 virtual table this driver cannot load, so take the
	// drift_checks statement out of the daemon's file rather than copying its columns.
	start := strings.Index(string(schema), "CREATE TABLE IF NOT EXISTS drift_checks")
	if start < 0 {
		t.Fatal("daemon schema.sql has no drift_checks table")
	}
	end := strings.Index(string(schema)[start:], ");")
	if end < 0 {
		t.Fatal("drift_checks statement is not terminated")
	}
	if _, err := raw.Exec(string(schema)[start : start+end+2]); err != nil {
		t.Fatalf("apply daemon drift_checks DDL: %v", err)
	}
	original := dbPathFunc
	dbPathFunc = func() (string, error) { return dbPath, nil }
	t.Cleanup(func() {
		dbPathFunc = original
		resetDBForTest(t)
	})
	return raw
}

func insertDrift(t *testing.T, raw *sql.DB, flagged int, share, baseline any) {
	t.Helper()
	_, err := raw.Exec(`INSERT INTO drift_checks
		(checked_at, window_items, window_null, share, baseline, baseline_windows, deviation, threshold, flagged, reason)
		VALUES ('2026-10-16T00:00:00+00:00', 40, 25, ?, ?, 7, NULL, 0.2, ?, NULL)`,
		share, baseline, flagged)
	if err != nil {
		t.Fatalf("insert drift check: %v", err)
	}
}

func TestLatestDriftCheckReturnsHighestIDRow(t *testing.T) {
	raw := driftDB(t)
	insertDrift(t, raw, 1, 0.62, 0.54)
	insertDrift(t, raw, 0, 0.5, 0.54)
	insertDrift(t, raw, 1, nil, nil)

	check, err := LatestDriftCheck()
	if err != nil {
		t.Fatalf("LatestDriftCheck: %v", err)
	}
	if check == nil || !check.Flagged {
		t.Fatalf("want the newest (flagged) row, got %+v", check)
	}
	if check.Share != nil || check.Baseline != nil {
		t.Errorf("thin-window row must carry nil share and baseline, got %v / %v", check.Share, check.Baseline)
	}

	insertDrift(t, raw, 1, 0.62, 0.54)
	check, err = LatestDriftCheck()
	if err != nil || check == nil {
		t.Fatalf("LatestDriftCheck: %v, %+v", err, check)
	}
	if check.Share == nil || *check.Share != 0.62 || check.Baseline == nil || *check.Baseline != 0.54 {
		t.Errorf("want share 0.62 baseline 0.54, got %v / %v", check.Share, check.Baseline)
	}
}

func TestLatestDriftCheckEmptyTableIsNilWithoutError(t *testing.T) {
	driftDB(t)
	check, err := LatestDriftCheck()
	if err != nil {
		t.Fatalf("an empty table must not be an error: %v", err)
	}
	if check != nil {
		t.Fatalf("want nil check, got %+v", check)
	}
}

func TestLatestDriftCheckQueryErrorIsAnError(t *testing.T) {
	raw := driftDB(t)
	if _, err := raw.Exec("DROP TABLE drift_checks"); err != nil {
		t.Fatalf("drop: %v", err)
	}
	check, err := LatestDriftCheck()
	if err == nil {
		t.Fatalf("a missing table must be an error, got check %+v", check)
	}
	if check != nil {
		t.Errorf("an error must come with a nil check, got %+v", check)
	}
}
