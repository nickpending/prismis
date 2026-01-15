package main

import (
	"flag"
	"log"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis/internal/config"
	"github.com/nickpending/prismis/internal/ui"
)

func main() {
	// Parse CLI flags
	remoteURL := flag.String("remote", "", "Remote daemon URL (e.g., http://server:8989)")
	flag.Parse()

	// Create model: --remote flag > config [remote].url > local mode
	var initialModel tea.Model
	if *remoteURL != "" {
		// Explicit --remote flag takes priority
		initialModel = ui.NewModelRemote(*remoteURL)
	} else {
		// Check config for [remote] section
		cfg, err := config.LoadConfig()
		if err == nil && cfg.HasRemoteConfig() {
			initialModel = ui.NewModelRemote(cfg.GetRemoteURL())
		} else {
			initialModel = ui.NewModel()
		}
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
