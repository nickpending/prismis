package ui

import (
	"fmt"
	"strings"
)

// Reddit metrics structure
type redditMetrics struct {
	score       int
	upvoteRatio float64
	numComments int
}

// extractRedditMetrics extracts Reddit-specific metrics from analysis JSON
func extractRedditMetrics(analysis string) redditMetrics {
	if analysis == "" {
		return redditMetrics{}
	}

	// Look for "metrics" object in analysis
	metricsStart := strings.Index(analysis, `"metrics":`)
	if metricsStart == -1 {
		return redditMetrics{}
	}

	// Find the object boundaries
	objectStart := strings.Index(analysis[metricsStart:], "{")
	if objectStart == -1 {
		return redditMetrics{}
	}
	objectStart += metricsStart

	objectEnd := strings.Index(analysis[objectStart:], "}")
	if objectEnd == -1 {
		return redditMetrics{}
	}

	metricsStr := analysis[objectStart : objectStart+objectEnd+1]

	var metrics redditMetrics

	// Extract score
	if scoreIdx := strings.Index(metricsStr, `"score":`); scoreIdx != -1 {
		start := scoreIdx + len(`"score":`)
		end := strings.IndexAny(metricsStr[start:], ",}")
		if end != -1 {
			scoreStr := strings.TrimSpace(metricsStr[start : start+end])
			fmt.Sscanf(scoreStr, "%d", &metrics.score)
		}
	}

	// Extract num_comments
	if commentsIdx := strings.Index(metricsStr, `"num_comments":`); commentsIdx != -1 {
		start := commentsIdx + len(`"num_comments":`)
		end := strings.IndexAny(metricsStr[start:], ",}")
		if end != -1 {
			commentsStr := strings.TrimSpace(metricsStr[start : start+end])
			fmt.Sscanf(commentsStr, "%d", &metrics.numComments)
		}
	}

	// Extract upvote_ratio
	if ratioIdx := strings.Index(metricsStr, `"upvote_ratio":`); ratioIdx != -1 {
		start := ratioIdx + len(`"upvote_ratio":`)
		end := strings.IndexAny(metricsStr[start:], ",}")
		if end != -1 {
			ratioStr := strings.TrimSpace(metricsStr[start : start+end])
			fmt.Sscanf(ratioStr, "%f", &metrics.upvoteRatio)
		}
	}

	return metrics
}

// YouTube metrics structure
type youTubeMetrics struct {
	viewCount int
	duration  int // in seconds
}

// extractYouTubeMetrics extracts YouTube-specific metrics from analysis JSON
func extractYouTubeMetrics(analysis string) youTubeMetrics {
	if analysis == "" {
		return youTubeMetrics{}
	}

	// Look for "metrics" object in analysis
	metricsStart := strings.Index(analysis, `"metrics":`)
	if metricsStart == -1 {
		return youTubeMetrics{}
	}

	// Find the object boundaries
	objectStart := strings.Index(analysis[metricsStart:], "{")
	if objectStart == -1 {
		return youTubeMetrics{}
	}
	objectStart += metricsStart

	objectEnd := strings.Index(analysis[objectStart:], "}")
	if objectEnd == -1 {
		return youTubeMetrics{}
	}

	metricsStr := analysis[objectStart : objectStart+objectEnd+1]

	var metrics youTubeMetrics

	// Extract view_count
	if viewIdx := strings.Index(metricsStr, `"view_count":`); viewIdx != -1 {
		start := viewIdx + len(`"view_count":`)
		end := strings.IndexAny(metricsStr[start:], ",}")
		if end != -1 {
			viewStr := strings.TrimSpace(metricsStr[start : start+end])
			fmt.Sscanf(viewStr, "%d", &metrics.viewCount)
		}
	}

	// Extract duration
	if durationIdx := strings.Index(metricsStr, `"duration":`); durationIdx != -1 {
		start := durationIdx + len(`"duration":`)
		end := strings.IndexAny(metricsStr[start:], ",}")
		if end != -1 {
			durationStr := strings.TrimSpace(metricsStr[start : start+end])
			fmt.Sscanf(durationStr, "%d", &metrics.duration)
		}
	}

	return metrics
}

// formatDuration formats seconds into HH:MM:SS or MM:SS
func formatDuration(seconds int) string {
	hours := seconds / 3600
	minutes := (seconds % 3600) / 60
	secs := seconds % 60

	if hours > 0 {
		return fmt.Sprintf("%d:%02d:%02d", hours, minutes, secs)
	}
	return fmt.Sprintf("%d:%02d", minutes, secs)
}

// formatDurationMinutes formats seconds (as int or float) into minutes only
func formatDurationMinutes(seconds interface{}) string {
	var totalSeconds int

	// Handle both int and float64 from JSON
	switch v := seconds.(type) {
	case int:
		totalSeconds = v
	case float64:
		totalSeconds = int(v)
	default:
		return "0m"
	}

	if totalSeconds < 60 {
		return "1m" // Round up sub-minute videos to 1m
	}

	minutes := totalSeconds / 60
	if minutes >= 60 {
		hours := minutes / 60
		remainingMinutes := minutes % 60
		return fmt.Sprintf("%dh%dm", hours, remainingMinutes)
	}

	return fmt.Sprintf("%dm", minutes)
}
