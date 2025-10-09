package commands

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// TestAudioCommand_CreatesAudioMsg verifies :audio command produces AudioMsg
func TestAudioCommand_CreatesAudioMsg(t *testing.T) {
	// INVARIANT: :audio command must produce AudioMsg
	// BREAKS: Command doesn't trigger API call if broken

	cmd := cmdAudio([]string{})
	msg := cmd()

	// Verify it returns AudioMsg type
	_, ok := msg.(AudioMsg)
	if !ok {
		t.Errorf("Expected AudioMsg, got %T", msg)
	}
}

// TestAudioCommand_NoArguments verifies :audio doesn't require arguments
func TestAudioCommand_NoArguments(t *testing.T) {
	// INVARIANT: :audio command works without arguments
	// BREAKS: User confusion if args required

	// Should work with no arguments
	cmd := cmdAudio([]string{})
	msg := cmd()

	if msg == nil {
		t.Error("Command returned nil message")
	}

	// Should ignore extra arguments gracefully
	cmd2 := cmdAudio([]string{"unexpected", "args"})
	msg2 := cmd2()

	if msg2 == nil {
		t.Error("Command with args returned nil message")
	}
}

// TestAudioMsg_IsMessageType verifies AudioMsg implements tea.Msg
func TestAudioMsg_IsMessageType(t *testing.T) {
	// INVARIANT: AudioMsg must be valid tea.Msg for Bubbletea
	// BREAKS: Runtime panic if not proper message type

	var msg tea.Msg = AudioMsg{}

	// If this compiles and doesn't panic, AudioMsg is valid
	if msg == nil {
		t.Error("AudioMsg should not be nil")
	}
}
