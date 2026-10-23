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
# The three markdown items ('markdown', 'titled', 'hostile') are low priority and read, so
# they raise LOW and TOTAL and leave UNREAD alone.
HIGH, MEDIUM, LOW = 3, 2, 4
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
# synthesis accent (.synthesis-body h3).
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
    "markdown": "Volcanic basalt columns form hexagonal patterns",
    "titled": "Medieval bridge restoration finishes in Prague",
    "hostile": "Deep sea cable repair ships head to Atlantic",
}

# The rewritten title the summarizer put in the h1 of the 'titled' item's reading summary.
REWRITTEN_TITLE = "Prague reopens its restored medieval crossing"
SEEDED_LINK = "https://example.com/md"

MARKDOWN_SUMMARY = f"""# Seeded heading

## Overview
Plain paragraph with **bold**, inline `code` and a [link]({SEEDED_LINK}).

# Second part

## Steps
- first bullet
- second bullet

1. numbered one
2. numbered two
"""

MARKDOWN_SYNTHESIS = """### Why it matters
Body paragraph.

### Takeaways
- a synthesis bullet
- another synthesis bullet
"""

TITLED_SUMMARY = f"""# {REWRITTEN_TITLE}

## Overview
A paragraph under the overview.
"""

HOSTILE_SUMMARY = """## Safe heading

A safe [link](https://example.com/ok) in a paragraph.

<script>window.__xss = 'script'</script>

<img src=x onerror="window.__xss = 'onerror'">

[bad link](javascript:window.__xss='href')

![x](http://evil.example/p.png)
"""


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
    source_id = storage.add_source(
        "https://example.com/feed.xml", "rss", "Browser Feed"
    )
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
    add("m1", "medium")
    add("m2", "medium", read=True)
    add("l1", "low")
    add(
        "markdown",
        "low",
        read=True,
        analysis={
            "reading_summary": MARKDOWN_SUMMARY,
            "deep_extraction": {"synthesis": MARKDOWN_SYNTHESIS},
        },
    )
    add("titled", "low", read=True, analysis={"reading_summary": TITLED_SUMMARY})
    add("hostile", "low", read=True, analysis={"reading_summary": HOSTILE_SUMMARY})
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


# --------------------------------------------------------------------------- drift warning


FLAGGED_TEXT = (
    "⚠ Drift: 62% of the last 24 hours' items are unprioritized, usual is 54%"
)
THIN_TEXT = "⚠ Drift: too few items to measure, usual is ?"


def _store_drift(
    test_db: Path,
    *,
    flagged: bool,
    share: float | None,
    baseline: float | None,
) -> None:
    Storage(test_db).insert_drift_check(
        {
            "checked_at": datetime.now(UTC).isoformat(),
            "window_items": 40,
            "window_null": 25,
            "share": share,
            "baseline": baseline,
            "baseline_windows": 7,
            "deviation": None
            if share is None or baseline is None
            else share - baseline,
            "threshold": 0.2,
            "flagged": flagged,
            "reason": None,
        }
    )


def _refresh(page: Page) -> None:
    page.evaluate("window.prismisApp.loadContent()")


def test_flagged_drift_check_adds_one_warning_span_beside_the_counts(
    pages: PageFactory, test_db: Path
) -> None:
    """
    INVARIANT: a flagged latest drift check puts exactly one .status-drift span in
               #statusInfo with the daemon's console wording, the thin-window wording
               when share and baseline are null, and leaves the other spans' text alone
    BREAKS: a scoring regression stays invisible in the feed the user is reading
    """
    _store_drift(test_db, flagged=True, share=0.62, baseline=0.54)
    page = pages.open()
    _loaded(page)
    drift = page.locator("#statusInfo .status-drift")
    expect(drift).to_have_text(FLAGGED_TEXT)
    assert drift.count() == 1
    expect(page.locator("#statusInfo .status-total")).to_have_text(f"{TOTAL} items")
    expect(page.locator("#statusInfo .badge-high")).to_have_text(f"{HIGH} high")
    expect(page.locator("#statusInfo .status-unread")).to_have_text(f"{UNREAD} unread")

    _store_drift(test_db, flagged=True, share=None, baseline=None)
    _refresh(page)
    expect(drift).to_have_text(THIN_TEXT)
    assert page.locator("#statusInfo .status-drift").count() == 1


def test_drift_warning_clears_when_no_longer_flagged_or_unreadable(
    pages: PageFactory, test_db: Path
) -> None:
    """
    INVARIANT: the warning is gone after the refresh following an unflagged check, and
               is absent with no check at all and when GET /api/drift answers 500; the
               offline status bar keeps zero children
    BREAKS: a stale warning outlives the regression it reported, or a drift failure
            breaks the feed
    """
    no_check = pages.open()
    _loaded(no_check)
    assert no_check.locator("#statusInfo .status-drift").count() == 0

    _store_drift(test_db, flagged=True, share=0.62, baseline=0.54)
    page = pages.open()
    expect(page.locator("#statusInfo .status-drift")).to_have_text(FLAGGED_TEXT)

    _store_drift(test_db, flagged=False, share=0.5, baseline=0.54)
    _refresh(page)
    expect(page.locator("#statusInfo .status-unread")).to_have_text(f"{UNREAD} unread")
    assert page.locator("#statusInfo .status-drift").count() == 0

    _store_drift(test_db, flagged=True, share=0.62, baseline=0.54)
    _refresh(page)
    expect(page.locator("#statusInfo .status-drift")).to_have_text(FLAGGED_TEXT)
    page.route(
        "**/api/drift",
        lambda route: route.fulfill(
            status=500, content_type="application/json", body='{"detail":"boom"}'
        ),
    )
    _refresh(page)
    expect(page.locator("#statusInfo .status-total")).to_have_text(f"{TOTAL} items")
    assert page.locator("#statusInfo .status-drift").count() == 0

    page.evaluate("window.prismisApp.updateOfflineStatus()")
    assert page.locator("#statusInfo").evaluate("e => e.children.length") == 0


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
               title, summary, synthesis and a quote; synthesis h3 uses the accent color
    BREAKS: a long URL or token widens the page and a phone reader pans sideways
    """
    page = pages.open(width=width)
    _loaded(page)
    _assert_no_overflow(page, "feed", width)

    _summary_of(page, seeded_ids["long"]).click()
    heading = page.locator("#detailContent .synthesis-body h3").first
    expect(heading).to_be_visible()
    expect(page.locator("#detailContent blockquote").first).to_be_visible()
    _assert_no_overflow(page, "detail", width)
    assert heading.evaluate("e => getComputedStyle(e).color") == COLOR_SYNTHESIS

    page.keyboard.press("?")
    expect(page.locator("#helpModal")).to_be_visible()
    _assert_no_overflow(page, "help", width)


# ------------------------------------------------------------------------- markdown


def _open_detail(page: Page, item_id: str) -> None:
    _loaded(page)
    _summary_of(page, item_id).click()
    expect(page.locator("#detailContent .detail-summary")).to_be_visible()


def test_summary_and_synthesis_render_as_markdown_without_the_leading_title(
    pages: PageFactory, seeded_ids: dict[str, str]
) -> None:
    """
    INVARIANT: the reading summary and the deep synthesis render as markdown elements, the
               summary's leading h1 is dropped and a later h1 stays, links open in a new tab
    BREAKS: readers see raw ##, ** and '- ', or the article title twice
    """
    page = pages.open()
    _open_detail(page, seeded_ids["markdown"])

    summary = page.locator("#detailContent .detail-summary")
    expect(summary.locator("h1")).to_have_count(1)
    expect(summary.locator("h1")).to_have_text("Second part")
    assert "Seeded heading" not in summary.inner_text()
    for selector in ("h2", "ul > li", "ol > li", "strong", "code"):
        assert summary.locator(selector).count() > 0, selector
    link = summary.locator("a")
    expect(link).to_have_attribute("href", SEEDED_LINK)
    expect(link).to_have_attribute("target", "_blank")
    assert "noopener" in (link.get_attribute("rel") or "")

    synthesis = page.locator("#detailContent .synthesis-body")
    assert synthesis.locator("h3").count() == 2
    assert synthesis.locator("li").count() == 2

    text = page.locator("#detailContent").inner_text()
    assert "##" not in text
    assert "**" not in text
    assert not re.search(r"^- ", text, re.MULTILINE), text


def test_leading_title_h1_is_dropped_even_when_rewritten(
    pages: PageFactory, seeded_ids: dict[str, str]
) -> None:
    """
    INVARIANT: a summary opening with an h1 that rewrites the title shows neither that h1
               nor its text; the stored title is shown once, in the panel title
    BREAKS: the panel shows two different headlines for one article
    """
    page = pages.open()
    _open_detail(page, seeded_ids["titled"])

    summary = page.locator("#detailContent .detail-summary")
    expect(summary.locator("h1")).to_have_count(0)
    assert REWRITTEN_TITLE not in summary.inner_text()
    first_heading = summary.locator("h1, h2, h3, h4, h5, h6").first
    assert first_heading.evaluate("e => e.tagName") == "H2"
    expect(first_heading).to_have_text("Overview")
    expect(page.locator("#detailContent .detail-title")).to_contain_text(
        TITLES["titled"]
    )


def test_hostile_summary_is_sanitized_and_runs_nothing(
    pages: PageFactory, seeded_ids: dict[str, str]
) -> None:
    """
    INVARIANT: model output cannot inject script, handlers, javascript: links or images,
               and cannot make the page fetch from another host
    BREAKS: a hostile article steers the summary into code running on a page that holds
            the API key
    """
    page = pages.open()
    _open_detail(page, seeded_ids["hostile"])

    summary = page.locator("#detailContent .detail-summary")
    expect(summary.locator("h2")).to_have_text("Safe heading")
    expect(summary.locator("a[href='https://example.com/ok']")).to_have_count(1)

    detail = page.locator("#detailContent")
    assert detail.locator("script").count() == 0
    assert detail.locator("[onerror]").count() == 0
    assert detail.locator("img").count() == 0
    javascript_links = detail.locator("a").evaluate_all(
        "els => els.filter(a => (a.getAttribute('href') || '').toLowerCase()"
        ".startsWith('javascript:')).length"
    )
    assert javascript_links == 0
    page.wait_for_timeout(500)  # an image error handler would have fired by now
    assert page.evaluate("window.__xss") is None
    assert "evil.example" not in pages.blocked_hosts
