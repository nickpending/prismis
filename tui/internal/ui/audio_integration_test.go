package ui

import (
	"testing"

	"github.com/nickpending/prismis/internal/commands"
)

// TestAudioMsg_SetsStatusImmediately verifies status message appears before API call
func TestAudioMsg_SetsStatusImmediately(t *testing.T) {
	// INVARIANT: Status message must show immediately when :audio executed
	// BREAKS: User thinks command didn't work (user reported this bug!)

	// Create minimal model
	m := Model{
		statusMessage: "",
	}

	// Handle AudioMsg
	updatedModel, _ := m.Update(commands.AudioMsg{})
	updatedM := updatedModel.(Model)

	// Verify status message was set
	if updatedM.statusMessage == "" {
		t.Error("Status message not set when AudioMsg handled")
	}

	expectedMsg := "Generating audio briefing..."
	if updatedM.statusMessage != expectedMsg {
		t.Errorf("Expected status '%s', got '%s'", expectedMsg, updatedM.statusMessage)
	}
}

// TestAudioMsg_TriggersOperation verifies AudioMsg triggers API operation
func TestAudioMsg_TriggersOperation(t *testing.T) {
	// INVARIANT: AudioMsg must trigger operations.GenerateAudioBriefing()
	// BREAKS: Command does nothing if not wired correctly

	m := Model{}

	// Handle AudioMsg
	_, cmd := m.Update(commands.AudioMsg{})

	// Verify a command was returned (operation triggered)
	if cmd == nil {
		t.Error("AudioMsg did not return a tea.Cmd (operation not triggered)")
	}
}
