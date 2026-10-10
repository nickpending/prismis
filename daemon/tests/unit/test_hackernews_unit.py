"""Hacker News stories read with their discussion (hn-discussion SC-1, SC-2, SC-5).

Every HN exchange is replayed from a vcrpy cassette (`http_cassette`, conftest.py) with
no network. The Firebase responses in the cassettes are what the live API sent on
2026-10-10 (story 50033678 has a deleted and a dead comment among its first kids); the
feed in each is hand-written in the shape of news.ycombinator.com/rss, and the blocked
article's 403 is hand-written too. A re-record (PRISMIS_RECORD_HTTP=1) would replace
all of it with the live site's current answers, which these assertions are not written
against.
"""

import re
from pathlib import Path

import pytest
from conftest import make_config

from prismis_daemon.config import Config
from prismis_daemon.fetchers.rss import RSSFetcher
from prismis_daemon.hackernews import html_to_text, is_item_link, story_id
from prismis_daemon.models import ContentItem
from prismis_daemon.readability import (
    DISCUSSION_HEADER,
    is_discussion_basis,
    is_readable,
)

# The recorded feeds' entries age with the cassette, and the fetcher drops entries older
# than the lookback.
RECORDED_FEED_LOOKBACK_DAYS = 36500
HN_SOURCE = {"url": "https://news.ycombinator.com/rss", "id": "hn-source"}
WSJ_URL = "https://wsj.com/tech/ai/tom-brown-athropic-669005ad"
BLOCKED_ARTICLE = {"outcome": "fetch_failed", "detail": "HTTP 403"}
COMMENTS_ANCHOR = (
    '<a href="https://news.ycombinator.com/item?id={id}">Comments</a>'
)


def hn_fetcher(max_comments: int = 5) -> RSSFetcher:
    return RSSFetcher(
        max_items=5,
        config=make_config(
            max_days_lookback=RECORDED_FEED_LOOKBACK_DAYS,
            hackernews_max_comments=max_comments,
        ),
    )


def only_item(items: list[ContentItem]) -> ContentItem:
    assert len(items) == 1
    return items[0]


def content_of(item: ContentItem) -> str:
    assert item.content is not None
    return item.content


def comment_authors(content: str) -> list[str]:
    return re.findall(r"^\*\*(.+?):\*\*$", content, flags=re.MULTILINE)


# ---------------------------------------------------------------------------
# SC-1: the discussion block
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("http_cassette")
def test_a_blocked_story_gets_its_first_live_comments_in_hn_order() -> None:
    """
    SC-1: the story's kids are, in HN's order, dtf, bix6, stillpointlab, netfortius,
    a deleted comment, a dead one, gsky. The item carries the first five live ones in
    that order under the shared header as plain text, after a link line, since the
    article answered 403.
    BREAKS: a block that is missing, reordered, longer than max_comments, that keeps
    the deleted or dead comment (fewer than five live ones would then be read) or any
    raw HTML fails one assertion each.
    """
    item = only_item(hn_fetcher(max_comments=5).fetch_content(HN_SOURCE))

    content = content_of(item)
    assert content.startswith(f"Link: {WSJ_URL}\n\n{DISCUSSION_HEADER}\n\n")
    assert content.count(DISCUSSION_HEADER) == 1
    assert comment_authors(content) == [
        "dtf",
        "bix6",
        "stillpointlab",
        "netfortius",
        "gsky",
    ]
    assert '> "He offered to mop the floors at OpenAI to get his foot in the door."' in (
        content
    )
    assert "\n\nThe American dream is alive and kicking." in content
    assert "\n> https://archive.ph/X2W6e\n" in content
    assert "[dead]" not in content
    assert "throwaway132448" not in content
    assert not re.search(r"<[a-z/]|&#x|&quot;|&gt;|&amp;", content), content
    assert is_readable(content)
    assert is_discussion_basis(content)
    assert item.analysis == {"fetch_outcome": BLOCKED_ARTICLE}


@pytest.mark.usefixtures("http_cassette")
def test_max_comments_bounds_how_many_comments_are_asked_for() -> None:
    """
    SC-1: with max_comments = 3 the cassette holds the story and its first three kids
    only; a fourth comment request would be refused by the cassette and fail the test.
    BREAKS: ignoring the bound reads every kid, or keeps all comments of the first
    kids it did read.
    """
    item = only_item(hn_fetcher(max_comments=3).fetch_content(HN_SOURCE))

    assert comment_authors(content_of(item)) == ["dtf", "bix6", "stillpointlab"]


@pytest.mark.usefixtures("http_cassette")
def test_a_known_readable_story_is_not_asked_for_its_comments() -> None:
    """
    The cassette holds the feed only: any Firebase request for the entry would be
    refused and fail the test.
    BREAKS: reading comments for an entry the orchestrator already stored readably
    spends 1 + max_comments requests whose result is discarded.
    """
    # The feed entry has no guid, so its external id is the hash of its link.
    external_id = RSSFetcher(config=make_config())._get_external_id({"link": WSJ_URL})

    item = only_item(
        hn_fetcher().fetch_content(HN_SOURCE, known_readable_ids={external_id})
    )

    assert content_of(item) == COMMENTS_ANCHOR.format(id=50033678)


@pytest.mark.usefixtures("http_cassette")
def test_a_story_with_no_comments_is_built_as_it_would_have_been() -> None:
    """
    BREAKS: writing the header over nothing, or a link line, turns an untouched item
    into one that reads as discussion-only.
    """
    item = only_item(hn_fetcher().fetch_content(HN_SOURCE))

    assert content_of(item) == COMMENTS_ANCHOR.format(id=50036322)
    assert DISCUSSION_HEADER not in content_of(item)
    assert item.analysis == {
        "fetch_outcome": {"outcome": "fetch_failed", "detail": "HTTP 404"}
    }


# ---------------------------------------------------------------------------
# SC-2: an Ask HN post's body
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("http_cassette")
def test_an_ask_hn_post_body_is_the_items_own_text() -> None:
    """
    SC-2: the entry links to its own HN item, so there is no article to extract (the
    cassette holds no article request); the body is the item's text, then the block.
    BREAKS: keeping the feed's Comments anchor as the body, or putting the comments
    first, fails the first assertion.
    """
    item = only_item(hn_fetcher().fetch_content(HN_SOURCE))

    own_text = (
        "I'm curious about the technical details behind the RAW denoising in Adobe "
        "Lightroom, DxO DeepPRIME, etc. What makes these propreitary solutions so "
        "much better than open-source models like RawNIND?"
    )
    assert content_of(item).startswith(f"{own_text}\n\n{DISCUSSION_HEADER}\n\n")
    assert comment_authors(content_of(item)) == ["PaulHoule"]
    assert "Link: " not in content_of(item)
    assert "&#x" not in content_of(item)
    assert item.analysis is None


# ---------------------------------------------------------------------------
# SC-5: a failed read
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("http_cassette")
@pytest.mark.parametrize(
    "detail",
    [
        pytest.param("HTTPError", id="story-503"),
        pytest.param("JSONDecodeError", id="story-not-json"),
        pytest.param("HTTPError", id="comment-500"),
    ],
)
def test_a_failed_comment_fetch_records_fetch_failed_and_keeps_the_item(
    detail: str,
) -> None:
    """
    SC-5: the story request answers 503 / not JSON, or the second comment answers 500
    after one good comment. The fetch cycle does not raise, the item is the one the
    article step built (the feed's Comments anchor, no half a thread, no link line)
    and its analysis says which way the comment read failed.
    BREAKS: letting the error out fails the cycle; keeping the one good comment
    leaves half a thread; dropping the outcome leaves a failed read looking like a
    quiet story.
    """
    item = only_item(hn_fetcher().fetch_content(HN_SOURCE))

    assert content_of(item) == COMMENTS_ANCHOR.format(id=50033678)
    assert item.analysis == {
        "fetch_outcome": BLOCKED_ARTICLE,
        "comments_outcome": {"outcome": "fetch_failed", "detail": detail},
    }


# ---------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------


def test_story_id_recognizes_only_an_hn_item_comments_link() -> None:
    assert story_id("https://news.ycombinator.com/item?id=50033678") == "50033678"
    assert story_id("http://news.ycombinator.com/item?id=7") == "7"
    assert story_id("https://news.ycombinator.com/item?id=12&x=1") is None
    assert story_id("https://example.com/item?id=12") is None
    assert story_id("https://old.reddit.com/r/x/comments/1/") is None
    assert story_id(None) is None
    assert is_item_link("https://news.ycombinator.com/item?id=5", "5")
    assert not is_item_link("https://news.ycombinator.com/item?id=5", "6")
    assert not is_item_link("https://example.com/post", "5")


@pytest.mark.parametrize(
    ("html", "text"),
    [
        ("plain", "plain"),
        ("first<p>second", "first\n\nsecond"),
        ("it&#x27;s &quot;fine&quot; &amp; &lt;done&gt;", "it's \"fine\" & <done>"),
        (
            '<a href="https:&#x2F;&#x2F;example.com&#x2F;full&#x2F;path" rel="nofollow">'
            "https:&#x2F;&#x2F;example.com&#x2F;fu...</a>",
            "https://example.com/full/path",
        ),
        ('see <a href="https://x.test/a">this page</a> now', "see this page now"),
        (
            "code:<p><pre><code>a = 1\n  b = 2</code></pre><p>done",
            "code:\n\na = 1\n  b = 2\n\ndone",
        ),
        ("<i>emphasis</i> kept as text", "emphasis kept as text"),
        ("", ""),
    ],
)
def test_html_to_text_gives_plain_text(html: str, text: str) -> None:
    """
    BREAKS: leaving tags or entities in, running paragraphs together, or keeping a
    link's shortened text instead of its address.
    """
    assert html_to_text(html) == text


@pytest.mark.parametrize("value", [-1, -50])
def test_a_negative_hackernews_max_comments_is_refused(value: int) -> None:
    with pytest.raises(ValueError, match="hackernews_max_comments"):
        make_config(hackernews_max_comments=value).validate()


def test_the_default_config_reads_five_hackernews_comments(
    isolated_xdg_env: Path,
) -> None:
    assert Config.from_file().hackernews_max_comments == 5
