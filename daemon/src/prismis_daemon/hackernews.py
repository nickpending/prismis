"""Hacker News stories' discussion, read from HN's official Firebase API.

An HN story arrives as plain RSS with a Comments link and no comments. This module
reads the story's top comments the way fetchers/reddit.py reads a post's: the story
item (https://hacker-news.firebaseio.com/v0/item/<id>.json) lists its `kids` in HN's
ranked order, and each of the first N live kids is one more item fetch. An Ask or
Show HN self post carries its own body in the story item's `text`. Both are HTML in
the API; this module hands back plain text.

fetchers/rss.py calls `fetch_discussion` for an entry `story_id` recognizes and
writes the result with `readability.format_discussion`.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

import requests

from .http_deadline import deadline_session

logger = logging.getLogger(__name__)

HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"

# One story is the story item plus up to max_comments (more when kids are dead) comment
# items, a few hundred milliseconds each; the budget bounds the lot so a stalled API
# cannot hold the fetch cycle.
HN_FETCH_BUDGET = 30.0

# A story's comments link, as it appears in every HN feed entry.
_COMMENTS_LINK_RE = re.compile(
    r"^https?://news\.ycombinator\.com/item\?id=(\d+)$", re.IGNORECASE
)

# What a call to the API raises when it did not complete or answered with something
# that is not the JSON item the code reads. Anything outside them is a defect.
_HN_ERRORS = (requests.exceptions.RequestException, ValueError)

# The author shown for a comment that names none.
_UNKNOWN_AUTHOR = "unknown"


@dataclass
class HNDiscussion:
    """What the API holds for one story: its own text and its top live comments."""

    # The Ask/Show HN body as plain text; None for a story that is a link.
    text: str | None = None
    # Dicts with `author` and `body`, in HN's ranked order.
    comments: list[dict[str, str]] = field(default_factory=list)


def story_id(comments_link: str | None) -> str | None:
    """The HN item id in a feed entry's comments link, or None when it is not one."""
    match = _COMMENTS_LINK_RE.match((comments_link or "").strip())
    return match.group(1) if match else None


def is_item_link(url: str, item_id: str) -> bool:
    """Whether `url` is HN's own page for item `item_id` -- an Ask/Show HN entry's link."""
    return story_id(url) == item_id


class _TextExtractor(HTMLParser):
    """HN's comment HTML as plain text: paragraphs apart, links as their address."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._blocks: list[str] = []
        self._current: list[str] = []
        self._pre_depth = 0
        self._href: str | None = None
        self._link_text: list[str] = []

    def _end_block(self) -> None:
        if self._pre_depth:
            text = "".join(self._current).strip("\n")
        else:
            text = " ".join("".join(self._current).split())
        if text:
            self._blocks.append(text)
        self._current = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "p":
            self._end_block()
        elif tag == "pre":
            self._end_block()
            self._pre_depth += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._link_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self._end_block()
            self._pre_depth = max(0, self._pre_depth - 1)
        elif tag == "a":
            link_text = "".join(self._link_text)
            self._link_text = []
            # HN shortens a link's visible text with "..."; the href is the whole URL.
            if self._href and link_text.strip().lower().startswith("http"):
                self._current.append(self._href)
            else:
                self._current.append(link_text)
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._link_text.append(data)
        else:
            self._current.append(data)

    def text(self) -> str:
        self._end_block()
        return "\n\n".join(self._blocks)


def html_to_text(html: str) -> str:
    """Plain text of HN's HTML: no tags, entities decoded, paragraphs blank-line apart."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def _get_item(session: requests.Session, item_id: str | int) -> dict[str, Any] | None:
    """One item's JSON, or None when the API holds none (it answers `null`)."""
    response = session.get(HN_ITEM_URL.format(id=item_id))
    response.raise_for_status()
    item = response.json()
    if item is None:
        return None
    if not isinstance(item, dict):
        raise ValueError(f"item {item_id} is not a JSON object")
    return item


def _live_comment(item: dict[str, Any] | None) -> dict[str, str] | None:
    """The comment as {author, body} plain text, or None for a deleted, dead or
    empty one."""
    if not item or item.get("deleted") or item.get("dead"):
        return None
    text = item.get("text")
    if not isinstance(text, str):
        return None
    body = html_to_text(text)
    if not body:
        return None
    author = item.get("by")
    return {
        "author": author if isinstance(author, str) and author else _UNKNOWN_AUTHOR,
        "body": body,
    }


def fetch_discussion(
    item_id: str, max_comments: int, budget: float = HN_FETCH_BUDGET
) -> tuple[HNDiscussion, dict[str, str] | None]:
    """Read story `item_id`'s own text and its first `max_comments` live comments.

    Args:
        item_id: The HN item id of the story
        max_comments: How many live comments to keep (0 = unlimited); a deleted or
            dead kid does not count, the next kid takes its place
        budget: Seconds the whole read may spend on the API

    Returns:
        (discussion, outcome). outcome is None when the read completed -- including
        with zero comments -- and {"outcome": "fetch_failed", "detail": <exception
        type>} when a request failed or the API answered with something that is not
        an item, in which case discussion is empty so a failed read is not mistaken
        for a quiet story.
    """
    session = deadline_session(budget)
    try:
        story = _get_item(session, item_id)
        if story is None:
            raise ValueError(f"story {item_id} does not exist")
        text = story.get("text")
        discussion = HNDiscussion(
            text=(html_to_text(text) or None) if isinstance(text, str) else None
        )
        kids = story.get("kids") or []
        if not isinstance(kids, list):
            raise ValueError(f"story {item_id} has non-list kids")
        for kid in kids:
            if max_comments and len(discussion.comments) >= max_comments:
                break
            comment = _live_comment(_get_item(session, kid))
            if comment:
                discussion.comments.append(comment)
        logger.debug(f"Fetched {len(discussion.comments)} comments for HN {item_id}")
        return discussion, None
    except _HN_ERRORS as e:
        logger.warning(f"Failed to fetch HN discussion for {item_id}: {e}")
        return HNDiscussion(), {"outcome": "fetch_failed", "detail": type(e).__name__}
    finally:
        session.close()

