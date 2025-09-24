package ui

import (
	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis/internal/db"
)

// testModel creates a properly initialized Model for testing
func testModel() Model {
	return Model{
		width:    100,
		height:   40,
		loading:  false,
		view:     "list",
		priority: "all",
		viewport: viewport.New(100, 30),
		items:    []db.ContentItem{},
		cursor:   0,
	}
}

// testModelWithItems creates a Model with test items
func testModelWithItems(items []db.ContentItem) Model {
	m := testModel()
	m.items = items
	return m
}