package ui

import (
	"strings"
	"testing"

	"github.com/charmbracelet/bubbles/viewport"
	"github.com/nickpending/prismis/internal/db"
)

// TestReaderView tests that reader view displays content correctly
func TestReaderView(t *testing.T) {
	model := Model{
		items: []db.ContentItem{
			{
				ID:      "1",
				Title:   "Test Article",
				URL:     "https://example.com/article",
				Content: "This is the full content of the article.",
				Summary: "A brief summary.",
			},
		},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Debug: print what we actually get
	t.Logf("Reader output:\n%s", output)

	// Should show article content in reader view
	if !strings.Contains(output, "Test Article") {
		t.Error("Reader should show article title")
	}
	// The domain is shown for RSS feeds, not the full URL
	if !strings.Contains(output, "ARTICLE 1 of 1") {
		t.Error("Reader should show article position")
	}

	// Should show help hint in status bar
	if !strings.Contains(output, "Press ? for help") {
		t.Error("Reader should show help hint in status bar")
	}
}

// TestReaderEmptyContent tests reader with no content
func TestReaderEmptyContent(t *testing.T) {
	model := Model{
		items:    []db.ContentItem{},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Should handle empty state gracefully
	if output == "" {
		t.Error("Reader should show something even with no items")
	}
}

// TestParseMetadata_DeepExtraction verifies parseMetadata correctly parses deep_extraction.
// INV-D1: analysis["deep_extraction"] must be extracted into a typed *DeepExtraction with
// non-empty Synthesis — if this breaks, deep extraction becomes invisible to every reader user.
func TestParseMetadata_DeepExtraction(t *testing.T) {
	analysisJSON := `{
		"entities": ["Go", "LLM"],
		"deep_extraction": {
			"synthesis": "Counterintuitive finding: the obvious conclusion is wrong.",
			"quotables": ["Revenue grew 22% but profitability fell.", "Key verbatim quote."],
			"model": "gpt-5-mini",
			"extracted_at": "2026-04-27T12:00:00+00:00"
		}
	}`

	metadata := parseMetadata(analysisJSON)

	if metadata.DeepExtraction == nil {
		t.Fatal("DeepExtraction should not be nil when analysis contains deep_extraction")
	}
	if metadata.DeepExtraction.Synthesis == "" {
		t.Error("Synthesis must not be empty")
	}
	if metadata.DeepExtraction.Synthesis != "Counterintuitive finding: the obvious conclusion is wrong." {
		t.Errorf("Synthesis mismatch: got %q", metadata.DeepExtraction.Synthesis)
	}
	if len(metadata.DeepExtraction.Quotables) != 2 {
		t.Errorf("Expected 2 quotables, got %d", len(metadata.DeepExtraction.Quotables))
	}
	if metadata.DeepExtraction.Model != "gpt-5-mini" {
		t.Errorf("Model mismatch: got %q", metadata.DeepExtraction.Model)
	}
}

// TestParseMetadata_DeepExtractionAbsent verifies DeepExtraction is nil when the key is absent.
// INV-D1 nil guard: appendDeepExtractionSection must receive nil and omit the section entirely —
// an empty synthesis string would produce an empty "## Deep Synthesis" header in the reader.
func TestParseMetadata_DeepExtractionAbsent(t *testing.T) {
	analysisJSON := `{"entities": ["Go"], "quotes": ["some quote"]}`

	metadata := parseMetadata(analysisJSON)

	if metadata.DeepExtraction != nil {
		t.Errorf("DeepExtraction must be nil when deep_extraction key is absent, got %+v", metadata.DeepExtraction)
	}
}

// TestAppendDeepExtractionSection verifies the markdown output shape.
// BREAKS: if the ## / ### headers or > block-quote format changes, renderSimpleMarkdown
// won't apply cyan/gray styling, so the deep synthesis section loses visual distinction (SC-6).
func TestAppendDeepExtractionSection(t *testing.T) {
	de := &DeepExtraction{
		Synthesis: "Dense synthesis paragraph.",
		Quotables: []string{"First quotable.", "Second quotable."},
		Model:     "gpt-5-mini",
	}
	base := "Base content."

	result := appendDeepExtractionSection(base, de)

	if !strings.Contains(result, "Base content.") {
		t.Error("Base content must be preserved")
	}
	if !strings.Contains(result, "## Deep Synthesis") {
		t.Error("Must contain '## Deep Synthesis' header")
	}
	if !strings.Contains(result, "Dense synthesis paragraph.") {
		t.Error("Must contain synthesis text")
	}
	if !strings.Contains(result, "### Notable Lines") {
		t.Error("Must contain '### Notable Lines' sub-header when quotables present")
	}
	if !strings.Contains(result, "> First quotable.") {
		t.Error("Must render quotables as '> ' block quotes")
	}
	if !strings.Contains(result, "> Second quotable.") {
		t.Error("Must render second quotable as '> ' block quote")
	}
}

// TestAppendDeepExtractionSection_NilGuard verifies nil DeepExtraction returns content unchanged.
// BREAKS: if nil check is absent, every reader open panics with a nil-dereference on non-extracted items.
func TestAppendDeepExtractionSection_NilGuard(t *testing.T) {
	base := "Existing article content."
	result := appendDeepExtractionSection(base, nil)
	if result != base {
		t.Errorf("Nil guard: expected content unchanged, got %q", result)
	}
}

// TestAppendDeepExtractionSection_EmptyQuotables verifies synthesis renders without Notable Lines
// when quotables slice is empty — edge case from task spec.
func TestAppendDeepExtractionSection_EmptyQuotables(t *testing.T) {
	de := &DeepExtraction{
		Synthesis: "Only synthesis, no quotes.",
		Quotables: []string{},
		Model:     "gpt-5-mini",
	}

	result := appendDeepExtractionSection("Base.", de)

	if !strings.Contains(result, "## Deep Synthesis") {
		t.Error("Must contain Deep Synthesis header")
	}
	if strings.Contains(result, "### Notable Lines") {
		t.Error("Must NOT render Notable Lines header when quotables is empty")
	}
}

// TestReaderViewWithMetadata tests reader displays analysis metadata
func TestReaderViewWithMetadata(t *testing.T) {
	analysisJSON := `{
		"tools": ["Go", "Docker"],
		"topics": ["cloud-native", "microservices"],
		"urls": ["https://example.com/ref1"]
	}`

	model := Model{
		items: []db.ContentItem{
			{
				ID:       "1",
				Title:    "Technical Article",
				Content:  "Article about Go and Docker.",
				Analysis: analysisJSON,
			},
		},
		cursor:   0,
		view:     "reader",
		loading:  false,
		width:    100,
		height:   30,
		viewport: viewport.New(100, 30),
	}

	output := model.View()

	// Basic check that it renders without error
	if !strings.Contains(output, "Technical Article") {
		t.Error("Reader should display article with metadata")
	}
}
