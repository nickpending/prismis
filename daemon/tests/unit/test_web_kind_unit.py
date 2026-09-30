"""Static-analysis tests for SC-2: web UI kind badge and filter.

gh #84 (web-kind) requires the web UI (daemon/src/prismis_daemon/static/index.html)
to show each item's content kind and let the reader filter by it. These tests read
index.html as text and verify the invariant structurally -- no browser runtime or
running daemon is required.

Why this approach is valid:
- index.html is a static file committed to source; it changes only on edit.
- Each invariant here is about source structure (a conditional expression exists,
  a query param is set, a literal is or is not present), not runtime behavior
  (what a browser actually renders after a real fetch).
- SC-1's own endpoint (GET /api/kinds) is proven by
  daemon/tests/integration/test_api_kinds_integration.py; this file proves only
  that the page is wired to use it correctly.
"""

from __future__ import annotations

import re
from pathlib import Path

# The ten kinds declared once in kind_classifier.KINDS (gh #77). The work order's
# stakes forbid a second hardcoded copy of this list anywhere the kind filter
# reaches -- including the web page -- because a second copy drifts from the first.
KIND_NAMES = [
    "release",
    "experience",
    "question",
    "analysis",
    "news",
    "incident",
    "research",
    "vulnerability",
    "humor",
    "tutorial",
]


def _html_path() -> Path:
    """Locate index.html relative to this test file."""
    # daemon/tests/unit/ -> daemon/ -> prismis/daemon/src/prismis_daemon/static/
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "src" / "prismis_daemon" / "static" / "index.html"


def _read_html() -> str:
    path = _html_path()
    assert path.exists(), f"index.html not found at {path}"
    return path.read_text()


def _method_body(html: str, start_signature: str, end_signature: str) -> str:
    """Slice out the source between two textual anchors, in source order."""
    start = html.index(start_signature)
    end = html.index(end_signature, start)
    assert end > start, f"{end_signature!r} must appear after {start_signature!r}"
    return html[start:end]


# ---------------------------------------------------------------------------
# SC-2: item cards render a kind badge from item.kind, lower case, only when present
# ---------------------------------------------------------------------------


def test_kind_badge_rendered_only_when_item_has_kind_and_lowercased() -> None:
    """
    SC-2: item cards render a kind badge from item.kind in lower case, only
    when the item has a kind.

    BREAKS: If the badge is unconditional, unclassified items show a badge for
    an empty/undefined kind. If it isn't lower-cased, it disagrees with the
    TUI's own kind rendering convention.
    """
    html = _read_html()

    assert "kind-badge" in html, "SC-2: no kind-badge class found; kind badge not rendered"

    match = re.search(r"const kindBadge = (.+?);\n", html, re.S)
    assert match, "SC-2: expected a `const kindBadge = ...;` expression in renderContentItem"

    expr = match.group(1)
    assert "item.kind" in expr, "SC-2: kind badge must derive from item.kind"
    assert "toLowerCase" in expr, "SC-2: kind badge must render item.kind in lower case"
    assert "?" in expr or "&&" in expr, (
        "SC-2: kind badge expression must be conditional on item.kind being present"
    )


# ---------------------------------------------------------------------------
# SC-2: a kind control, populated from GET /api/kinds, with an all-kinds default
# ---------------------------------------------------------------------------


def test_kind_select_exists_and_has_all_kinds_default() -> None:
    """
    SC-2: a kind control is populated from the /api/kinds response with an
    all-kinds default.

    BREAKS: Without a default option shipped in the markup, the control is
    empty (and unusable) until the /api/kinds fetch resolves.
    """
    html = _read_html()

    assert 'id="kindSelect"' in html, "SC-2: kind <select> control missing"

    match = re.search(
        r'<select id="kindSelect"[^>]*>\s*<option value="">([^<]+)</option>', html
    )
    assert match, 'SC-2: kindSelect must ship an all-kinds default <option value="">'


def test_kind_select_populated_from_api_kinds_response() -> None:
    """
    SC-2: the kind control's choices come from GET /api/kinds, not a second
    hardcoded list.

    BREAKS: A hardcoded <option> list would drift from kind_classifier.KINDS
    the moment a kind is added, renamed or removed there (the exact defect the
    work order's stakes call out).
    """
    html = _read_html()

    assert "/api/kinds" in html, "SC-2: page must call GET /api/kinds"
    assert re.search(r"data\.data\.kinds", html), (
        "SC-2: must read the kind choices out of the documented "
        "{success, data: {kinds: [...]}} response shape /api/kinds returns"
    )


# ---------------------------------------------------------------------------
# SC-2: choosing a kind goes to the server, not a client-side filter
# ---------------------------------------------------------------------------


def test_choosing_kind_adds_query_param_not_browser_filter() -> None:
    """
    SC-2: choosing a kind adds kind=<kind> to the /api/entries request rather
    than filtering loaded items in the browser.

    BREAKS: Filtering `this.content` client-side repeats the filter-after-limit
    defect search-kind-filter already fixed -- the page only ever loaded 100
    items, so a client-side filter can hide items the server would have
    returned for that kind beyond the page's limit.
    """
    html = _read_html()

    assert not re.search(r"this\.content\.filter\([^)]*\.kind", html), (
        "SC-2 VIOLATION: filtering loaded items by kind in the browser repeats "
        "the filter-after-limit defect search-kind-filter fixed"
    )

    load_content = _method_body(html, "async loadContent()", "handleOfflineState(error)")
    assert re.search(r"params\.set\(\s*['\"]kind['\"]\s*,\s*this\.kindFilter\s*\)", load_content), (
        "SC-2: choosing a kind must add kind=<kind> to the /api/entries request "
        "via this.kindFilter, inside loadContent"
    )


# ---------------------------------------------------------------------------
# SC-2: the chosen kind survives the 30s auto-refresh
# ---------------------------------------------------------------------------


def test_kind_filter_persists_across_refresh() -> None:
    """
    SC-2: the chosen kind persists across the 30s refresh.

    BREAKS: startAutoRefresh's setInterval calls loadContent() on a timer with
    no arguments; if the chosen kind weren't held on `this` and re-read inside
    loadContent, the filter would silently reset to all-kinds every 30s.
    """
    html = _read_html()

    assert "prismis_kind" in html, "SC-2: chosen kind must be persisted (localStorage key)"

    load_content = _method_body(html, "async loadContent()", "handleOfflineState(error)")
    assert "this.kindFilter" in load_content, (
        "SC-2: loadContent must read this.kindFilter so the chosen kind is sent "
        "on the initial load AND every 30s auto-refresh call"
    )


# ---------------------------------------------------------------------------
# SC-2: no literal from the ten kind names appears in the page
# ---------------------------------------------------------------------------


def test_no_kind_name_hardcoded_as_a_literal() -> None:
    """
    SC-2: none of the ten kind names appears as a literal in the page.

    BREAKS: A hardcoded kind (e.g. a <option value="release">) gives the kind
    names a second home in the web UI that can drift from kind_classifier.KINDS,
    the exact defect the work order's stakes forbid. Matches only quoted string
    literals -- "analysis" is also a pre-existing field name (item.analysis)
    that must keep appearing unquoted.
    """
    html = _read_html()

    for kind in KIND_NAMES:
        pattern = re.compile(r"""['"]""" + re.escape(kind) + r"""['"]""", re.IGNORECASE)
        found = pattern.search(html)
        assert not found, (
            f"SC-2 VIOLATION: '{kind}' appears as a quoted literal in index.html "
            f"at offset {found.start() if found else -1}; kind choices must come "
            f"from GET /api/kinds, not a second hardcoded list"
        )
