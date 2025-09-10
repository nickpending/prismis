package db

import (
	"fmt"
	"testing"
)

// TestIntegrationDemo demonstrates GetUnreadContent and MarkAsRead working together
func TestIntegrationDemo(t *testing.T) {
	// Setup
	dbPath := createTestDB(t)
	oldFunc := dbPathFunc
	dbPathFunc = func() (string, error) {
		return dbPath, nil
	}
	defer func() {
		dbPathFunc = oldFunc
	}()

	// Step 1: Get all unread content
	fmt.Println("Step 1: Getting all unread content...")
	unreadItems, err := GetUnreadContent()
	if err != nil {
		t.Fatalf("Failed to get unread content: %v", err)
	}
	fmt.Printf("Found %d unread items\n", len(unreadItems))

	// Step 2: Mark first item as read
	if len(unreadItems) > 0 {
		firstItem := unreadItems[0]
		fmt.Printf("\nStep 2: Marking '%s' (ID: %s) as read...\n", firstItem.Title, firstItem.ID)

		err = MarkAsRead(firstItem.ID)
		if err != nil {
			t.Fatalf("Failed to mark as read: %v", err)
		}
		fmt.Println("✅ Successfully marked as read")
	}

	// Step 3: Get unread content again to verify
	fmt.Println("\nStep 3: Getting unread content again...")
	updatedUnread, err := GetUnreadContent()
	if err != nil {
		t.Fatalf("Failed to get updated unread content: %v", err)
	}
	fmt.Printf("Now have %d unread items (was %d)\n", len(updatedUnread), len(unreadItems))

	// Verify count decreased by 1
	if len(updatedUnread) != len(unreadItems)-1 {
		t.Errorf("Expected %d unread items after marking one as read, got %d",
			len(unreadItems)-1, len(updatedUnread))
	}

	fmt.Println("\n✅ Integration test passed: GetUnreadContent and MarkAsRead work together!")
}
