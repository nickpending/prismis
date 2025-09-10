package ui

import (
	"testing"

	"github.com/charmbracelet/lipgloss"
)

func TestCleanCyberThemeColors(t *testing.T) {
	theme := CleanCyberTheme

	// Test that all colors are defined
	tests := []struct {
		name     string
		color    lipgloss.Color
		expected string
	}{
		{"Cyan", theme.Cyan, "#00D9FF"},
		{"Purple", theme.Purple, "#E6CCFF"},
		{"Green", theme.Green, "#00FF88"},
		{"Red", theme.Red, "#FF0066"},
		{"Orange", theme.Orange, "#FF8800"},
		{"Gray", theme.Gray, "#666666"},
		{"DarkGray", theme.DarkGray, "#333333"},
		{"White", theme.White, "#EEEEEE"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if string(tt.color) != tt.expected {
				t.Errorf("Color %s = %v, want %v", tt.name, tt.color, tt.expected)
			}
		})
	}

	// Test theme name
	if theme.Name != "clean_cyber" {
		t.Errorf("Theme name = %v, want clean_cyber", theme.Name)
	}
}

func TestThemeStyleMethods(t *testing.T) {
	theme := CleanCyberTheme

	// Test that style methods don't panic
	_ = theme.BorderStyle()
	_ = theme.HeaderStyle()
	_ = theme.HighPriorityStyle()
	_ = theme.MediumPriorityStyle()
	_ = theme.LowPriorityStyle()
	_ = theme.TagStyle()
	_ = theme.SuccessStyle()
	_ = theme.TextStyle()
	_ = theme.MutedStyle()

	// If we get here without panicking, the test passes
	t.Log("All style methods executed successfully")
}
