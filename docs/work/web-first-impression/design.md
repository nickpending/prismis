---
type: design
date: 2026-10-14
title: "Design: web-first-impression"
description: "Make the web view, prismis's lowest-friction entry point, read as a finished product on first visit: it explains itself, its status bar has hierarchy, its article detail works at phone width, and it says which version is running. All of it is proven in a real browser rather than by reading the HTML."
purpose: "The recorded design of the web-first-impression work order, read by its plan and its builders."
producer: cli:shape
---

# Design: web-first-impression

## Purpose

Make the web view, prismis's lowest-friction entry point, read as a finished product on first visit: it explains itself, its status bar has hierarchy, its article detail works at phone width, and it says which version is running. All of it is proven in a real browser rather than by reading the HTML.

## Form

1. Version endpoint. api.py gains GET /api/version behind verify_api_key, returning {success, message, data: {version: prismis_daemon.__version__}}, placed with the other authenticated GET routes. /health stays unchanged and unauthenticated: an unauthenticated version string tells a LAN scanner which known-vulnerable build to aim at (the daemon binds 0.0.0.0 when the operator opts into LAN, as <host> does), and the page cannot be used before a key anyway.

2. Identity banner. The existing .header gradient bar (index.html:26, markup at index.html:744) is the banner; no second strip is added. It gets slimmer padding. The subtitle line gains a version span filled from GET /api/version whenever the page has a key: in init, and in saveApiKey. It reads `v<version>` on success and `version unavailable` when the call fails, so a failure never looks like a blank. A (?) button sits at the header's right edge (margin-left: auto) with aria-label "Help".

3. Help modal. A new #helpModal reuses the overlay pattern of .api-key-modal (index.html:529): the overlay rules become a shared selector list for both modals, and .modal-content gets a wider, scrollable variant (max-width 560px, max-height 85vh, overflow-y auto). Its sections mirror help_modal.go's structure but list only what the web view has. KEYBOARD: ? toggles help, Esc closes the top panel. READING: click a summary to open the detail panel with deep synthesis and quotes; the title opens the original article. FEEDBACK: 👍/👎 teach the evaluator your preferences, and Mark Read. FILTERS & SORT: the kind select, sort by Priority/Newest/Unread, the last-24-hours window and the 30 s refresh. PRIORITY: what HIGH/MEDIUM/LOW mean, plus ✦ (shaped by your votes), xN sources (duplicates) and the interests-matched badge. TOP MUST-READS and AUDIO BRIEFING get one line each. A close button and a backdrop click both close it.

4. Key entry. saveApiKey (index.html:900) today calls loadContent and startAutoRefresh but not loadKinds, so a first-time visitor's kind select stays at All Kinds until a reload. It now loads kinds and the version too, the same three calls init makes when a key is already stored.

5. Keyboard. setupEventListeners (index.html:848) adds one document keydown handler. It ignores events with ctrl, meta or alt held, events whose target is an input, select or textarea, and every event while the API-key modal is visible. `?` toggles the help modal. `Escape` closes the help modal if it is open, otherwise the detail panel if it is open (closeDetail, index.html:1706). The header button calls the same open method.

6. Status bar (#41). Markup at index.html:756 keeps the ids statusInfo, kindSelect, sortSelect and refreshInfo, so the existing handlers and test_web_kind_unit.py hold. updateStatus (index.html:1573) renders the counts as separate spans built with textContent (items, then high, medium and low colored as their badge classes, then unread). updateOfflineStatus keeps its plain offline text. Each select gets a small uppercase label (KIND, SORT) in a labeled control group. The selects keep their look but get a visible :focus-visible ring instead of today's outline: none. refreshInfo is de-emphasized on its own line under the controls on wide screens. Padding tightens. At ≤768px the control groups wrap to full width.

7. Detail panel and synthesis (#40). The detail-panel CSS block (index.html:677-727) moves above the single @media (max-width: 768px) block, so phone rules can override it in source order. Desktop gains a .detail-synthesis h4 rule (the synthesis accent color, 12px uppercase, spaced like the h3 above it) and paragraph spacing inside .synthesis-body. overflow-wrap: anywhere goes on the detail title, summary, synthesis and quotes, and on feed titles and summaries, so a long URL or unbroken token wraps instead of widening the page. The media query gains detail-panel padding of 16px, tighter synthesis and quote padding, and full-width close-button placement. The dead .filter-tabs and .filter-tab rules (index.html:633-644) are deleted.

8. Browser proof. daemon/pyproject.toml's dev group adds playwright (the Python package; no package.json). A new daemon/tests/integration/test_web_browser_integration.py runs the real FastAPI app under uvicorn in a thread on 127.0.0.1, following the cli conftest's _run_live_daemon pattern (cli/tests/conftest.py:191), against the sealed XDG environment and a test_db seeded through add_new_content. The seed has high, medium and low items, one with a deep_extraction synthesis holding ### headings and a 200-character unbroken token, and quotes. Each browser context routes every request whose host is not 127.0.0.1 to abort, so Google Fonts and every other third party is never contacted. An init script stores TEST_API_KEY as prismis_api_key. Chromium launches with no skip guard: a missing browser fails with Playwright's own error naming `playwright install`. The tests: ? opens and closes help; the header button opens help; Esc closes help, then the detail panel, one per press; ? typed into the API-key input (no stored key) leaves help closed and lands in the input; the banner shows v + prismis_daemon.__version__ after a key is stored, and also after the key is entered through the modal, where the kind select is also populated; the status bar shows the seeded per-priority counts; the synthesis h4 computes to the accent color rather than the default; and at 375, 768 and 1024 px the feed, the open detail panel and the open help modal each have scrollWidth no greater than clientWidth. Integration tests assert that /api/version returns data.version equal to prismis_daemon.__version__ with the key and 403 without it, and that /health's data carries no version.

9. CI. ci.yml (ci.yml:71) gains a step after dependency install: read the locked Playwright version with `uv run --frozen python -c` over importlib.metadata in daemon/ into a step output, restore ~/.cache/ms-playwright from a cache keyed on that version, then run `uv run --frozen playwright install --with-deps chromium`. The key is derived from the installed version, as the embedding-model cache is derived from embeddings.py, so a Playwright bump fetches the matching browser and an unrelated lock change does not.

## Commitments

- GET /api/version returns data.version equal to the installed prismis-daemon version with a valid key and refuses without one; /health is unchanged and carries no version.
- Once a key is stored or entered, the web header shows v<version> from /api/version, and shows `version unavailable` when that call fails; entering the key through the modal also populates the kind select without a reload.
- ? toggles a help modal and Esc closes the help modal first, then the detail panel; neither key acts while typing in an input or select or while the API-key modal is open.
- The help modal lists only controls the web view has, and the (?) header button opens it.
- The status bar keeps its four element ids, shows per-priority counts in their badge colors, labels both selects, and gives them a visible keyboard focus ring.
- Synthesis h4 headings are styled, and at 375, 768 and 1024 px neither the feed, the open detail panel nor the open help modal overflows horizontally, even with a long unbroken token in the content.
- Each commitment above is asserted by a Playwright test against the real app and a real database, with every non-loopback request blocked, running in every verify.sh and CI run; a missing browser fails the run rather than skipping.

## Sacrifices

- Chromium only: mobile Safari and Firefox, which #40 names, are not exercised; layout is proven at phone widths in Chromium, not on a phone.
- No pixel or screenshot comparison: visual polish of the status bar and help modal is judged by the operator, and only structure, behavior, color of the h4 and overflow are asserted.
- #42's palette and visual identity stay as they are.
- Filter chips and an archived toggle named in #41 are not built.
- CI downloads Chromium and its system packages (cached by lock hash), and a first local verify needs a one-time `playwright install chromium`.
- The existing regex tests over index.html stay as they are and are not migrated to the browser.
- The help modal's contents are a hand-written list; a control added later needs its help line added by hand, as the TUI's help_modal.go does.
- The version is not shown before an API key is entered.

## Risk

If the browser tests are flaky (a 30 s auto-refresh re-render mid-assertion, slow Chromium start on CI), verify goes red on unrelated commits and the gate loses trust. Playwright's auto-waiting expectations and a seeded, static dataset bound this. If the Chromium install step breaks, CI fails loudly rather than passing. A styling regression in the help modal or status bar that keeps structure intact would pass the tests, which is the accepted cost of not doing pixel comparison. A copied live-server fixture could trip verify's duplication check, so the daemon-side fixture is written for the daemon's own conftest rather than pasted.

## Licensed by

README.md:105
