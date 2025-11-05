package main

import (
	"flag"
	"log"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/ui"
)

func main() {
	// Parse CLI flags
	remoteURL := flag.String("remote", "", "Remote daemon URL (e.g., http://server:8989)")
	flag.Parse()

	// Create model with remote URL if provided
	var initialModel tea.Model
	if *remoteURL != "" {
		initialModel = ui.NewModelRemote(*remoteURL)
	} else {
		initialModel = ui.NewModel()
	}

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
