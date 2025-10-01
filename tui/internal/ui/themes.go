package ui

import (
	"fmt"
	"strconv"
	"strings"

	"github.com/charmbracelet/glamour/ansi"
	"github.com/charmbracelet/glamour/styles"
	"github.com/charmbracelet/lipgloss"
)

// StyleTheme defines a clean cyberpunk color scheme for the TUI
type StyleTheme struct {
	Name          string
	Cyan          lipgloss.Color // Primary UI accent #00D9FF
	Purple        lipgloss.Color // Tags and metadata #E6CCFF
	VibrantPurple lipgloss.Color // Errors and gradient accent #9F4DFF
	Green         lipgloss.Color // Success/online indicators #00FF88
	Red           lipgloss.Color // High priority #FF0066
	Orange        lipgloss.Color // Medium priority #FF8800
	Gray          lipgloss.Color // Muted text/low priority #666666
	DarkGray      lipgloss.Color // Borders and backgrounds #333333
	White         lipgloss.Color // Main text #EEEEEE
}

// CleanCyberTheme provides the exact colors used in clean_cyber.go
var CleanCyberTheme = StyleTheme{
	Name:          "clean_cyber",
	Cyan:          lipgloss.Color("#00D9FF"),
	Purple:        lipgloss.Color("#E6CCFF"),
	VibrantPurple: lipgloss.Color("#9F4DFF"),
	Green:         lipgloss.Color("#00FF88"),
	Red:           lipgloss.Color("#FF0066"),
	Orange:        lipgloss.Color("#FF8800"),
	Gray:          lipgloss.Color("#666666"),
	DarkGray:      lipgloss.Color("#333333"),
	White:         lipgloss.Color("#EEEEEE"),
}

// MonokaiProTheme provides warm dark colors inspired by Monokai Pro
var MonokaiProTheme = StyleTheme{
	Name:          "monokai_pro",
	Cyan:          lipgloss.Color("#78DCE8"),
	Purple:        lipgloss.Color("#AB9DF2"),
	VibrantPurple: lipgloss.Color("#FF6188"),
	Green:         lipgloss.Color("#A9DC76"),
	Red:           lipgloss.Color("#FF6188"),
	Orange:        lipgloss.Color("#FC9867"),
	Gray:          lipgloss.Color("#727072"),
	DarkGray:      lipgloss.Color("#403E41"),
	White:         lipgloss.Color("#FCFCFA"),
}

// LightTheme provides a warm, natural color scheme distinct from cyber aesthetic
// Softer tones that still maintain readability on dark terminal backgrounds
var LightTheme = StyleTheme{
	Name:          "light",
	Cyan:          lipgloss.Color("#06B6D4"), // Soft cyan/turquoise (vs neon cyan)
	Purple:        lipgloss.Color("#8B5CF6"), // Deep violet (vs light lavender)
	VibrantPurple: lipgloss.Color("#EC4899"), // Rose pink accent (vs neon purple)
	Green:         lipgloss.Color("#22C55E"), // Grass green (vs electric green)
	Red:           lipgloss.Color("#F43F5E"), // Rose red (vs hot pink)
	Orange:        lipgloss.Color("#FB923C"), // Warm peach (vs bright orange)
	Gray:          lipgloss.Color("#64748B"), // Slate gray (vs neutral gray)
	DarkGray:      lipgloss.Color("#475569"), // Dark slate (vs charcoal)
	White:         lipgloss.Color("#F1F5F9"), // Slate white (vs stark white)
}

// AvailableThemes is a list of all available themes for cycling
var AvailableThemes = []StyleTheme{
	CleanCyberTheme,
	MonokaiProTheme,
	LightTheme,
}

// Package-level variables for backward compatibility
var (
	ErrorStyle    = CleanCyberTheme.ErrorStyle()
	DimmedStyle   = CleanCyberTheme.DimmedStyle()
	SelectedStyle = CleanCyberTheme.SelectedStyle()
)

// Common styles using the clean cyber theme
func (t StyleTheme) BorderStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		BorderStyle(lipgloss.NormalBorder()).
		BorderForeground(t.DarkGray)
}

func (t StyleTheme) HeaderStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Background(t.DarkGray).
		Foreground(t.Cyan).
		Bold(true)
}

func (t StyleTheme) HighPriorityStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Red).
		Bold(true)
}

func (t StyleTheme) MediumPriorityStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Orange).
		Bold(true)
}

func (t StyleTheme) LowPriorityStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Gray)
}

func (t StyleTheme) TagStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Purple)
}

func (t StyleTheme) SuccessStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Green)
}

func (t StyleTheme) TextStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.White)
}

func (t StyleTheme) MutedStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Gray)
}

func (t StyleTheme) ErrorStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.VibrantPurple).
		Bold(true)
}

func (t StyleTheme) DimmedStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Gray).
		Faint(true)
}

func (t StyleTheme) SelectedStyle() lipgloss.Style {
	return lipgloss.NewStyle().
		Foreground(t.Cyan).
		Bold(true)
}

// ToGlamourStyle converts our theme to a glamour style config for markdown rendering
func (t StyleTheme) ToGlamourStyle() ansi.StyleConfig {
	// Start with a base dark style
	style := styles.DraculaStyleConfig

	// Remove document margin for proper modal rendering
	style.Document.Margin = uintPtr(0)

	// Map our theme colors to glamour's markdown elements
	style.Document.StylePrimitive.Color = stringPtr(string(t.White))
	style.Heading.StylePrimitive.Color = stringPtr(string(t.Cyan))
	style.Heading.StylePrimitive.Bold = boolPtr(true)

	// Make H1 stand out more than H2
	style.H1.StylePrimitive.Color = stringPtr(string(t.Cyan))
	style.H1.StylePrimitive.Bold = boolPtr(true)
	style.H1.StylePrimitive.Prefix = ""
	style.H1.Prefix = "▸ " // Replace ## with arrow
	style.H1.Suffix = ""   // Remove any suffix
	style.H1.Format = ""   // Clear the ## format

	// H2 headers
	style.H2.StylePrimitive.Color = stringPtr(string(t.Cyan))
	style.H2.StylePrimitive.Bold = boolPtr(true)
	style.H2.Prefix = "▸ " // Replace ## with arrow
	style.H2.Suffix = ""
	style.H2.Format = ""

	// H3 headers
	style.H3.StylePrimitive.Color = stringPtr(string(t.Cyan))
	style.H3.Prefix = "▸ " // Replace ### with arrow
	style.H3.Suffix = ""
	style.H3.Format = ""

	// H4-H6 headers
	style.H4.Prefix = "▸ "
	style.H4.Suffix = ""
	style.H4.Format = ""
	style.H5.Prefix = "▸ "
	style.H5.Suffix = ""
	style.H5.Format = ""
	style.H6.Prefix = "▸ "
	style.H6.Suffix = ""
	style.H6.Format = ""

	style.Link.Color = stringPtr(string(t.Purple))
	style.LinkText.Color = stringPtr(string(t.Purple))
	style.Code.Color = stringPtr(string(t.Green))
	style.CodeBlock.StylePrimitive.Color = stringPtr(string(t.Green))
	style.Emph.Color = stringPtr(string(t.Orange))
	style.Strong.Color = stringPtr(string(t.Red))

	// List styling - subtle indent, normal text color
	style.List.StyleBlock.Indent = uintPtr(1)                               // Small indent for lists
	style.List.StyleBlock.IndentToken = stringPtr("  ")                     // 2 spaces for wrapped lines
	style.List.StyleBlock.StylePrimitive.Color = stringPtr(string(t.White)) // Normal white text
	style.List.LevelIndent = 4                                              // Indent nested lists more

	// Item styling - simple and clean
	style.Item.BlockPrefix = "• "                 // Bullet prefix
	style.Item.Color = stringPtr(string(t.White)) // White text for items
	style.Item.Format = ""                        // Clear any default format

	// Enumeration (numbered lists) - normal color
	style.Enumeration.Color = stringPtr(string(t.White)) // White for numbered lists too

	// Task list items
	style.Task.Ticked = "[✓] "
	style.Task.Unticked = "[ ] "

	// Make blockquotes lighter - use a lighter gray or dim white
	style.BlockQuote.StylePrimitive.Color = stringPtr("#999999") // Lighter gray for better readability
	style.BlockQuote.StylePrimitive.Italic = boolPtr(true)

	return style
}

// Helper functions for creating pointers
func stringPtr(s string) *string { return &s }
func uintPtr(u uint) *uint       { return &u }
func boolPtr(b bool) *bool       { return &b }

// RenderWithGradientBackground renders text with a gradient background
func RenderWithGradientBackground(text string, width int, startColor, endColor string) string {
	// Ensure text is exactly the width specified
	var paddedText string
	textRunes := []rune(text)
	if len(textRunes) < width {
		// Pad with spaces to reach full width
		paddedText = string(textRunes) + strings.Repeat(" ", width-len(textRunes))
	} else {
		// Truncate if too long
		paddedText = string(textRunes[:width])
	}

	// Split into characters for individual background colors
	runes := []rune(paddedText)
	var result strings.Builder

	for i, r := range runes {
		// Calculate position along gradient (0.0 to 1.0)
		position := float64(i) / float64(max(width-1, 1))

		// Interpolate background color at this position
		bgColor := InterpolateColor(startColor, endColor, position)

		// Apply gradient background with white/bright foreground for readability
		style := lipgloss.NewStyle().
			Background(lipgloss.Color(bgColor)).
			Foreground(lipgloss.Color("#FFFFFF")).
			Bold(true)

		result.WriteString(style.Render(string(r)))
	}

	return result.String()
}

// InterpolateColor interpolates between two hex colors at the given position
func InterpolateColor(startColor, endColor string, position float64) string {
	// Parse start color
	startR, startG, startB, err := parseHexColor(startColor)
	if err != nil {
		return startColor // Fallback to start color
	}

	// Parse end color
	endR, endG, endB, err := parseHexColor(endColor)
	if err != nil {
		return startColor // Fallback to start color
	}

	// Clamp position to valid range
	if position < 0 {
		position = 0
	}
	if position > 1 {
		position = 1
	}

	// Interpolate RGB values
	r := int(float64(startR) + (float64(endR-startR) * position))
	g := int(float64(startG) + (float64(endG-startG) * position))
	b := int(float64(startB) + (float64(endB-startB) * position))

	// Convert back to hex
	return fmt.Sprintf("#%02X%02X%02X", r, g, b)
}

// parseHexColor parses a hex color string into RGB values
func parseHexColor(hexColor string) (int, int, int, error) {
	// Remove # prefix if present
	if strings.HasPrefix(hexColor, "#") {
		hexColor = hexColor[1:]
	}

	// Must be 6 characters for RGB
	if len(hexColor) != 6 {
		return 0, 0, 0, fmt.Errorf("invalid hex color format")
	}

	// Parse RGB components
	r, err := strconv.ParseInt(hexColor[0:2], 16, 0)
	if err != nil {
		return 0, 0, 0, fmt.Errorf("invalid red component: %w", err)
	}

	g, err := strconv.ParseInt(hexColor[2:4], 16, 0)
	if err != nil {
		return 0, 0, 0, fmt.Errorf("invalid green component: %w", err)
	}

	b, err := strconv.ParseInt(hexColor[4:6], 16, 0)
	if err != nil {
		return 0, 0, 0, fmt.Errorf("invalid blue component: %w", err)
	}

	return int(r), int(g), int(b), nil
}

// RenderGradientText renders text with a gradient from startColor to endColor
func RenderGradientText(text string, startColor, endColor string) string {
	if text == "" {
		return ""
	}

	runes := []rune(text)
	if len(runes) == 1 {
		// Single character - use start color
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(startColor))
		return style.Render(text)
	}

	var result strings.Builder
	for i, r := range runes {
		// Calculate position along gradient (0.0 to 1.0)
		position := float64(i) / float64(len(runes)-1)

		// Interpolate color at this position
		color := InterpolateColor(startColor, endColor, position)

		// Apply color to this character
		style := lipgloss.NewStyle().Foreground(lipgloss.Color(color))
		result.WriteString(style.Render(string(r)))
	}

	return result.String()
}
