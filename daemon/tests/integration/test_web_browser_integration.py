"""Browser tests for the web view: the real page, served by the real app, driven in Chromium.

The app is `prismis_daemon.api.app` under uvicorn on 127.0.0.1 (the `live_web_app` fixture
in conftest), against the sealed XDG environment and a test database seeded through
`Storage`. Nothing the daemon owns is stood in for.

Every request to a host other than 127.0.0.1 is aborted at the browser context, Google
Fonts included, so no test ever reaches a third party.

Chromium is launched with no skip guard: when it is not installed, Playwright's own error
names `playwright install chromium` and the run fails.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import pytest
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Locator,
    Page,
    Playwright,
    Route,
    expect,
    sync_playwright,
)

from conftest import TEST_API_KEY, add_new_content
from prismis_daemon import __version__
from prismis_daemon.models import ContentItem
from prismis_daemon.storage import Storage

# Seeded feed: known per-priority counts, some read.
HIGH, MEDIUM, LOW = 3, 2, 1
TOTAL = HIGH + MEDIUM + LOW
UNREAD = 4

# One unbroken token per field. 200 characters, no break opportunity.
TOKEN = {
    "title": "T" * 200,
    "summary": "S" * 200,
    "synthesis": "Y" * 200,
    "quote": "Q" * 200,
}

# Badge colors from the stylesheet (.badge-high, .badge-medium, .badge-low) and the
# synthesis accent (.detail-synthesis h3).
COLOR_HIGH = "rgb(255, 0, 102)"
COLOR_MEDIUM = "rgb(255, 136, 0)"
COLOR_LOW = "rgb(102, 102, 102)"
COLOR_SYNTHESIS = "rgb(159, 77, 255)"

WIDTHS = [375, 768, 1024]

# Titles that share nothing, so the feed's duplicate grouping (title similarity) keeps all
# six items as separate rows and the status bar counts equal the seeded counts.
TITLES = {
    "long": "Long",
    "h2": "Zig compiler gains incremental builds",
    "h3": "Quarterly semiconductor export figures published",
    "m1": "Garden birds migrate earlier each spring",
    "m2": "New sourdough hydration study released",
    "l1": "Local library extends weekend opening hours",
}


@pytest.fixture(scope="module")
def playwright_instance() -> Iterator[Playwright]:
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(playwright_instance: Playwright) -> Iterator[Browser]:
    # No skip guard: a missing Chromium raises Playwright's error naming the install command.
    b = playwright_instance.chromium.launch()
    yield b
    b.close()


@pytest.fixture
def seeded_ids(live_web_app: str, test_db: Path) -> dict[str, str]:
    """Seed the daemon's database through Storage; returns the id of each named item."""
    storage = Storage(test_db)
    source_id = storage.add_source("https://example.com/feed.xml", "rss", "Browser Feed")
    now = datetime.now(UTC)
    ids: dict[str, str] = {}

    def add(
        name: str,
        priority: str,
        *,
        read: bool = False,
        title: str | None = None,
        summary: str | None = None,
        analysis: dict[str, Any] | None = None,
    ) -> None:
        ids[name] = add_new_content(
            storage,
            ContentItem(
                source_id=source_id,
                external_id=f"browser-{name}",
                title=title or TITLES[name],
                url=f"https://example.com/{name}",
                content=f"Body of {name}",
                summary=summary or f"{TITLES[name]}: a short summary.",
                analysis=analysis or {},
                priority=priority,
                published_at=now,
                fetched_at=now,
                read=read,
            ),
        )

    add(
        "long",
        "high",
        title=f"Long {TOKEN['title']}",
        summary=f"Long {TOKEN['summary']}",
        analysis={
            "kind": "release",
            "quotes": [f"Quote {TOKEN['quote']}"],
            "deep_extraction": {
                "synthesis": (
                    f"### Why it matters\nBody {TOKEN['synthesis']}\n\n"
                    "### Caveats\nSecond paragraph.\n\nThird paragraph."
                ),
                "quotables": ["A second, ordinary quote."],
            },
        },
    )
    add("h2", "high", read=True, analysis={"kind": "tutorial"})
    add("h3", "high", analysis={"matched_interests": ["a", "b"]})
    add("m1", "medium", analysis={"preference_influenced": True})
    add("m2", "medium", read=True)
    add("l1", "low")
    return ids


class PageFactory:
    """Opens pages in fresh browser contexts that abort every non-loopback request."""

    def __init__(self, browser: Browser, base_url: str) -> None:
        self._browser = browser
        self._base_url = base_url
        self._contexts: list[BrowserContext] = []
        self.blocked_hosts: list[str] = []
        self.failed_requests: dict[str, str] = {}

    def _guard(self, route: Route) -> None:
        host = urlparse(route.request.url).hostname
        if host == "127.0.0.1":
            route.continue_()
        else:
            self.blocked_hosts.append(str(host))
            route.abort()

    def open(self, *, key: str | None = TEST_API_KEY, width: int = 1024) -> Page:
        context = self._browser.new_context(viewport={"width": width, "height": 800})
        self._contexts.append(context)
        context.route("**/*", self._guard)
        context.on(
            "requestfailed",
            lambda request: self.failed_requests.__setitem__(
                str(urlparse(request.url).hostname), str(request.failure)
            ),
        )
        if key is not None:
            context.add_init_script(
                f"localStorage.setItem('prismis_api_key', {key!r});"
            )
        page = context.new_page()
        page.goto(self._base_url + "/")
        return page

    def close(self) -> None:
        for context in self._contexts:
            context.close()


@pytest.fixture
def pages(
    browser: Browser, live_web_app: str, seeded_ids: dict[str, str]
) -> Iterator[PageFactory]:
    factory = PageFactory(browser, live_web_app)
    yield factory
    factory.close()


def _loaded(page: Page) -> None:
    """Wait until the feed has rendered: the status bar shows the seeded total."""
    expect(page.locator("#statusInfo .status-total")).to_have_text(f"{TOTAL} items")


def _summary_of(page: Page, item_id: str) -> Locator:
    return page.locator(f'.item-summary[data-item-id="{item_id}"]').first


# --------------------------------------------------------------------------- guard


def test_non_loopback_requests_are_aborted(pages: PageFactory) -> None:
    """
    INVARIANT: no browser test reaches a host other than 127.0.0.1
    BREAKS: a test that depends on Google Fonts or any third party is flaky offline
            and leaks the test run to the network
    NOTE: index.html links Google Fonts, so the guard has a real third-party request to
          stop. The request must have failed with the abort's own error: a request that
          reached the network would succeed, or fail with a DNS error on an offline host.
    """
    page = pages.open()
    _loaded(page)
    for _ in range(100):  # the failure event can trail the page's own render
        if "fonts.googleapis.com" in pages.failed_requests:
            break
        page.wait_for_timeout(50)
    assert "fonts.googleapis.com" in pages.blocked_hosts
    assert pages.failed_requests["fonts.googleapis.com"] == "net::ERR_FAILED"


# --------------------------------------------------------------------------- version


def test_header_shows_installed_version(pages: PageFactory) -> None:
    """
    INVARIANT: with a stored key the header reads exactly v<version> from /api/version
    BREAKS: the operator cannot tell which build the page is talking to
    """
    page = pages.open()
    expect(page.locator("#versionInfo")).to_have_text(f"v{__version__}")
    assert page.locator("#versionInfo").text_content() == f"v{__version__}"


def test_header_says_version_unavailable_when_call_fails(pages: PageFactory) -> None:
    """
    INVARIANT: a failing /api/version call shows `version unavailable`, never a blank span
    BREAKS: a wrong key or a downed route looks like a page that forgot to show a version
    """
    page = pages.open(key="not-the-key")
    expect(page.locator("#versionInfo")).to_have_text("version unavailable")


def test_no_version_text_before_a_key_exists(pages: PageFactory) -> None:
    """
    INVARIANT: no version text appears before a key is stored
    BREAKS: the page implies it asked the daemon something it could not authenticate
    """
    page = pages.open(key=None)
    expect(page.locator("#apiKeyModal")).to_be_visible()
    expect(page.locator("#versionInfo")).to_have_text("")


# --------------------------------------------------------------------------- key entry


def test_question_mark_types_into_key_input_and_key_entry_loads_everything(
    pages: PageFactory,
) -> None:
    """
    INVARIANT: ? typed into the API-key input stays in the input and never opens help;
               saving the key loads feed, kinds and version without a reload
    BREAKS: a first-time visitor cannot type a key containing ?, and lands on a page
            whose kind select stays at All Kinds until they reload
    """
    page = pages.open(key=None)
    key_input = page.locator("#apiKeyInput")
    key_input.press("?")
    expect(key_input).to_have_value("?")
    expect(page.locator("#helpModal")).to_be_hidden()

    # Focus off the input, key prompt still open: the prompt itself blocks the shortcut.
    key_input.blur()
    page.keyboard.press("?")
    expect(page.locator("#helpModal")).to_be_hidden()

    key_input.fill(TEST_API_KEY)
    page.locator("#saveApiKey").click()

    _loaded(page)
    expect(page.locator("#kindSelect option")).to_have_text(
        ["All Kinds", "release", "tutorial"]
    )
    expect(page.locator("#versionInfo")).to_have_text(f"v{__version__}")


# --------------------------------------------------------------------------- help modal


def test_question_mark_toggles_help_and_escape_closes_it(pages: PageFactory) -> None:
    """
    INVARIANT: ? opens help, ? again closes it, Escape closes it
    BREAKS: the page has no in-app guidance, or help cannot be dismissed from the keyboard
    """
    page = pages.open()
    _loaded(page)
    help_modal = page.locator("#helpModal")
    expect(help_modal).to_be_hidden()

    page.keyboard.press("?")
    expect(help_modal).to_be_visible()
    page.keyboard.press("?")
    expect(help_modal).to_be_hidden()

    page.keyboard.press("?")
    expect(help_modal).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_modal).to_be_hidden()


def test_header_button_backdrop_and_close_button_drive_help(pages: PageFactory) -> None:
    """
    INVARIANT: the (?) header button opens help; the backdrop and the close button close it
    BREAKS: pointer and touch users have no way to reach or leave help
    """
    page = pages.open()
    _loaded(page)
    help_modal = page.locator("#helpModal")

    page.get_by_role("button", name="Help").click()
    expect(help_modal).to_be_visible()
    help_modal.click(position={"x": 4, "y": 4})
    expect(help_modal).to_be_hidden()

    page.get_by_role("button", name="Help").click()
    expect(help_modal).to_be_visible()
    page.locator("#helpClose").click()
    expect(help_modal).to_be_hidden()


def test_modified_question_mark_and_focused_select_do_not_open_help(
    pages: PageFactory,
) -> None:
    """
    INVARIANT: ? with ctrl, meta or alt held, or aimed at a focused select, does nothing
    BREAKS: browser shortcuts and select typeahead pop the help modal open
    NOTE: a plain ? opens help first, proving the handler is attached before the
          negative cases are trusted.
    """
    page = pages.open()
    _loaded(page)
    help_modal = page.locator("#helpModal")

    page.keyboard.press("?")
    expect(help_modal).to_be_visible()
    page.keyboard.press("Escape")
    expect(help_modal).to_be_hidden()

    for combo in ("Control+?", "Meta+?", "Alt+?"):
        page.keyboard.press(combo)
        expect(help_modal).to_be_hidden()

    page.locator("#sortSelect").focus()
    page.keyboard.press("?")
    expect(help_modal).to_be_hidden()
    page.keyboard.press("Escape")
    expect(page.locator("#detailPanel")).to_be_hidden()


def test_help_lists_the_controls_the_page_has_and_no_others(pages: PageFactory) -> None:
    """
    INVARIANT: help describes the web view's real controls and names no control it lacks
    BREAKS: the help promises a vote shortcut or an archived toggle the page does not have
    """
    page = pages.open()
    _loaded(page)
    page.keyboard.press("?")
    text = page.locator("#helpModal").inner_text()

    for needle in (
        "?",
        "Esc",
        "summary",
        "title",
        "👍",
        "👎",
        "Mark Read",
        "KIND",
        "SORT",
        "Priority",
        "Newest",
        "Unread",
        "24 hours",
        "30 seconds",
        "HIGH",
        "MEDIUM",
        "LOW",
        "✦",
        "xN sources",
        "interests-matched",
        "Top Must-Reads",
        "Audio Briefing",
    ):
        assert needle in text, f"help does not mention {needle!r}"

    assert not re.search(r"archiv|shortcut|\bj/k\b", text, re.IGNORECASE), text

    # Each control the help names exists on the page it describes.
    assert page.locator(".vote-btn.up").count() > 0
    assert page.locator(".vote-btn.down").count() > 0
    assert page.locator(".mark-read-btn").count() > 0
    assert page.locator("#kindSelect").count() == 1
    assert page.locator("#sortSelect option").all_text_contents() == [
        "Priority",
        "Newest",
        "Unread",
    ]
    assert page.locator(".must-reads-title").count() == 1
    assert page.locator("#generateAudioBtn").count() == 1


def test_escape_closes_help_before_the_detail_panel(
    pages: PageFactory, seeded_ids: dict[str, str]
) -> None:
    """
    INVARIANT: Escape closes the top panel only: help first, then the detail panel;
               with only the detail panel open, one Escape closes it
    BREAKS: one Escape dismisses both layers and the reader loses their place
    """
    page = pages.open()
    _loaded(page)
    detail = page.locator("#detailPanel")
    help_modal = page.locator("#helpModal")

    _summary_of(page, seeded_ids["h3"]).click()
    expect(detail).to_be_visible()
    expect(page.locator("#detailContent .detail-title")).to_be_visible()
    page.keyboard.press("?")
    expect(help_modal).to_be_visible()

    page.keyboard.press("Escape")
    expect(help_modal).to_be_hidden()
    expect(detail).to_be_visible()

    page.keyboard.press("Escape")
    expect(detail).to_be_hidden()

    _summary_of(page, seeded_ids["h3"]).click()
    expect(detail).to_be_visible()
    page.keyboard.press("Escape")
    expect(detail).to_be_hidden()


# --------------------------------------------------------------------------- status bar


def test_status_bar_counts_labels_and_focus_ring(pages: PageFactory) -> None:
    """
    INVARIANT: the status bar keeps its four ids, renders each seeded per-priority count in
               its own element in the badge color, labels both selects, and shows a
               focus ring on a keyboard-focused select
    BREAKS: a status bar that reads as a placeholder, or a keyboard user who cannot see
            which select has focus
    """
    page = pages.open()
    _loaded(page)

    for element_id in ("statusInfo", "kindSelect", "sortSelect", "refreshInfo"):
        assert page.locator(f"#{element_id}").count() == 1, element_id

    for css, text, color in (
        (".badge-high", f"{HIGH} high", COLOR_HIGH),
        (".badge-medium", f"{MEDIUM} medium", COLOR_MEDIUM),
        (".badge-low", f"{LOW} low", COLOR_LOW),
    ):
        count = page.locator(f"#statusInfo {css}")
        expect(count).to_have_text(text)
        assert count.evaluate("e => getComputedStyle(e).color") == color
    expect(page.locator("#statusInfo .status-unread")).to_have_text(f"{UNREAD} unread")

    expect(page.locator("label[for=kindSelect]")).to_have_text("KIND")
    expect(page.locator("label[for=sortSelect]")).to_have_text("SORT")

    for _ in range(40):
        page.keyboard.press("Tab")
        if page.evaluate("document.activeElement.id") == "kindSelect":
            break
    assert page.evaluate("document.activeElement.id") == "kindSelect"
    ring = page.locator("#kindSelect").evaluate(
        "e => ({style: getComputedStyle(e).outlineStyle,"
        " width: parseFloat(getComputedStyle(e).outlineWidth)})"
    )
    assert ring["style"] != "none" and ring["width"] > 0, ring


def test_offline_status_is_plain_text_and_filter_tab_rules_are_gone(
    pages: PageFactory,
) -> None:
    """
    INVARIANT: the offline status renders as plain text, and the dead .filter-tabs and
               .filter-tab rules are gone from the stylesheet
    BREAKS: offline shows stale count spans; dead CSS keeps shipping
    """
    page = pages.open()
    _loaded(page)
    page.evaluate("window.prismisApp.updateOfflineStatus()")
    info = page.locator("#statusInfo")
    expect(info).to_have_text("Offline - daemon unreachable")
    assert info.evaluate("e => e.children.length") == 0

    selectors = page.evaluate(
        "Array.from(document.styleSheets).filter(s => s.href === null)"
        ".flatMap(s => Array.from(s.cssRules))"
        ".flatMap(r => r.selectorText === undefined ? Array.from(r.cssRules) : [r])"
        ".map(r => r.selectorText || '')"
    )
    assert not [s for s in selectors if "filter-tab" in s]
    assert any("status-count" in s for s in selectors), "stylesheet was not read"


# --------------------------------------------------------------------------- layout


def _widths(page: Page) -> dict[str, tuple[int, int]]:
    return page.evaluate(
        """() => {
            const m = (el) => [el.scrollWidth, el.clientWidth];
            return {
                document: m(document.documentElement),
                detail: m(document.getElementById('detailPanel')),
                help: m(document.getElementById('helpModal')),
            };
        }"""
    )


def _assert_no_overflow(page: Page, phase: str, width: int) -> None:
    for name, (scroll, client) in _widths(page).items():
        assert scroll <= client, (
            f"{name} overflows horizontally at {width}px ({phase}): "
            f"scrollWidth {scroll} > clientWidth {client}"
        )


@pytest.mark.parametrize("width", WIDTHS)
def test_no_horizontal_overflow_and_synthesis_headings_are_styled(
    pages: PageFactory, seeded_ids: dict[str, str], width: int
) -> None:
    """
    INVARIANT: at 375, 768 and 1024 px the document, the detail panel and the help modal
               never scroll horizontally, even with a 200-character unbroken token in the
               title, summary, synthesis and a quote; synthesis h4 uses the accent color
    BREAKS: a long URL or token widens the page and a phone reader pans sideways
    """
    page = pages.open(width=width)
    _loaded(page)
    _assert_no_overflow(page, "feed", width)

    _summary_of(page, seeded_ids["long"]).click()
    heading = page.locator("#detailContent .detail-synthesis h4").first
    expect(heading).to_be_visible()
    expect(page.locator("#detailContent blockquote").first).to_be_visible()
    _assert_no_overflow(page, "detail", width)
    assert heading.evaluate("e => getComputedStyle(e).color") == COLOR_SYNTHESIS

    page.keyboard.press("?")
    expect(page.locator("#helpModal")).to_be_visible()
    _assert_no_overflow(page, "help", width)
