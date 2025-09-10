package config

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/BurntSushi/toml"
)

// Config represents the TUI configuration from config.toml
type Config struct {
	API struct {
		Key string `toml:"key"`
	} `toml:"api"`
	TUI struct {
		RefreshInterval int `toml:"refresh_interval"` // Auto-refresh interval in seconds, 0 disables
	} `toml:"tui"`
}

// LoadConfig loads configuration from the standard XDG config path with sensible defaults
func LoadConfig() (*Config, error) {
	// Get config directory using XDG_CONFIG_HOME or fallback
	configDir := os.Getenv("XDG_CONFIG_HOME")
	if configDir == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return nil, fmt.Errorf("failed to get home directory: %w", err)
		}
		configDir = filepath.Join(home, ".config")
	}

	configPath := filepath.Join(configDir, "prismis", "config.toml")

	// Initialize config with defaults
	config := &Config{
		TUI: struct {
			RefreshInterval int `toml:"refresh_interval"`
		}{
			RefreshInterval: 60, // Default to 60 seconds
		},
	}

	// Read config file if it exists
	if _, err := os.Stat(configPath); err == nil {
		configData, err := os.ReadFile(configPath)
		if err != nil {
			return nil, fmt.Errorf("failed to read config file: %w", err)
		}

		// Parse TOML config, merging with defaults
		if err := toml.Unmarshal(configData, config); err != nil {
			return nil, fmt.Errorf("failed to parse config: %w", err)
		}
	}

	return config, nil
}

// GetRefreshInterval returns the configured refresh interval in seconds
// Returns 0 if auto-refresh is disabled
func (c *Config) GetRefreshInterval() int {
	return c.TUI.RefreshInterval
}
