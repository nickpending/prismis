package commands

import (
	"testing"

	tea "github.com/charmbracelet/bubbletea"
)

// INVARIANT: :context suggest creates ContextSuggestMsg
// BREAKS: Command dispatch if message type wrong
func TestContextSuggestCommand(t *testing.T) {
	cmd := cmdContext([]string{"suggest"})
	msg := cmd()

	_, ok := msg.(ContextSuggestMsg)
	if !ok {
		t.Errorf("Expected ContextSuggestMsg, got %T", msg)
	}
}

// INVARIANT: :context edit creates ContextEditMsg
// BREAKS: Editor won't open if message type wrong
func TestContextEditCommand(t *testing.T) {
	cmd := cmdContext([]string{"edit"})
	msg := cmd()

	_, ok := msg.(ContextEditMsg)
	if !ok {
		t.Errorf("Expected ContextEditMsg, got %T", msg)
	}
}

// INVARIANT: :context review creates ContextReviewMsg
// BREAKS: Review won't display if message type wrong
func TestContextReviewCommand(t *testing.T) {
	cmd := cmdContext([]string{"review"})
	msg := cmd()

	_, ok := msg.(ContextReviewMsg)
	if !ok {
		t.Errorf("Expected ContextReviewMsg, got %T", msg)
	}
}

// INVARIANT: :context without subcommand returns error
// BREAKS: User gets confused if no error shown
func TestContextCommandNoSubcommand(t *testing.T) {
	cmd := cmdContext([]string{})
	msg := cmd()

	errMsg, ok := msg.(ErrorMsg)
	if !ok {
		t.Errorf("Expected ErrorMsg for missing subcommand, got %T", msg)
		return
	}

	if len(errMsg.Message) == 0 {
		t.Error("Error message should not be empty")
	}
}

// INVARIANT: Invalid subcommand returns error
// BREAKS: User gets confusing behavior if invalid command silently ignored
func TestContextCommandInvalidSubcommand(t *testing.T) {
	cmd := cmdContext([]string{"invalid"})
	msg := cmd()

	errMsg, ok := msg.(ErrorMsg)
	if !ok {
		t.Errorf("Expected ErrorMsg for invalid subcommand, got %T", msg)
		return
	}

	if len(errMsg.Message) == 0 {
		t.Error("Error message should not be empty")
	}
}

// INVARIANT: All context messages implement tea.Msg
// BREAKS: Bubble Tea crashes if messages don't implement interface
func TestContextMessagesImplementTeaMsg(t *testing.T) {
	messages := []tea.Msg{
		ContextSuggestMsg{},
		ContextEditMsg{},
		ContextReviewMsg{},
	}

	for _, msg := range messages {
		// If this compiles, messages implement tea.Msg
		_ = msg
	}
}
