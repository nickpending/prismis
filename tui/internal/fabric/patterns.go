package fabric

import (
	"bufio"
	"bytes"
	"context"
	"os/exec"
	"strings"
	"sync"
	"time"
)

// Patterns manages the list of available Fabric patterns
type Patterns struct {
	patterns []string
	once     sync.Once
	mu       sync.RWMutex
}

// NewPatterns creates a new Patterns instance
func NewPatterns() *Patterns {
	return &Patterns{
		patterns: make([]string, 0),
	}
}

// GetPatterns returns the list of available Fabric patterns (cached after first call)
func (p *Patterns) GetPatterns() []string {
	// Use sync.Once to ensure patterns are fetched exactly once
	p.once.Do(func() {
		patterns, err := p.fetchPatterns()

		p.mu.Lock()
		defer p.mu.Unlock()

		if err != nil {
			// Return empty slice on error (graceful degradation)
			p.patterns = make([]string, 0)
		} else {
			p.patterns = patterns
		}
	})

	// Return cached patterns with read lock
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.patterns
}

// ValidatePattern checks if a pattern exists in the cached list
func (p *Patterns) ValidatePattern(pattern string) bool {
	patterns := p.GetPatterns()
	for _, p := range patterns {
		if p == pattern {
			return true
		}
	}
	return false
}

// FilterPatterns returns patterns matching the given prefix
func (p *Patterns) FilterPatterns(prefix string) []string {
	patterns := p.GetPatterns()
	if prefix == "" {
		return patterns
	}

	var matches []string
	lowerPrefix := strings.ToLower(prefix)

	for _, pattern := range patterns {
		if strings.HasPrefix(strings.ToLower(pattern), lowerPrefix) {
			matches = append(matches, pattern)
		}
	}

	return matches
}

// Reset clears the cached patterns (useful for testing)
func (p *Patterns) Reset() {
	p.mu.Lock()
	defer p.mu.Unlock()
	p.patterns = make([]string, 0)
	// Note: sync.Once cannot be reset, so this creates a new instance for testing
	p.once = sync.Once{}
}

// fetchPatterns executes 'fabric --list' and parses the output
func (p *Patterns) fetchPatterns() ([]string, error) {
	// Create context with timeout (5 seconds should be enough for list)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	// Execute fabric --listpatterns
	cmd := exec.CommandContext(ctx, "fabric", "--listpatterns")

	var stdout bytes.Buffer
	cmd.Stdout = &stdout

	if err := cmd.Run(); err != nil {
		return nil, err
	}

	// Parse output line by line
	var patterns []string
	scanner := bufio.NewScanner(&stdout)

	for scanner.Scan() {
		line := strings.TrimSpace(scanner.Text())
		if line != "" {
			// Skip empty lines and potential headers/separators
			if !strings.Contains(line, "---") && !strings.Contains(line, "Pattern") {
				patterns = append(patterns, line)
			}
		}
	}

	if err := scanner.Err(); err != nil {
		return nil, err
	}

	return patterns, nil
}
