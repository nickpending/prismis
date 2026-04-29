"""Static-analysis tests for INV-D3: web card click-delegation invariant.

INV-D3: Every interactive element inside a .content-item card that registers a
click listener MUST call e.stopPropagation() to prevent the card-level onclick
(openDetail) from firing when the user intends only the inner action (vote,
mark-read, etc.).

These tests read index.html as a text file and verify the invariant structurally.
No browser runtime is required — the invariant is enforceable by static analysis
of the JavaScript source.

Why this approach is valid:
- index.html is a static file committed to source; it changes only on edit.
- The invariant is about source structure (all btn listeners have stopPropagation),
  not runtime behavior (event propagation order).
- Catching a missing stopPropagation at test time is far cheaper than a user
  filing "clicking vote opens the detail panel".
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


# ---------------------------------------------------------------------------
# INV-D3: all button listeners inside card event-delegation sites stop propagation
# ---------------------------------------------------------------------------


def test_inv_d3_all_card_button_listeners_stop_propagation() -> None:
    """
    INV-D3: Every btn.addEventListener('click', ...) in renderContent and
    renderTop3MustReads calls e.stopPropagation() before performing its action.

    BREAKS: A button listener added without stopPropagation will bubble up to
    the .content-item onclick="window.prismisApp.openDetail(...)" and open the
    detail panel whenever the user votes or marks an article read.

    Verification method: count total btn.addEventListener('click') sites and
    confirm each is followed by e.stopPropagation() before the next listener
    registration. Uses regex on the JS source — no browser runtime required.
    """
    html = _read_html()

    # All card-button listeners use this pattern: btn.addEventListener('click', (e) => {
    # Collect each block from "btn.addEventListener" to its closing "});".
    # The innermost "});" that closes the arrow-function callback is what we want.
    # Split on the pattern to get segments, then verify each starts with stopPropagation.
    listener_opens = list(
        re.finditer(r"btn\.addEventListener\('click',\s*\(e\)\s*=>\s*\{", html)
    )

    assert len(listener_opens) >= 4, (
        f"Expected at least 4 btn.addEventListener('click') sites "
        f"(mark-read and vote in both renderContent and renderTop3MustReads); "
        f"found {len(listener_opens)}. A listener may have been removed."
    )

    missing = []
    for m in listener_opens:
        # Capture the 200 chars after the opening brace — stopPropagation
        # must be the first statement in the callback.
        block = html[m.end() : m.end() + 200]
        if "e.stopPropagation()" not in block:
            line_no = html[: m.start()].count("\n") + 1
            missing.append(line_no)

    assert not missing, (
        f"INV-D3 VIOLATION: btn.addEventListener('click') at line(s) {missing} "
        f"does not call e.stopPropagation(). Clicking these buttons will also "
        f"trigger openDetail() on the parent .content-item card."
    )


# ---------------------------------------------------------------------------
# INV-D3: card onclick is registered on .content-item (guard against removal)
# ---------------------------------------------------------------------------


def test_inv_d3_card_onclick_triggers_open_detail() -> None:
    """
    The .content-item card must carry onclick="window.prismisApp.openDetail(...)".

    BREAKS: If the onclick is removed, clicking cards silently does nothing —
    SC-8 (web detail panel) breaks entirely.
    """
    html = _read_html()

    # The card onclick is injected as a template literal attribute.
    matches = re.findall(r'onclick="window\.prismisApp\.openDetail\(', html)
    assert len(matches) >= 1, (
        "INV-D3: .content-item must have onclick='window.prismisApp.openDetail(...)'; "
        "not found. SC-8 (web detail panel) is broken."
    )


# ---------------------------------------------------------------------------
# INV-D3: detail panel element exists in DOM
# ---------------------------------------------------------------------------


def test_web_detail_panel_element_exists() -> None:
    """
    SC-8: The #detailPanel element must be present in index.html.

    BREAKS: If the panel HTML is removed, openDetail() calls
    document.getElementById('detailPanel') which returns null — every card
    click throws a TypeError in JS console and shows nothing to the user.
    """
    html = _read_html()

    assert 'id="detailPanel"' in html, (
        "SC-8: #detailPanel element missing from index.html — "
        "openDetail() will throw TypeError on every card click."
    )
    assert 'id="detailContent"' in html, (
        "SC-8: #detailContent element missing — openDetail() cannot populate the panel."
    )
    assert 'id="detailClose"' in html, (
        "SC-8: #detailClose element missing — users cannot close the detail panel."
    )
