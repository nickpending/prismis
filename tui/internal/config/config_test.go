package config

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLoadConfig_WithDefaults(t *testing.T) {
	// Test loading config when file doesn't exist - should use defaults
	oldXDG := os.Getenv("XDG_CONFIG_HOME")
	defer os.Setenv("XDG_CONFIG_HOME", oldXDG)

	// Set to non-existent directory
	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() failed: %v", err)
	}

	// Should have default refresh interval
	if config.TUI.RefreshInterval != 60 {
		t.Errorf("Expected default refresh interval 60, got %d", config.TUI.RefreshInterval)
	}

	// API key should be empty (no config file)
	if config.API.Key != "" {
		t.Errorf("Expected empty API key, got %q", config.API.Key)
	}
}

func TestLoadConfig_WithFile(t *testing.T) {
	// Test loading config from actual file
	oldXDG := os.Getenv("XDG_CONFIG_HOME")
	defer os.Setenv("XDG_CONFIG_HOME", oldXDG)

	// Create temporary config directory
	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	configDir := filepath.Join(tmpDir, "prismis")
	err := os.MkdirAll(configDir, 0755)
	if err != nil {
		t.Fatalf("Failed to create config directory: %v", err)
	}

	// Write test config file
	configContent := `[api]
key = "test-api-key"

[tui]
refresh_interval = 120
`
	configPath := filepath.Join(configDir, "config.toml")
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	// Load config
	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() failed: %v", err)
	}

	// Should have values from file
	if config.API.Key != "test-api-key" {
		t.Errorf("Expected API key 'test-api-key', got %q", config.API.Key)
	}

	if config.TUI.RefreshInterval != 120 {
		t.Errorf("Expected refresh interval 120, got %d", config.TUI.RefreshInterval)
	}
}

func TestLoadConfig_PartialFile(t *testing.T) {
	// Test loading config with only some values - should merge with defaults
	oldXDG := os.Getenv("XDG_CONFIG_HOME")
	defer os.Setenv("XDG_CONFIG_HOME", oldXDG)

	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	configDir := filepath.Join(tmpDir, "prismis")
	err := os.MkdirAll(configDir, 0755)
	if err != nil {
		t.Fatalf("Failed to create config directory: %v", err)
	}

	// Write partial config (only API key)
	configContent := `[api]
key = "partial-key"
`
	configPath := filepath.Join(configDir, "config.toml")
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() failed: %v", err)
	}

	// Should have API key from file
	if config.API.Key != "partial-key" {
		t.Errorf("Expected API key 'partial-key', got %q", config.API.Key)
	}

	// Should have default refresh interval
	if config.TUI.RefreshInterval != 60 {
		t.Errorf("Expected default refresh interval 60, got %d", config.TUI.RefreshInterval)
	}
}

func TestGetRefreshInterval(t *testing.T) {
	config := &Config{
		TUI: struct {
			RefreshInterval int `toml:"refresh_interval"`
		}{
			RefreshInterval: 90,
		},
	}

	interval := config.GetRefreshInterval()
	if interval != 90 {
		t.Errorf("Expected refresh interval 90, got %d", interval)
	}
}

func TestLoadConfig_DisabledRefresh(t *testing.T) {
	// Test with refresh_interval = 0 (disabled)
	oldXDG := os.Getenv("XDG_CONFIG_HOME")
	defer os.Setenv("XDG_CONFIG_HOME", oldXDG)

	tmpDir := t.TempDir()
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	configDir := filepath.Join(tmpDir, "prismis")
	err := os.MkdirAll(configDir, 0755)
	if err != nil {
		t.Fatalf("Failed to create config directory: %v", err)
	}

	// Write config with refresh disabled
	configContent := `[tui]
refresh_interval = 0
`
	configPath := filepath.Join(configDir, "config.toml")
	err = os.WriteFile(configPath, []byte(configContent), 0644)
	if err != nil {
		t.Fatalf("Failed to write config file: %v", err)
	}

	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig() failed: %v", err)
	}

	if config.TUI.RefreshInterval != 0 {
		t.Errorf("Expected refresh interval 0 (disabled), got %d", config.TUI.RefreshInterval)
	}
}
