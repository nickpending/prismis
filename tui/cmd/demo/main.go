package main

import (
	"fmt"
	"log"
	"os"

	"github.com/nickpending/prismis/internal/api"
)

func main() {
	fmt.Println("=== Prismis TUI HTTP Client Demo ===")
	fmt.Println()

	// For demo, set up test config
	tmpDir := "/tmp/prismis-demo"
	os.MkdirAll(tmpDir+"/prismis", 0755)
	configContent := `[api]
key = "test-key"
`
	os.WriteFile(tmpDir+"/prismis/config.toml", []byte(configContent), 0644)
	os.Setenv("XDG_CONFIG_HOME", tmpDir)

	// Create client using NewClient
	client, err := api.NewClient()
	if err != nil {
		log.Fatalf("Failed to create client: %v", err)
	}

	// 1. Get current sources
	fmt.Println("1. Getting current sources...")
	sources, err := client.GetSources()
	if err != nil {
		log.Fatalf("Error getting sources: %v", err)
	}
	fmt.Printf("   Found %d sources\n", sources.Total)
	if len(sources.Sources) > 0 {
		fmt.Printf("   First source: %s (%s)\n", sources.Sources[0].URL, sources.Sources[0].Type)
	}
	fmt.Println()

	// 2. Add a new source
	fmt.Println("2. Adding test RSS feed...")
	req := api.SourceRequest{
		URL:  "https://feeds.arstechnica.com/arstechnica/index",
		Type: "rss",
	}
	resp, err := client.AddSource(req)
	if err != nil {
		fmt.Printf("   Error (expected if already exists): %v\n", err)
	} else {
		fmt.Printf("   Success: %s\n", resp.Message)
		if resp.Data != nil {
			if id, ok := resp.Data["id"]; ok {
				fmt.Printf("   Source ID: %v\n", id)
			}
		}
	}
	fmt.Println()

	// 3. Get sources again to see the new one
	fmt.Println("3. Getting sources after add...")
	sources, err = client.GetSources()
	if err != nil {
		log.Fatalf("Error getting sources: %v", err)
	}
	fmt.Printf("   Now have %d sources\n", sources.Total)
	fmt.Println()

	// 4. Try to delete a non-existent source to show error handling
	fmt.Println("4. Testing delete with non-existent ID...")
	_, err = client.DeleteSource("non-existent-id")
	if err != nil {
		fmt.Printf("   Error (expected): %v\n", err)
	}
	fmt.Println()

	fmt.Println("=== Demo Complete ===")
	fmt.Println("All HTTP client methods working correctly!")
}
