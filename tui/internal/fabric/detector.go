package fabric

import (
	"os/exec"
	"sync"
)

// Detector checks if Fabric is available on the system
type Detector struct {
	available bool
	checked   bool
	mu        sync.RWMutex
}

// NewDetector creates a new Fabric detector
func NewDetector() *Detector {
	return &Detector{}
}

// Check detects if Fabric is available (cached after first check)
func (d *Detector) Check() bool {
	d.mu.RLock()
	if d.checked {
		defer d.mu.RUnlock()
		return d.available
	}
	d.mu.RUnlock()

	// Need to check - upgrade to write lock
	d.mu.Lock()
	defer d.mu.Unlock()

	// Double-check after acquiring write lock
	if d.checked {
		return d.available
	}

	// Check if fabric is in PATH
	_, err := exec.LookPath("fabric")
	d.available = err == nil
	d.checked = true

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
	d.checked = false
	d.available = false
}