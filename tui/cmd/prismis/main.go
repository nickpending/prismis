package main

import (
	"log"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/ui"
)

func main() {
	initialModel := ui.NewModel()

	// Configure program to not clear screen on exit and use alt screen buffer
	p := tea.NewProgram(
		initialModel,
		tea.WithAltScreen(),       // Use alternate screen buffer
		tea.WithMouseCellMotion(), // Enable mouse support
	)
	if _, err := p.Run(); err != nil {
		log.Fatal(err)
	}
}
