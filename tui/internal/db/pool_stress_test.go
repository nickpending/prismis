package db

import (
	"sync"
	"testing"
	"time"
)

func TestPoolSingletonStress(t *testing.T) {
	/*
		CONFIDENCE: Pool singleton remains stable under load
		THRESHOLD: No panics, all operations complete
	*/

	// Reset singleton for clean test
	dbOnce = sync.Once{}
	dbPool = nil
	dbErr = nil

	const numGoroutines = 50
	const opsPerGoroutine = 20

	var wg sync.WaitGroup
	errors := make(chan error, numGoroutines*opsPerGoroutine)
	poolInstances := make(chan interface{}, numGoroutines)

	// Launch many goroutines simultaneously
	for i := 0; i < numGoroutines; i++ {
		wg.Add(1)
		go func(id int) {
			defer wg.Done()

			// Each goroutine tries to get the pool
			db, err := GetDB()
			if err != nil {
				errors <- err
				return
			}

			// Record the pool instance we got
			poolInstances <- db

			// Perform multiple operations
			for j := 0; j < opsPerGoroutine; j++ {
				// Simple query to verify pool works
				var result int
				err := db.QueryRow("SELECT 1").Scan(&result)
				if err != nil {
					errors <- err
					return
				}

				// Small delay to simulate real work
				time.Sleep(time.Microsecond * 100)
			}
		}(i)
	}

	// Wait for all goroutines
	wg.Wait()
	close(errors)
	close(poolInstances)

	// Check for errors
	var errorCount int
	for err := range errors {
		t.Errorf("Operation failed: %v", err)
		errorCount++
	}

	if errorCount > 0 {
		t.Fatalf("Had %d errors during stress test", errorCount)
	}

	// Verify singleton property - all should get same instance
	var firstInstance interface{}
	instanceCount := 0
	for instance := range poolInstances {
		instanceCount++
		if firstInstance == nil {
			firstInstance = instance
		} else if instance != firstInstance {
			t.Error("GetDB returned different instances - singleton violated")
		}
	}

	if instanceCount != numGoroutines {
		t.Errorf("Expected %d pool instances, got %d", numGoroutines, instanceCount)
	}

	// Verify pool is still healthy after stress
	db, err := GetDB()
	if err != nil {
		t.Fatalf("Pool unhealthy after stress: %v", err)
	}

	// Can still execute queries
	var result int
	err = db.QueryRow("SELECT 42").Scan(&result)
	if err != nil {
		t.Fatalf("Query failed after stress: %v", err)
	}

	if result != 42 {
		t.Errorf("Expected 42, got %d", result)
	}

	t.Logf("✅ Pool singleton survived %d goroutines with %d operations each",
		numGoroutines, opsPerGoroutine)
}
