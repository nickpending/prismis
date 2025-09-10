package db

import (
	"testing"
)

func TestConnectionPool(t *testing.T) {
	// Test that we can get a connection pool
	db1, err := GetDB()
	if err != nil {
		t.Fatalf("Failed to get first DB connection: %v", err)
	}
	if db1 == nil {
		t.Fatal("First DB connection is nil")
	}

	// Test that second call returns same pool
	db2, err := GetDB()
	if err != nil {
		t.Fatalf("Failed to get second DB connection: %v", err)
	}
	if db2 == nil {
		t.Fatal("Second DB connection is nil")
	}

	// Verify it's the same pool instance (singleton)
	if db1 != db2 {
		t.Error("GetDB() returned different instances - should be singleton")
	}

	// Test that pool is actually working with a simple query
	var result int
	err = db1.QueryRow("SELECT 1").Scan(&result)
	if err != nil {
		t.Fatalf("Failed to execute test query: %v", err)
	}
	if result != 1 {
		t.Errorf("Expected 1, got %d", result)
	}

	t.Log("✅ Connection pool singleton working correctly")
}
