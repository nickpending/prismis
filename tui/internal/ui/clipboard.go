package ui

import (
	"fmt"
	"io"
	"os/exec"
	"runtime"
	"strings"
)

// CopyToClipboard copies the given text to the system clipboard.
// It detects the OS and uses the appropriate command.
func CopyToClipboard(text string) error {
	if text == "" {
		return fmt.Errorf("cannot copy empty text to clipboard")
	}

	var cmd *exec.Cmd

	switch runtime.GOOS {
	case "darwin":
		// macOS: use pbcopy
		cmd = exec.Command("pbcopy")
	case "linux":
		// Linux: try xclip first, then xsel, then wl-copy for Wayland
		if _, err := exec.LookPath("xclip"); err == nil {
			cmd = exec.Command("xclip", "-selection", "clipboard")
		} else if _, err := exec.LookPath("xsel"); err == nil {
			cmd = exec.Command("xsel", "--clipboard", "--input")
		} else if _, err := exec.LookPath("wl-copy"); err == nil {
			cmd = exec.Command("wl-copy")
		} else {
			return fmt.Errorf("no clipboard command found (install xclip, xsel, or wl-clipboard)")
		}
	case "windows":
		// Windows: use clip.exe (works in WSL too)
		cmd = exec.Command("clip.exe")
	default:
		return fmt.Errorf("unsupported platform: %s", runtime.GOOS)
	}

	// Get stdin pipe
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return fmt.Errorf("failed to get stdin pipe: %w", err)
	}

	// Start the command
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("failed to start clipboard command: %w", err)
	}

	// Write text to stdin
	if _, err := io.WriteString(stdin, text); err != nil {
		// Try to kill the command if write fails
		_ = cmd.Process.Kill()
		return fmt.Errorf("failed to write to clipboard: %w", err)
	}

	// Close stdin to signal we're done
	if err := stdin.Close(); err != nil {
		return fmt.Errorf("failed to close stdin: %w", err)
	}

	// Wait for command to complete
	if err := cmd.Wait(); err != nil {
		// Check if it's just an exit status issue (some clipboard commands return non-zero)
		if !strings.Contains(err.Error(), "exit status") {
			return fmt.Errorf("clipboard command failed: %w", err)
		}
	}

	return nil
}
