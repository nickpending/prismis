package ui

import (
	"fmt"
	"runtime"
	"strings"
	"time"

	"github.com/charmbracelet/lipgloss"
	"github.com/nickpending/prismis-local/internal/db"
)

// buildViewStateString creates a formatted string showing current view state
func buildViewStateString(m Model) string {
	var states []string

	// Priority filter
	switch m.priority {
	case "high":
		states = append(states, "Priority: HIGH")
	case "medium":
		states = append(states, "Priority: MEDIUM")
	case "low":
		states = append(states, "Priority: LOW")
	case "unprioritized":
		states = append(states, "Priority: UNPRIORITIZED")
	case "favorites":
		states = append(states, "Priority: ★ FAVORITES")
	default:
		states = append(states, "Priority: PRIORITIZED")
	}

	// View state (unread vs all)
	if m.showAll {
		states = append(states, "View: ALL")
	} else {
		states = append(states, "View: UNREAD")
	}

	// Sort state (newest vs oldest)
	if m.sortNewest {
		states = append(states, "Sort: NEWEST")
	} else {
		states = append(states, "Sort: OLDEST")
	}

	// Filter state (source type)
	switch m.filterType {
	case "rss":
		states = append(states, "Filter: RSS")
	case "reddit":
		states = append(states, "Filter: REDDIT")
	case "youtube":
		states = append(states, "Filter: YOUTUBE")
	default:
		states = append(states, "Filter: ALL")
	}

	// Add hidden count if applicable
	if m.hiddenCount > 0 && !m.showUnprioritized {
		states = append(states, fmt.Sprintf("Hidden: %d", m.hiddenCount))
	}

	return strings.Join(states, " | ")
}

// RenderList renders the feed view with clean cyber styling
func RenderList(m Model) string {
	if m.width == 0 {
		return "Loading..."
	}

	if m.loading {
		return renderLoading()
	}

	if m.err != nil {
		return renderError(m.err)
	}

	theme := CleanCyberTheme

	// Build the header content with padding built-in
	title := " PRISMIS" // Add space for left padding

	// Build state string
	stateString := buildViewStateString(m)

	// Add time
	timeString := time.Now().Format("15:04")

	// Calculate spacing to right-align state and time
	stateTimeString := fmt.Sprintf("%s  ◆ %s ", stateString, timeString) // Add space for right padding
	availableWidth := m.width - len(title) - len(stateTimeString)

	var spacing string
	if availableWidth > 0 {
		spacing = strings.Repeat(" ", availableWidth)
	} else {
		spacing = "  " // Minimum spacing
	}

	// Combine all parts to full width
	headerContent := fmt.Sprintf("%s%s%s", title, spacing, stateTimeString)

	// Create gradient background for the full width header (no additional styling)
	header := RenderWithGradientBackground(headerContent, m.width, "#00D9FF", "#9F4DFF")

	// Main content area
	contentHeight := m.height - 5 // header + empty line + status + borders

	// Left sidebar (25% width)
	sidebarWidth := m.width / 4
	if sidebarWidth < 30 {
		sidebarWidth = 30
	}

	// Right content (75% width)
	contentWidth := m.width - sidebarWidth - 1

	// Build sidebar
	sidebar := renderSidebar(m, sidebarWidth, contentHeight, theme)

	// Build main content list
	content := renderContentList(m, contentWidth, contentHeight, theme)

	// Combine sidebar and content
	sidebarStyle := lipgloss.NewStyle().
		Width(sidebarWidth).
		Height(contentHeight).
		BorderStyle(lipgloss.NormalBorder()).
		BorderRight(true).
		BorderForeground(theme.DarkGray).
		Padding(0, 1)

	contentStyle := lipgloss.NewStyle().
		Width(contentWidth).
		Height(contentHeight).
		Padding(0, 1)

	main := lipgloss.JoinHorizontal(
		lipgloss.Top,
		sidebarStyle.Render(sidebar),
		contentStyle.Render(content),
	)

	// Status bar
	statusStyle := lipgloss.NewStyle().
		Background(theme.DarkGray).
		Foreground(theme.Gray).
		Width(m.width).
		Padding(0, 1)

	// Show status message if present, otherwise show help
	var statusText string
	if m.statusMessage != "" {
		// Show status message with highlighted style
		statusText = lipgloss.NewStyle().
			Foreground(theme.Cyan).
			Bold(true).
			Render(m.statusMessage)
	} else {
		statusText = "j/k:navigate  enter:read  m:mark  o:open browser  c:copy  y:yank URL  u:unread/all  d:date sort  s:source filter  1/2/3/a:priority  0:unprioritized  q:quit"
	}
	status := statusStyle.Render(statusText)

	return lipgloss.JoinVertical(
		lipgloss.Left,
		header,
		"",
		main,
		status,
	)
}

func renderSidebar(m Model, width, height int, theme StyleTheme) string {
	// Calculate split heights (35% stats / 65% sources)
	statsHeight := height * 35 / 100

	// System Stats Section
	statsHeader := lipgloss.NewStyle().
		Foreground(theme.Gray). // Subtle gray for section dividers
		Render("── SYSTEM " + strings.Repeat("─", width-12))

	// Get actual source count and last update
	sourceCount := len(m.sources)
	totalItems := len(m.items)

	// Count priorities
	var highCount, medCount int
	for _, item := range m.items {
		switch item.Priority {
		case "high":
			highCount++
		case "medium":
			medCount++
		}
	}

	// Find most recent fetch time
	var lastUpdate string = "never"
	var mostRecent time.Time
	for _, source := range m.sources {
		if source.LastFetched != nil && source.LastFetched.After(mostRecent) {
			mostRecent = *source.LastFetched
		}
	}
	if !mostRecent.IsZero() {
		lastUpdate = formatTime(time.Since(mostRecent)) + " ago"
	}

	// Get memory usage
	memStats := getMemoryUsage()

	statsContent := []string{
		fmt.Sprintf("Sources:     %d active", sourceCount),
		fmt.Sprintf("Total:       %d items", totalItems),
		fmt.Sprintf("Priority:    %s %d high",
			lipgloss.NewStyle().Foreground(theme.Red).Render("▲"), highCount),
		fmt.Sprintf("Feed Health: %s Online",
			lipgloss.NewStyle().Foreground(theme.Green).Render("●")),
		fmt.Sprintf("Memory:      %s", memStats),
		fmt.Sprintf("Updates:     %s",
			lipgloss.NewStyle().Foreground(theme.Gray).Render(lastUpdate)),
	}

	statsSection := lipgloss.NewStyle().
		Height(statsHeight).
		Render(lipgloss.JoinVertical(
			lipgloss.Left,
			statsHeader,
			"", // Add blank line after header for consistency
			strings.Join(statsContent, "\n"),
		))

	// Divider removed - SOURCES header provides enough separation

	// Group sources by type for display
	sourcesByType := make(map[string][]db.Source)
	for _, source := range m.sources {
		sourcesByType[source.Type] = append(sourcesByType[source.Type], source)
	}

	// Sources Section
	sourceHeader := lipgloss.NewStyle().
		Foreground(theme.Gray). // Subtle gray to match SYSTEM header
		Render("── SOURCES " + strings.Repeat("─", width-13))

	sources := []string{}

	// RSS Sources
	if rssources, ok := sourcesByType["rss"]; ok && len(rssources) > 0 {
		header := fmt.Sprintf("RSS [%d]", len(rssources))
		sources = append(sources, lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render(header))
		for _, source := range rssources {
			// Status based on active, errors, and last fetch
			var status string
			if !source.Active {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("○") // Paused/inactive
			} else if source.ErrorCount > 3 {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("●") // Has errors
			} else if source.LastFetched == nil || time.Since(*source.LastFetched) > 24*time.Hour {
				status = lipgloss.NewStyle().Foreground(theme.Orange).Render("●") // Stale
			} else {
				status = lipgloss.NewStyle().Foreground(theme.Green).Render("●") // Healthy
			}
			// Color the item count number
			countStr := lipgloss.NewStyle().Foreground(theme.White).Render(fmt.Sprintf("[%d]", source.UnreadCount))
			sources = append(sources, fmt.Sprintf("%s %s %s", status, truncate(source.Name, width-12), countStr))
		}
		sources = append(sources, "")
	}

	// Reddit Sources
	if redditsources, ok := sourcesByType["reddit"]; ok && len(redditsources) > 0 {
		header := fmt.Sprintf("REDDIT [%d]", len(redditsources))
		sources = append(sources, lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render(header))
		for _, source := range redditsources {
			// Status based on active, errors, and last fetch
			var status string
			if !source.Active {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("○") // Paused/inactive
			} else if source.ErrorCount > 3 {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("●") // Has errors
			} else if source.LastFetched == nil || time.Since(*source.LastFetched) > 24*time.Hour {
				status = lipgloss.NewStyle().Foreground(theme.Orange).Render("●") // Stale
			} else {
				status = lipgloss.NewStyle().Foreground(theme.Green).Render("●") // Healthy
			}
			// Color the item count number
			countStr := lipgloss.NewStyle().Foreground(theme.White).Render(fmt.Sprintf("[%d]", source.UnreadCount))
			sources = append(sources, fmt.Sprintf("%s %s %s", status, source.Name, countStr))
		}
		sources = append(sources, "")
	}

	// YouTube Sources
	if ytsources, ok := sourcesByType["youtube"]; ok && len(ytsources) > 0 {
		header := fmt.Sprintf("YOUTUBE [%d]", len(ytsources))
		sources = append(sources, lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render(header))
		for _, source := range ytsources {
			// Status based on active, errors, and last fetch
			var status string
			if !source.Active {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("○") // Paused/inactive
			} else if source.ErrorCount > 3 {
				status = lipgloss.NewStyle().Foreground(theme.Red).Render("●") // Has errors
			} else if source.LastFetched == nil || time.Since(*source.LastFetched) > 24*time.Hour {
				status = lipgloss.NewStyle().Foreground(theme.Orange).Render("●") // Stale
			} else {
				status = lipgloss.NewStyle().Foreground(theme.Green).Render("●") // Healthy
			}
			// Color the item count number
			countStr := lipgloss.NewStyle().Foreground(theme.White).Render(fmt.Sprintf("[%d]", source.UnreadCount))
			sources = append(sources, fmt.Sprintf("%s %s %s", status, source.Name, countStr))
		}
	}

	sourcesSection := lipgloss.JoinVertical(
		lipgloss.Left,
		sourceHeader,
		"", // Add blank line after header
		strings.Join(sources, "\n"),
	)

	return lipgloss.JoinVertical(
		lipgloss.Left,
		statsSection,
		sourcesSection,
	)
}

func renderContentList(m Model, width, height int, theme StyleTheme) string {
	if len(m.items) == 0 {
		return renderEmptyState(theme)
	}

	var lines []string

	// Calculate visible items
	itemHeight := 2 // lines per item
	maxVisible := height / itemHeight

	startIdx := 0
	if m.cursor > maxVisible-3 {
		startIdx = m.cursor - maxVisible + 3
	}
	endIdx := startIdx + maxVisible
	if endIdx > len(m.items) {
		endIdx = len(m.items)
		if endIdx-startIdx < maxVisible {
			startIdx = max(0, endIdx-maxVisible)
		}
	}

	for i := startIdx; i < endIdx; i++ {
		item := m.items[i]

		// Priority indicator - star for favorited, checkmark for read items, dot for unread
		var priorityIndicator string
		if item.Favorited {
			// Heart for favorited items (overrides all other indicators) - gradient purple
			priorityIndicator = lipgloss.NewStyle().Foreground(lipgloss.Color("#9F4DFF")).Render("♥")
		} else if item.Read {
			// Use checkmark for read items in gray
			priorityIndicator = lipgloss.NewStyle().Foreground(theme.Gray).Render("✓")
		} else {
			// Use colored dot for unread items
			var dotColor lipgloss.Color
			switch item.Priority {
			case "high":
				dotColor = theme.Red
			case "medium":
				dotColor = theme.Orange
			case "low":
				dotColor = theme.Cyan
			default:
				// Default to gray if priority is empty or null
				dotColor = theme.Gray
			}
			priorityIndicator = lipgloss.NewStyle().Foreground(dotColor).Render("●")
		}

		// Selection indicator and flash effect
		selector := "  "
		titleColor := theme.White
		if i == m.cursor {
			selector = lipgloss.NewStyle().Foreground(theme.Cyan).Bold(true).Render("▸ ")
			titleColor = theme.Cyan
		}
		
		// Dim read items
		if item.Read {
			titleColor = theme.Gray // Dim the title for read items
		}

		// No separate star indicator needed - stars are now part of priority indicator
		
		// Format line 1: number, title
		titleText := truncate(item.Title, width-20) // Standard width since no separate star
		line1 := fmt.Sprintf("%s%s %2d. %s",
			selector,
			priorityIndicator,
			i+1,
			lipgloss.NewStyle().Foreground(titleColor).Render(titleText),
		)

		// Format line 2: metadata
		timeAgo := formatTime(time.Since(item.Published))
		metaStyle := lipgloss.NewStyle().Foreground(theme.Gray)

		// Use actual source information
		var sourceTypeStr string
		switch item.SourceType {
		case "reddit":
			sourceTypeStr = "reddit"
		case "youtube":
			sourceTypeStr = "youtube"
		case "rss":
			sourceTypeStr = "rss"
		default:
			sourceTypeStr = "web"
		}

		// Build metadata line with real data
		var line2 string
		tags := extractTags(item.Analysis)
		contentLength := extractContentLength(item.Analysis)

		// Build metadata components
		var metaParts []string

		// Source type (always show)
		metaParts = append(metaParts, metaStyle.Render(sourceTypeStr))

		// Source name if available
		if item.SourceName != "" {
			metaParts = append(metaParts, metaStyle.Render(item.SourceName))
		}

		// For RSS feeds, also show the domain
		if item.SourceType == "rss" {
			domain := extractDomain(item.URL)
			metaParts = append(metaParts, metaStyle.Render(domain))
		}

		// Time ago
		metaParts = append(metaParts, metaStyle.Render(timeAgo))

		// Reddit-specific metrics
		if item.SourceType == "reddit" {
			redditMetrics := extractRedditMetrics(item.Analysis)
			if redditMetrics.score > 0 || redditMetrics.numComments > 0 {
				// Show upvotes with arrow
				if redditMetrics.score > 0 {
					upvoteStr := fmt.Sprintf("↑%d", redditMetrics.score)
					metaParts = append(metaParts, lipgloss.NewStyle().Foreground(theme.Orange).Render(upvoteStr))
				}
				// Show comments
				if redditMetrics.numComments > 0 {
					commentStr := fmt.Sprintf("%dc", redditMetrics.numComments)
					metaParts = append(metaParts, metaStyle.Render(commentStr))
				}
			}
		}

		// YouTube-specific metrics
		if item.SourceType == "youtube" {
			youtubeMetrics := extractYouTubeMetrics(item.Analysis)
			if youtubeMetrics.viewCount > 0 {
				// Format view count without stupid emoji
				var viewStr string
				if youtubeMetrics.viewCount >= 1000000 {
					viewStr = fmt.Sprintf("%.1fM views", float64(youtubeMetrics.viewCount)/1000000)
				} else if youtubeMetrics.viewCount >= 1000 {
					viewStr = fmt.Sprintf("%.1fK views", float64(youtubeMetrics.viewCount)/1000)
				} else {
					viewStr = fmt.Sprintf("%d views", youtubeMetrics.viewCount)
				}
				metaParts = append(metaParts, metaStyle.Render(viewStr))
			}
			if youtubeMetrics.duration > 0 {
				// Format duration in minutes only (no seconds)
				durationStr := formatDurationMinutes(youtubeMetrics.duration)
				metaParts = append(metaParts, metaStyle.Render(durationStr))
			}
		}

		// Content length if available (more compact display) - only for RSS
		if item.SourceType == "rss" && contentLength > 0 {
			var lengthStr string
			if contentLength >= 10000 {
				lengthStr = fmt.Sprintf("%.1fk", float64(contentLength)/1000)
			} else if contentLength >= 1000 {
				lengthStr = fmt.Sprintf("%.1fk", float64(contentLength)/1000)
			} else {
				lengthStr = fmt.Sprintf("%d", contentLength)
			}
			metaParts = append(metaParts, metaStyle.Render(lengthStr+" chars"))
		}

		// Tags if available
		if tags != "" {
			metaParts = append(metaParts, tags)
		}

		line2 = "        " + strings.Join(metaParts, " | ")

		lines = append(lines, line1, line2)
	}

	return strings.Join(lines, "\n")
}

func renderLoading() string {
	return lipgloss.NewStyle().
		Foreground(CleanCyberTheme.Cyan).
		Bold(true).
		Render("Loading content...")
}

func renderError(err error) string {
	return lipgloss.NewStyle().
		Foreground(CleanCyberTheme.Red).
		Bold(true).
		Render(fmt.Sprintf("Error: %v", err))
}

func renderEmptyState(theme StyleTheme) string {
	return lipgloss.NewStyle().
		Foreground(theme.Gray).
		Italic(true).
		Render("No unread items. Press 'a' to add sources.")
}

func truncate(s string, max int) string {
	if len(s) <= max {
		return s
	}
	return s[:max-3] + "..."
}

func formatTime(d time.Duration) string {
	if d.Hours() < 1 {
		return fmt.Sprintf("%dm", int(d.Minutes()))
	}
	if d.Hours() < 24 {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return fmt.Sprintf("%dd", int(d.Hours()/24))
}

func extractDomain(url string) string {
	// Handle URL with or without protocol
	cleanURL := url
	if strings.HasPrefix(url, "http://") {
		cleanURL = strings.TrimPrefix(url, "http://")
	} else if strings.HasPrefix(url, "https://") {
		cleanURL = strings.TrimPrefix(url, "https://")
	}

	// Split by "/" to get domain part
	parts := strings.Split(cleanURL, "/")
	if len(parts) == 0 {
		return "web"
	}

	domain := parts[0]

	// Remove www. prefix
	domain = strings.TrimPrefix(domain, "www.")

	// Just return the domain as-is (e.g., "simonwillison.net", "blog.rust-lang.org")
	return domain
}

// extractContentLength extracts the content_length from the Analysis JSON metadata
func extractContentLength(analysis string) int {
	if analysis == "" {
		return 0
	}

	// Look for "content_length": <number>
	start := strings.Index(analysis, `"content_length":`)
	if start == -1 {
		return 0
	}

	// Move past the key
	start += len(`"content_length":`)

	// Find the next comma or closing brace
	end := strings.IndexAny(analysis[start:], ",}")
	if end == -1 {
		return 0
	}

	// Extract and parse the number
	numStr := strings.TrimSpace(analysis[start : start+end])
	var length int
	fmt.Sscanf(numStr, "%d", &length)
	return length
}

// extractTags extracts tags from the Analysis JSON field
func extractTags(analysis string) string {
	if analysis == "" {
		return ""
	}

	// Parse tags from the JSON-like analysis field
	theme := CleanCyberTheme
	tagStyle := lipgloss.NewStyle().Foreground(theme.Purple)

	// Look for entities array in the analysis JSON
	// Pattern: "entities": ["tag1", "tag2", ...]
	start := strings.Index(analysis, `"entities"`)
	if start == -1 {
		return ""
	}

	// Find the array start
	arrayStart := strings.Index(analysis[start:], "[")
	if arrayStart == -1 {
		return ""
	}
	start += arrayStart + 1

	// Find the array end
	arrayEnd := strings.Index(analysis[start:], "]")
	if arrayEnd == -1 {
		return ""
	}

	tagStr := analysis[start : start+arrayEnd]
	tagStr = strings.ReplaceAll(tagStr, `"`, "")
	tagStr = strings.ReplaceAll(tagStr, " ", "")

	if tagStr == "" {
		return ""
	}

	tagList := strings.Split(tagStr, ",")

	// Format first 2-3 tags in a single container
	tagsToShow := []string{}
	for i, tag := range tagList {
		if i >= 2 {
			break
		}
		if tag != "" {
			tagsToShow = append(tagsToShow, tag)
		}
	}

	if len(tagsToShow) == 0 {
		return ""
	}

	// Join tags with separator (no container for clean look)
	tagString := strings.Join(tagsToShow, " • ")
	return tagStyle.Render(tagString)
}

// extractAllTags extracts ALL tags from the Analysis JSON field (for reader modal)
func extractAllTags(analysis string) string {
	if analysis == "" {
		return ""
	}

	// Parse tags from the JSON-like analysis field
	theme := CleanCyberTheme
	tagStyle := lipgloss.NewStyle().Foreground(theme.Purple)

	// Look for entities array in the analysis JSON
	// Pattern: "entities": ["tag1", "tag2", ...]
	start := strings.Index(analysis, `"entities"`)
	if start == -1 {
		return ""
	}

	// Find the array start
	arrayStart := strings.Index(analysis[start:], "[")
	if arrayStart == -1 {
		return ""
	}
	start += arrayStart + 1

	// Find the array end
	arrayEnd := strings.Index(analysis[start:], "]")
	if arrayEnd == -1 {
		return ""
	}

	tagStr := analysis[start : start+arrayEnd]
	tagStr = strings.ReplaceAll(tagStr, `"`, "")
	tagStr = strings.ReplaceAll(tagStr, " ", "")

	if tagStr == "" {
		return ""
	}

	tagList := strings.Split(tagStr, ",")

	// Format ALL tags for reader modal in a single container
	tagsToShow := []string{}
	for _, tag := range tagList {
		if tag != "" {
			tagsToShow = append(tagsToShow, tag)
		}
	}

	if len(tagsToShow) == 0 {
		return ""
	}

	// Join all tags with separator (no container for clean look)
	tagString := strings.Join(tagsToShow, " • ")
	return tagStyle.Render(tagString)
}

func max(a, b int) int {
	if a > b {
		return a
	}
	return b
}

// getMemoryUsage returns current memory usage
func getMemoryUsage() string {
	var m runtime.MemStats
	runtime.ReadMemStats(&m)

	// Convert to MB
	allocMB := float64(m.Alloc) / 1024 / 1024

	if allocMB < 10 {
		return fmt.Sprintf("%.1f MB", allocMB)
	}
	return fmt.Sprintf("%.0f MB", allocMB)
}
