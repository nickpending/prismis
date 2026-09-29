package commands

import "testing"

// TestParseAgeArg_TableDriven exercises parseAgeArg's error-prefix behavior,
// the gap the triage flagged for cluster 14 (three call sites collapsing into
// one function with no test).
func TestParseAgeArg_TableDriven(t *testing.T) {
	seven := 7
	fourteen := 14
	thirty := 30

	tests := []struct {
		name     string
		cmdName  string
		args     []string
		wantDays *int
		wantErr  string
	}{
		{name: "no args returns nil days and no error", cmdName: "prune", args: nil, wantDays: nil, wantErr: ""},
		{name: "days unit", cmdName: "prune", args: []string{"7d"}, wantDays: &seven, wantErr: ""},
		{name: "weeks unit converts to days", cmdName: "prune", args: []string{"2w"}, wantDays: &fourteen, wantErr: ""},
		{name: "months unit converts to days", cmdName: "prune", args: []string{"1m"}, wantDays: &thirty, wantErr: ""},
		{
			name:     "invalid age carries the unprioritized prefix",
			cmdName:  "unprioritized",
			args:     []string{"bogus"},
			wantDays: nil,
			wantErr:  "unprioritized: invalid age filter 'bogus' (use format like 7d, 2w, 1m)",
		},
		{
			name:     "invalid age carries the prune prefix",
			cmdName:  "prune",
			args:     []string{"xx"},
			wantDays: nil,
			wantErr:  "prune: invalid age filter 'xx' (use format like 7d, 2w, 1m)",
		},
		{
			name:     "invalid age carries the prune! prefix",
			cmdName:  "prune!",
			args:     []string{"xx"},
			wantDays: nil,
			wantErr:  "prune!: invalid age filter 'xx' (use format like 7d, 2w, 1m)",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			gotDays, gotErr := parseAgeArg(tt.cmdName, tt.args)

			if tt.wantErr == "" {
				if gotErr != nil {
					t.Fatalf("expected no error, got %q", gotErr.Message)
				}
			} else {
				if gotErr == nil {
					t.Fatalf("expected error %q, got nil", tt.wantErr)
				}
				if gotErr.Message != tt.wantErr {
					t.Errorf("expected error %q, got %q", tt.wantErr, gotErr.Message)
				}
			}

			if tt.wantDays == nil {
				if gotDays != nil {
					t.Errorf("expected nil days, got %d", *gotDays)
				}
			} else {
				if gotDays == nil {
					t.Fatalf("expected days %d, got nil", *tt.wantDays)
				}
				if *gotDays != *tt.wantDays {
					t.Errorf("expected days %d, got %d", *tt.wantDays, *gotDays)
				}
			}
		})
	}
}

// TestCmdUnprioritized_InvalidAge verifies the :unprioritized command surfaces
// parseAgeArg's error through its own tea.Cmd, prefixed with "unprioritized".
func TestCmdUnprioritized_InvalidAge(t *testing.T) {
	cmd := cmdUnprioritized([]string{"nope"})
	msg := cmd()

	errMsg, ok := msg.(ErrorMsg)
	if !ok {
		t.Fatalf("expected ErrorMsg, got %T", msg)
	}
	want := "unprioritized: invalid age filter 'nope' (use format like 7d, 2w, 1m)"
	if errMsg.Message != want {
		t.Errorf("expected message %q, got %q", want, errMsg.Message)
	}
}

// TestCmdPrune_ValidAge verifies :prune with a valid age filter produces a
// PruneMsg carrying the parsed day count, not an ErrorMsg.
func TestCmdPrune_ValidAge(t *testing.T) {
	cmd := cmdPrune([]string{"3d"})
	msg := cmd()

	pruneMsg, ok := msg.(PruneMsg)
	if !ok {
		t.Fatalf("expected PruneMsg, got %T", msg)
	}
	if pruneMsg.Days == nil || *pruneMsg.Days != 3 {
		t.Errorf("expected Days=3, got %v", pruneMsg.Days)
	}
	if pruneMsg.Force {
		t.Error("expected Force=false for :prune")
	}
	if pruneMsg.CountOnly {
		t.Error("expected CountOnly=false for :prune")
	}
}

// TestCmdPruneForce_InvalidAge verifies :prune! surfaces its own "prune!"
// prefix on an invalid age filter, distinct from :prune's "prune" prefix.
func TestCmdPruneForce_InvalidAge(t *testing.T) {
	cmd := cmdPruneForce([]string{"garbage"})
	msg := cmd()

	errMsg, ok := msg.(ErrorMsg)
	if !ok {
		t.Fatalf("expected ErrorMsg, got %T", msg)
	}
	want := "prune!: invalid age filter 'garbage' (use format like 7d, 2w, 1m)"
	if errMsg.Message != want {
		t.Errorf("expected message %q, got %q", want, errMsg.Message)
	}
}
