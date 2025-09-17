package fabric

import (
	"bytes"
	"context"
	"fmt"
	"io"
	"os/exec"
	"strings"
	"time"
)

// Executor handles running Fabric patterns on content
type Executor struct {
	timeout  time.Duration
	patterns *Patterns
}

// NewExecutor creates a new Fabric executor with default 30s timeout
func NewExecutor() *Executor {
	return &Executor{
		timeout:  30 * time.Second,
		patterns: NewPatterns(),
	}
}

// ExecutePattern runs a Fabric pattern on the given content
// Usage: echo "content" | fabric <pattern>
func (e *Executor) ExecutePattern(pattern string, content string) (string, error) {
	if content == "" {
		return "", fmt.Errorf("no content to process")
	}

	if pattern == "" {
		return "", fmt.Errorf("no pattern specified")
	}

	// Validate pattern against available patterns to prevent command injection
	if !e.patterns.ValidatePattern(pattern) {
		return "", fmt.Errorf("invalid fabric pattern: %s", pattern)
	}

	// Create context with timeout
	ctx, cancel := context.WithTimeout(context.Background(), e.timeout)
	defer cancel()

	// Build command - fabric -c <pattern> (with clipboard copy)
	cmd := exec.CommandContext(ctx, "fabric", "-c", pattern)

	// Get stdin pipe
	stdin, err := cmd.StdinPipe()
	if err != nil {
		return "", fmt.Errorf("failed to get stdin pipe: %w", err)
	}

	// Capture output
	var stdout, stderr bytes.Buffer
	cmd.Stdout = &stdout
	cmd.Stderr = &stderr

	// Start the command
	if err := cmd.Start(); err != nil {
		return "", fmt.Errorf("failed to start fabric: %w", err)
	}

	// Write content to stdin
	go func() {
		defer stdin.Close()
		io.WriteString(stdin, content)
	}()

	// Wait for command to complete
	if err := cmd.Wait(); err != nil {
		// Check if it was a timeout
		if ctx.Err() == context.DeadlineExceeded {
			return "", fmt.Errorf("fabric execution timed out after %v", e.timeout)
		}

		// Include stderr in error for debugging
		stderrStr := strings.TrimSpace(stderr.String())
		if stderrStr != "" {
			return "", fmt.Errorf("fabric failed: %w\nstderr: %s", err, stderrStr)
		}
		return "", fmt.Errorf("fabric failed: %w", err)
	}

	// Return the output
	result := stdout.String()
	if strings.TrimSpace(result) == "" {
		return "", fmt.Errorf("fabric returned no output")
	}

	return result, nil
}

// SetTimeout updates the execution timeout
func (e *Executor) SetTimeout(timeout time.Duration) {
	e.timeout = timeout
}
