package service

import (
	"fmt"

	"github.com/nickpending/prismis-local/internal/api"
)

// ContentService handles content operations via API
type ContentService struct {
	client *api.APIClient
}

// globalContentService is a singleton instance
var globalContentService *ContentService

// initContentService initializes the global content service
func initContentService() error {
	if globalContentService != nil {
		return nil
	}

	client, err := api.NewClient()
	if err != nil {
		return fmt.Errorf("failed to create API client: %w", err)
	}

	globalContentService = &ContentService{
		client: client,
	}
	return nil
}

// MarkAsRead marks a content item as read via the API
func MarkAsRead(contentID string) error {
	if err := initContentService(); err != nil {
		return err
	}

	readStatus := true
	request := api.ContentUpdateRequest{
		Read: &readStatus,
	}

	_, err := globalContentService.client.UpdateContent(contentID, request)
	if err != nil {
		return fmt.Errorf("failed to mark as read: %w", err)
	}

	return nil
}

// MarkAsUnread marks a content item as unread via the API
func MarkAsUnread(contentID string) error {
	if err := initContentService(); err != nil {
		return err
	}

	readStatus := false
	request := api.ContentUpdateRequest{
		Read: &readStatus,
	}

	_, err := globalContentService.client.UpdateContent(contentID, request)
	if err != nil {
		return fmt.Errorf("failed to mark as unread: %w", err)
	}

	return nil
}

// ToggleFavorite toggles the favorite status of a content item via the API
func ToggleFavorite(contentID string, favorited bool) error {
	if err := initContentService(); err != nil {
		return err
	}

	request := api.ContentUpdateRequest{
		Favorited: &favorited,
	}

	_, err := globalContentService.client.UpdateContent(contentID, request)
	if err != nil {
		return fmt.Errorf("failed to toggle favorite: %w", err)
	}

	return nil
}