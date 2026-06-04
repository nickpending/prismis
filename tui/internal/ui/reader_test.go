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
// INV-D1 nil guard: appendSynthesisSection must receive nil and omit the section entirely —
// an empty synthesis string would produce an empty "## Deep Synthesis" header in the reader.
func TestParseMetadata_DeepExtractionAbsent(t *testing.T) {
	analysisJSON := `{"entities": ["Go"], "quotes": ["some quote"]}`

	metadata := parseMetadata(analysisJSON)

	if metadata.DeepExtraction != nil {
		t.Errorf("DeepExtraction must be nil when deep_extraction key is absent, got %+v", metadata.DeepExtraction)
	}
}

// TestAppendSynthesisSection verifies the synthesis prose renders WITHOUT quotables —
// quotables now live in the unified Quotes section, not under Deep Synthesis.
// BREAKS: if Notable Lines reappear here, the reader shows two quote blocks again.
func TestAppendSynthesisSection(t *testing.T) {
	de := &DeepExtraction{
		Synthesis: "Dense synthesis paragraph.",
		Quotables: []string{"First quotable.", "Second quotable."},
		Model:     "gpt-5-mini",
	}
	base := "Base content."

	result := appendSynthesisSection(base, de)

	if !strings.Contains(result, "Base content.") {
		t.Error("Base content must be preserved")
	}
	if !strings.Contains(result, "## Deep Synthesis") {
		t.Error("Must contain '## Deep Synthesis' header")
	}
	if !strings.Contains(result, "Dense synthesis paragraph.") {
		t.Error("Must contain synthesis text")
	}
	if strings.Contains(result, "Notable Lines") {
		t.Error("Synthesis section must NOT render Notable Lines; quotes are unified separately")
	}
	if strings.Contains(result, "First quotable.") {
		t.Error("Synthesis section must NOT include quotables")
	}
}

// TestAppendSynthesisSection_NilGuard verifies nil or empty-synthesis returns content unchanged.
// BREAKS: if the guard is absent, non-extracted items panic or render an empty header.
func TestAppendSynthesisSection_NilGuard(t *testing.T) {
	base := "Existing article content."
	if result := appendSynthesisSection(base, nil); result != base {
		t.Errorf("Nil guard: expected content unchanged, got %q", result)
	}
	empty := &DeepExtraction{Synthesis: "", Quotables: []string{"x"}}
	if result := appendSynthesisSection(base, empty); result != base {
		t.Errorf("Empty synthesis: expected content unchanged, got %q", result)
	}
}

// TestUnifiedQuotes verifies light quotes + deep quotables merge into ONE deduped "## Quotes"
// section. BREAKS: the old separate "Key Quotes" / "Notable Lines" headers returning.
func TestUnifiedQuotes(t *testing.T) {
	de := &DeepExtraction{
		Synthesis: "S.",
		Quotables: []string{"Deep line.", "Shared quote."},
	}
	light := []string{"Light quote.", "Shared quote."}

	quotes := combineQuotes(light, de)

	// Dedup keeps light-first order; "Shared quote." appears once.
	if len(quotes) != 3 {
		t.Fatalf("expected 3 deduped quotes, got %d: %v", len(quotes), quotes)
	}
	if quotes[0] != "Light quote." || quotes[1] != "Shared quote." || quotes[2] != "Deep line." {
		t.Errorf("unexpected order/dedup: %v", quotes)
	}

	result := appendQuotesSection("Base.", quotes)
	if !strings.Contains(result, "## Quotes") {
		t.Error("Must render a single '## Quotes' header")
	}
	if strings.Contains(result, "Key Quotes") || strings.Contains(result, "Notable Lines") {
		t.Error("Must NOT render the old separate 'Key Quotes' / 'Notable Lines' headers")
	}
	if !strings.Contains(result, "> Light quote.") || !strings.Contains(result, "> Deep line.") {
		t.Error("Must render combined quotes as '> ' block quotes")
	}
	if appendQuotesSection("Base.", nil) != "Base." {
		t.Error("Empty quotes must leave content unchanged")
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
