package commands

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// TestExtractCommand_CreatesExtractMsg verifies :extract command produces ExtractMsg.
// BREAKS: If cmdExtract returns the wrong message type, the model.go Update() router
// never matches the case branch and the extraction silently does nothing (SC-7).
func TestExtractCommand_CreatesExtractMsg(t *testing.T) {
	cmd := cmdExtract([]string{})
	msg := cmd()

	_, ok := msg.(ExtractMsg)
	if !ok {
		t.Errorf("Expected ExtractMsg, got %T", msg)
	}
}

// TestExtractCommand_IgnoresArguments verifies :extract works with no or extra arguments.
// BREAKS: If the handler panics on args or requires args, :extract becomes unusable.
func TestExtractCommand_IgnoresArguments(t *testing.T) {
	cmd := cmdExtract([]string{})
	msg := cmd()
	if msg == nil {
		t.Error("Command returned nil message")
	}

	cmd2 := cmdExtract([]string{"unexpected", "args"})
	msg2 := cmd2()
	if msg2 == nil {
		t.Error("Command with args returned nil message")
	}
}

// TestExtractMsg_IsMessageType verifies ExtractMsg satisfies tea.Msg interface.
// BREAKS: Runtime panic if ExtractMsg is not a valid tea.Msg.
func TestExtractMsg_IsMessageType(t *testing.T) {
	// The assignment is the assertion: it does not compile unless ExtractMsg
	// satisfies tea.Msg. Round-trip it back to confirm the concrete type.
	var msg tea.Msg = ExtractMsg{}
	if _, ok := msg.(ExtractMsg); !ok {
		t.Errorf("tea.Msg holding ExtractMsg did not type-assert back to ExtractMsg")
	}
}
