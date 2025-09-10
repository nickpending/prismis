package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfig_MalformedTOML(t *testing.T) {
	// MEDIUM RISK: Malformed TOML should fail gracefully, not panic
	oldXDG := os.Getenv("XDG_CONFIG_HOME")
	defer os.Setenv("XDG_CONFIG_HOME", oldXDG)

	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	configDir := filepath.Join(tmpDir, "prismis")
	err := os.MkdirAll(configDir, 0755)
	if err != nil {
		t.Fatalf("Failed to create config directory: %v", err)
	}

	// Write malformed TOML
	malformedContent := `[api]
key = "test-key
# Missing closing quote

[tui
# Missing closing bracket
refresh_interval = not_a_number
`
	configPath := filepath.Join(configDir, "config.toml")
	err = os.WriteFile(configPath, []byte(malformedContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write malformed config: %v", err)
	}

	// Should return error, not panic
	config, err := LoadConfig()
	if err == nil {
		t.Fatal("Expected error for malformed TOML, got nil")
	}

	// Should have returned nil config
	if config != nil {
		t.Error("Expected nil config for malformed TOML")
	}

	// Error message should be helpful
	if err.Error() == "" {
		t.Error("Expected descriptive error message")
	}
}
