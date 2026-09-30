"""Static-analysis tests for SC-6: web UI title-only marker.

readable-content work order, job 2, requires the web UI
(daemon/src/prismis_daemon/static/index.html) to show a lower-case "title only"
marker on a content card for an item whose analysis carries title_only: true, and
no marker at all otherwise. These tests read index.html as text and verify the
invariant structurally -- no browser runtime or running daemon is required.

Why this approach is valid (same reasoning as test_web_kind_unit.py):
- index.html is a static file committed to source; it changes only on edit.
- Each invariant here is about source structure (a conditional expression exists,
  a literal marker string is present), not runtime behavior (what a browser
  actually renders after a real fetch).
- SC-6's data source (the top-level title_only field GET /api/entries mirrors from
  analysis.title_only) is proven by
  daemon/tests/integration/test_api_title_only_integration.py; this file proves
  only that the page is wired to use it correctly.
"""

from __future__ import annotations

import re
from pathlib import Path


def _html_path() -> Path:
    """Locate index.html relative to this test file."""
    # daemon/tests/unit/ -> daemon/ -> prismis/daemon/src/prismis_daemon/static/
    repo_root = Path(__file__).parent.parent.parent
    return repo_root / "src" / "prismis_daemon" / "static" / "index.html"


def _read_html() -> str:
    path = _html_path()
    assert path.exists(), f"index.html not found at {path}"
    return path.read_text()


def test_title_only_marker_rendered_only_when_item_has_title_only() -> None:
    """
    SC-6: item cards render a "title only" marker derived from item.title_only,
    only when the item's flag is set.

    BREAKS: If the marker is unconditional, every item (including readable ones)
    shows it. If it doesn't key off item.title_only, it can't track the flag the
    API mirrors from analysis.title_only.
    """
    html = _read_html()

    match = re.search(r"const titleOnlyBadge = (.+?);\n", html, re.S)
    assert match, (
        "SC-6: expected a `const titleOnlyBadge = ...;` expression in "
        "renderContentItem, the same pattern kindBadge already uses"
    )

    expr = match.group(1)
    assert "item.title_only" in expr, (
        "SC-6: title-only marker must derive from item.title_only"
    )
    assert "?" in expr or "&&" in expr, (
        "SC-6: title-only marker expression must be conditional on "
        "item.title_only being true"
    )


def test_title_only_marker_text_is_lowercase_title_only() -> None:
    """
    SC-6: the marker's visible text is the literal lower-case string "title only".

    BREAKS: An upper-cased or differently-worded marker disagrees with rudy's
    rule that TUI/web markers render lower case, and with the criterion's exact
    wording.
    """
    html = _read_html()

    match = re.search(r"const titleOnlyBadge = (.+?);\n", html, re.S)
    assert match, "SC-6: expected a `const titleOnlyBadge = ...;` expression"

    expr = match.group(1)
    assert "title only" in expr, (
        "SC-6: the title-only marker must render the literal lower-case text "
        "'title only'"
    )
    assert "TITLE ONLY" not in expr and "Title Only" not in expr, (
        "SC-6: the title-only marker must be lower case, not upper- or title-cased"
    )


def test_title_only_marker_included_in_card_output() -> None:
    """
    SC-6: the titleOnlyBadge variable is actually spliced into the card's
    returned markup, not merely computed and discarded.

    BREAKS: Defining titleOnlyBadge but never interpolating it into the
    template literal returned by renderContentItem leaves the marker invisible
    even though the wiring "looks" complete.
    """
    html = _read_html()

    render_start = html.index("renderContentItem(item, isTop3 = false) {")
    render_end = html.index("\n            }", render_start)
    render_body = html[render_start:render_end]

    assert "${titleOnlyBadge}" in render_body, (
        "SC-6: renderContentItem must interpolate ${titleOnlyBadge} into the "
        "returned card markup"
    )
