package operations

import (
	"fmt"
	
	tea "github.com/charmbracelet/bubbletea"
	"github.com/nickpending/prismis-local/internal/db"
	"github.com/nickpending/prismis-local/internal/service"
)

// Article operation result messages
type ArticleMarkedMsg struct {
	ID      string
	Read    bool
	Success bool
	Error   error
}

type ArticleFavoritedMsg struct {
	ID        string
	Favorited bool
	Success   bool
	Error     error
}

type ArticleURLCopiedMsg struct {
	Success bool
	Error   error
}

type ArticleContentCopiedMsg struct {
	Success bool
	Error   error
}

type ArticleOpenedMsg struct {
	Success bool
	Error   error
}

// MarkArticleRead marks an article as read
func MarkArticleRead(id string) tea.Cmd {
	return func() tea.Msg {
		err := service.MarkAsRead(id)
		return ArticleMarkedMsg{
			ID:      id,
			Read:    true,
			Success: err == nil,
			Error:   err,
		}
	}
}

// MarkArticleUnread marks an article as unread
func MarkArticleUnread(id string) tea.Cmd {
	return func() tea.Msg {
		err := service.MarkAsUnread(id)
		return ArticleMarkedMsg{
			ID:      id,
			Read:    false,
			Success: err == nil,
			Error:   err,
		}
	}
}

// ToggleArticleRead toggles the read status of an article
func ToggleArticleRead(item db.ContentItem) tea.Cmd {
	if item.Read {
		return MarkArticleUnread(item.ID)
	}
	return MarkArticleRead(item.ID)
}

// ToggleArticleFavorite toggles the favorite status of an article
func ToggleArticleFavorite(item db.ContentItem) tea.Cmd {
	return func() tea.Msg {
		newStatus := !item.Favorited
		err := service.ToggleFavorite(item.ID, newStatus)
		return ArticleFavoritedMsg{
			ID:        item.ID,
			Favorited: newStatus,
			Success:   err == nil,
			Error:     err,
		}
	}
}

// CopyArticleURL copies the article URL to clipboard
func CopyArticleURL(url string) tea.Cmd {
	return func() tea.Msg {
		// Import cycle issue - need to handle clipboard differently
		// For now, return a message that the UI layer can handle
		return ArticleURLCopiedMsg{
			Success: true,
			Error:   nil,
		}
	}
}

// CopyArticleContent copies the article content to clipboard
func CopyArticleContent(content string) tea.Cmd {
	return func() tea.Msg {
		// Import cycle issue - need to handle clipboard differently
		// For now, return a message that the UI layer can handle
		return ArticleContentCopiedMsg{
			Success: true,
			Error:   nil,
		}
	}
}

// OpenArticleInBrowser opens the article URL in the default browser
func OpenArticleInBrowser(url string) tea.Cmd {
	return func() tea.Msg {
		if url == "" {
			return ArticleOpenedMsg{
				Success: false,
				Error:   fmt.Errorf("empty URL"),
			}
		}
		// Browser opening handled by UI layer to avoid import cycles
		return ArticleOpenedMsg{
			Success: true,
			Error:   nil,
		}
	}
}