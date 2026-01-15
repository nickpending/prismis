package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"

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
	Reports *struct {
		OutputPath string `toml:"output_path"` // Directory to save reports, required
	} `toml:"reports"`
	Remote *struct {
		URL string `toml:"url"` // Remote daemon URL (e.g., https://prismis.example.com)
		Key string `toml:"key"` // API key for remote daemon
	} `toml:"remote"`
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

// ValidateReports validates that reports configuration is present and valid
func (c *Config) ValidateReports() error {
	if c.Reports == nil {
		return fmt.Errorf("reports configuration missing. Add [reports] section to config.toml with output_path")
	}

	if c.Reports.OutputPath == "" {
		return fmt.Errorf("reports.output_path not configured. Add output_path to [reports] section in config.toml")
	}

	return nil
}

// GetReportsOutputPath returns the configured reports output path, expanding ~ to home directory
func (c *Config) GetReportsOutputPath() (string, error) {
	if err := c.ValidateReports(); err != nil {
		return "", err
	}

	outputPath := c.Reports.OutputPath

	// Expand ~ to home directory
	if strings.HasPrefix(outputPath, "~/") {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", fmt.Errorf("failed to get home directory for reports path: %w", err)
		}
		outputPath = filepath.Join(home, outputPath[2:])
	}

	return outputPath, nil
}

// HasRemoteConfig returns true if [remote] section is configured with a URL
func (c *Config) HasRemoteConfig() bool {
	return c.Remote != nil && c.Remote.URL != ""
}

// GetRemoteURL returns the remote daemon URL if configured
func (c *Config) GetRemoteURL() string {
	if c.Remote != nil {
		return c.Remote.URL
	}
	return ""
}

// GetRemoteKey returns the remote API key if configured
func (c *Config) GetRemoteKey() string {
	if c.Remote != nil {
		return c.Remote.Key
	}
	return ""
}
