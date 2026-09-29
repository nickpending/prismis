package ui

import (
	"strings"
	"testing"
)

// TestRenderHeaderLine_PlainHeader verifies a non-Overview header renders as a
// single styled "▸ Header" line followed by a blank spacer line, and consumes
// none of the following source lines.
func TestRenderHeaderLine_PlainHeader(t *testing.T) {
	lines := []string{"## Key Points", "- one", "- two"}

	out, consumed := renderHeaderLine("Key Points", lines, 0, 40, CleanCyberTheme)

	if consumed != 0 {
		t.Errorf("expected 0 lines consumed for a plain header, got %d", consumed)
	}
	if len(out) != 2 {
		t.Fatalf("expected 2 output lines (header + spacer), got %d: %q", len(out), out)
	}
	if !strings.Contains(out[0], "▸ Key Points") {
		t.Errorf("expected header line to contain '▸ Key Points', got %q", out[0])
	}
	if out[1] != "" {
		t.Errorf("expected second line to be a blank spacer, got %q", out[1])
	}
}

// TestRenderHeaderLine_Overview_CollectsUntilNextHeaderBulletOrBlank verifies the
// Overview special-case boxes every line up to (not including) the next header,
// bullet, or blank line, and reports how many source lines it consumed so the
// caller's skipLines bookkeeping stays correct.
func TestRenderHeaderLine_Overview_CollectsUntilNextHeaderBulletOrBlank(t *testing.T) {
	lines := []string{
		"## Overview",
		"first sentence.",
		"second sentence.",
		"", // blank - collection stops here
		"## Key Points",
	}

	out, consumed := renderHeaderLine("Overview", lines, 0, 40, CleanCyberTheme)

	if consumed != 2 {
		t.Errorf("expected 2 lines consumed (the two sentences before the blank), got %d", consumed)
	}
	if len(out) != 2 {
		t.Fatalf("expected 2 output lines (header + boxed content), got %d: %q", len(out), out)
	}
	if !strings.Contains(out[0], "▸ Overview") {
		t.Errorf("expected header line to contain '▸ Overview', got %q", out[0])
	}
	if !strings.Contains(out[1], "first sentence.") || !strings.Contains(out[1], "second sentence.") {
		t.Errorf("expected boxed content to contain both collected sentences, got %q", out[1])
	}
}

// TestRenderHeaderLine_Overview_StopsAtNextHashHeader verifies the lookahead
// also stops on a "#"-prefixed line (a header), not just blank/bullet lines,
// since a naive scan-to-blank-only would swallow a following section.
func TestRenderHeaderLine_Overview_StopsAtNextHashHeader(t *testing.T) {
	lines := []string{"## Overview", "only line", "# Next Section", "more"}

	_, consumed := renderHeaderLine("Overview", lines, 0, 40, CleanCyberTheme)

	if consumed != 1 {
		t.Errorf("expected lookahead to stop before '# Next Section', consuming 1 line, got %d", consumed)
	}
}

// TestRenderHeaderLine_Overview_NoContent verifies an Overview header with no
// following content (immediately hits a blank/bullet/header) renders just the
// header line, with no empty box appended.
func TestRenderHeaderLine_Overview_NoContent(t *testing.T) {
	lines := []string{"## Overview", ""}

	out, consumed := renderHeaderLine("Overview", lines, 0, 40, CleanCyberTheme)

	if consumed != 0 {
		t.Errorf("expected 0 lines consumed when Overview has no content, got %d", consumed)
	}
	if len(out) != 1 {
		t.Fatalf("expected only the header line when Overview has no content, got %d: %q", len(out), out)
	}
}

// TestRenderSimpleMarkdown_SingleAndDoubleHashHeadersMatch verifies the "# " and
// "## " branches of renderSimpleMarkdown, which both delegate to
// renderHeaderLine, produce identical output for the same header text - proving
// the consolidation didn't change either branch's behavior.
func TestRenderSimpleMarkdown_SingleAndDoubleHashHeadersMatch(t *testing.T) {
	doubleHash := renderSimpleMarkdown("## Summary\nSome body text.", 40)
	singleHash := renderSimpleMarkdown("# Summary\nSome body text.", 40)

	if doubleHash != singleHash {
		t.Errorf("expected '## ' and '# ' headers to render identically:\n## -> %q\n# -> %q", doubleHash, singleHash)
	}
	if !strings.Contains(doubleHash, "▸ Summary") {
		t.Errorf("expected rendered output to contain '▸ Summary', got %q", doubleHash)
	}
}

// TestRenderSimpleMarkdown_OverviewBoxedForBothHeaderLevels verifies the
// Overview special-case (content boxing) fires for both "## Overview" and
// "# Overview", and that content after the box (the next header) still renders
// - i.e. skipLines advances by exactly what was consumed, not more.
func TestRenderSimpleMarkdown_OverviewBoxedForBothHeaderLevels(t *testing.T) {
	for _, prefix := range []string{"##", "#"} {
		content := prefix + " Overview\nThe overview body.\n\n## Key Points\n- a point"
		out := renderSimpleMarkdown(content, 40)

		if !strings.Contains(out, "The overview body.") {
			t.Errorf("prefix %q: expected boxed overview content, got %q", prefix, out)
		}
		if !strings.Contains(out, "▸ Key Points") {
			t.Errorf("prefix %q: expected the following header to still render, got %q", prefix, out)
		}
		if !strings.Contains(out, "a point") {
			t.Errorf("prefix %q: expected the bullet after the header to still render, got %q", prefix, out)
		}
	}
}
