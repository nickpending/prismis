package main

import (
	"log"
	"os"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/ui"
)

func main() {
	// Create the TUI model
	model := ui.NewModel()

	// Create the Bubble Tea program with alternate screen
	program := tea.NewProgram(model, tea.WithAltScreen())

	// Run the program
	if _, err := program.Run(); err != nil {
		log.Printf("Error running TUI: %v", err)
		os.Exit(1)
	}
}