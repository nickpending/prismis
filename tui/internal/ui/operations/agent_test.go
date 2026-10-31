package operations

import (
	"os"
	"path/filepath"
	"testing"
)

// TestOpenInAgent_MissingCLIReportsItAndCreatesNoStatusFile verifies that with no
// prismis-cli on PATH the Cmd answers with the install line at once, and leaves no
// status file behind for a run that never starts.
func TestOpenInAgent_MissingCLIReportsItAndCreatesNoStatusFile(t *testing.T) {
	tmpDir := t.TempDir()
	t.Setenv("PATH", t.TempDir())
	t.Setenv("TMPDIR", tmpDir)

	msg := OpenInAgent("item-1", "")()

	done, ok := msg.(AgentDoneMsg)
	if !ok {
		t.Fatalf("expected AgentDoneMsg, got %T", msg)
	}
	if done.Line != "prismis-cli is not installed; `A` needs it" || done.Err != nil {
		t.Errorf("got %+v, want only the install line", done)
	}
	if left, _ := os.ReadDir(tmpDir); len(left) != 0 {
		t.Errorf("TMPDIR holds %d file(s), want none", len(left))
	}
}

// TestTakeStatusLine verifies the callback's read of the status file: the first line,
// trimmed, and the file removed; an absent file reads as no line.
func TestTakeStatusLine(t *testing.T) {
	path := filepath.Join(t.TempDir(), "status")
	if err := os.WriteFile(path, []byte("opened prismis-abcd1234 in pane w9:p9\nsecond\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	if got := takeStatusLine(path); got != "opened prismis-abcd1234 in pane w9:p9" {
		t.Errorf("first line = %q", got)
	}
	if _, err := os.Stat(path); !os.IsNotExist(err) {
		t.Errorf("status file still exists (stat err %v)", err)
	}
	if got := takeStatusLine(path); got != "" {
		t.Errorf("absent file read as %q, want empty", got)
	}
}
