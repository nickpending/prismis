package operations

import (
	"fmt"
	"os"
	"os/exec"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
)

// MissingAgentCLILine is the outcome when prismis-cli is not on PATH.
const MissingAgentCLILine = "prismis-cli is not installed; `A` needs it"

// AgentDoneMsg carries the outcome of `prismis-cli open`: the one line it wrote to its
// status file, and the error the run ended with, if any.
type AgentDoneMsg struct {
	Line string
	Err  error
}

// OpenInAgent hands an item to `prismis-cli open`, which starts the configured agent.
// The CLI runs through tea.ExecProcess with the TUI suspended, so claude or pi owns the
// terminal until it exits; its outcome line arrives through a temp status file. In
// remote mode the CLI is pointed at the same daemon with --remote.
func OpenInAgent(itemID, remoteURL string) tea.Cmd {
	path, err := exec.LookPath("prismis-cli")
	if err != nil {
		return func() tea.Msg {
			return AgentDoneMsg{Line: MissingAgentCLILine}
		}
	}

	status, err := os.CreateTemp("", "prismis-agent-*.status")
	if err != nil {
		return func() tea.Msg {
			return AgentDoneMsg{Err: fmt.Errorf("failed to create status file: %w", err)}
		}
	}
	statusPath := status.Name()
	_ = status.Close()

	var args []string
	if remoteURL != "" {
		args = append(args, "--remote", remoteURL)
	}
	args = append(args, "open", itemID, "--status-file", statusPath)

	c := exec.Command(path, args...)
	return tea.ExecProcess(c, func(err error) tea.Msg {
		return AgentDoneMsg{Line: takeStatusLine(statusPath), Err: err}
	})
}

// takeStatusLine returns the first line of the status file, and removes the file.
func takeStatusLine(path string) string {
	data, err := os.ReadFile(path)
	_ = os.Remove(path)
	if err != nil {
		return ""
	}
	line, _, _ := strings.Cut(string(data), "\n")
	return strings.TrimSpace(line)
}
