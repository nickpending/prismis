package fabric

import (
	"os/exec"
	"sync"
)

// Detector checks if Fabric is available on the system
type Detector struct {
	available bool
	once      sync.Once
	mu        sync.RWMutex
}

// NewDetector creates a new Fabric detector
func NewDetector() *Detector {
	return &Detector{}
}

// Check detects if Fabric is available (cached after first check)
func (d *Detector) Check() bool {
	// Use sync.Once to ensure availability is checked exactly once
	d.once.Do(func() {
		// Check if fabric is in PATH
		_, err := exec.LookPath("fabric")

		d.mu.Lock()
		defer d.mu.Unlock()
		d.available = err == nil
	})

	// Return cached availability with read lock
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.available
}

// IsAvailable returns cached availability status without checking
func (d *Detector) IsAvailable() bool {
	d.mu.RLock()
	defer d.mu.RUnlock()
	return d.available
}

// Reset clears the cached status (useful for testing)
func (d *Detector) Reset() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.available = false
	// Note: sync.Once cannot be reset, so this creates a new instance for testing
	d.once = sync.Once{}
}
