package db

import (
	"database/sql"
	"fmt"
	"sync"

	_ "github.com/mattn/go-sqlite3"
)

var (
	// dbPool is the singleton database connection pool
	dbPool *sql.DB
	// dbOnce ensures the pool is created only once
	dbOnce sync.Once
	// dbErr stores any error from pool creation
	dbErr error
)

// GetDB returns the singleton database connection pool.
// It creates the pool on first call and reuses it for all subsequent calls.
// This ensures efficient connection reuse across all database operations.
func GetDB() (*sql.DB, error) {
	dbOnce.Do(func() {
		dbPath, err := getDBPath()
		if err != nil {
			dbErr = fmt.Errorf("failed to get database path: %w", err)
			return
		}

		// Open connection pool (doesn't actually connect yet)
		dbPool, err = sql.Open("sqlite3", dbPath)
		if err != nil {
			dbErr = fmt.Errorf("failed to open database: %w", err)
			return
		}

		// Configure connection pool settings
		dbPool.SetMaxOpenConns(25)   // Maximum number of open connections
		dbPool.SetMaxIdleConns(5)    // Maximum number of idle connections
		dbPool.SetConnMaxLifetime(0) // Connections don't expire (SQLite is local)

		// Set WAL mode and busy timeout on the pool
		// These pragmas will be inherited by all connections from the pool
		if _, err := dbPool.Exec("PRAGMA journal_mode=WAL"); err != nil {
			dbErr = fmt.Errorf("failed to set WAL mode: %w", err)
			// Close the pool if pragma fails
			dbPool.Close()
			dbPool = nil
			return
		}

		if _, err := dbPool.Exec("PRAGMA busy_timeout=5000"); err != nil {
			dbErr = fmt.Errorf("failed to set busy timeout: %w", err)
			// Close the pool if pragma fails
			dbPool.Close()
			dbPool = nil
			return
		}

		// Test the connection to ensure database is accessible
		if err := dbPool.Ping(); err != nil {
			dbErr = fmt.Errorf("failed to ping database: %w", err)
			dbPool.Close()
			dbPool = nil
			return
		}
	})

	if dbErr != nil {
		return nil, dbErr
	}

	return dbPool, nil
}

// CloseDB closes the singleton database connection pool.
// This should only be called when the application is shutting down.
func CloseDB() error {
	if dbPool != nil {
		err := dbPool.Close()
		dbPool = nil
		dbErr = nil
		// Reset the once so a new pool can be created
		dbOnce = sync.Once{}
		return err
	}
	return nil
}
