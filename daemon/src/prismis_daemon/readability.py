"""The one readability check every consumer calls (gh #80, SC-1).

Content reaches analysis after every fetcher runs, and reaching analysis is not the
same as being readable: an RSS entry that keeps a feed's `<a href=...>Comments</a>`
stub after trafilatura fails, a JS-walled page's noscript notice, a Reddit link post
whose content is nothing but the outbound URL, this daemon's own "no transcript" and
"no content" placeholders, and outright empty/markup-only text are all measured
failure shapes (gh #80) that get summarized and evaluated as if they were real
content. `is_readable` is the one place that tells those shapes apart from genuine
short content -- there is no bare length floor here, because a one-sentence news
blurb and a two-line Reddit question are both genuine and both short; every check
below is about the content's shape, never its length.

The placeholder texts prismis's own fetchers write for "nothing to extract" are
defined once here (as a constant or a `format_*` helper) and imported by the fetcher
that writes each one, so the writer and this check share one definition instead of
two copies of the same sentence drifting apart.
"""

from __future__ import annotations

import re

# fetchers/rss.py's `_extract_full_content` writes this exact string when neither
# trafilatura nor the feed entry's own content/summary/description yielded anything.
RSS_NO_CONTENT_FALLBACK = "No content available"

# fetchers/reddit.py's `_to_content_item` writes content starting with this prefix
# for every link post, before (or instead of) any extracted article text.
REDDIT_LINK_PREFIX = "Link: "

# fetchers/youtube.py's `_handle_missing_transcript` writes content ending in this
# sentence when yt-dlp found no transcript for a video.
YOUTUBE_NO_TRANSCRIPT_SUFFIX = "No transcript available for this video."

# The literal noscript text React (and several other SPA frameworks) ship by default
# -- the JS-required notice measured on gh #80's Hacker News share, not a paraphrase.
_JS_WALL_TEXT = "you need to enable javascript to run this app."

_TAG_RE = re.compile(r"<[^>]+>")
# The whole content is exactly one anchor element and nothing else -- the shape of
# Hacker News's `<a href=...>Comments</a>` RSS stub, not merely "contains a link".
_ANCHOR_ONLY_RE = re.compile(r"^<a\s[^>]*>[^<]*</a>$", re.IGNORECASE)


def format_reddit_link_only(url: str) -> str:
    """The content a Reddit link post gets before (or instead of) its article text.

    fetchers/reddit.py calls this to write the placeholder; `is_readable` recognizes
    it (and its trailing-blank-line variant already stored for existing rows) through
    `REDDIT_LINK_PREFIX`, so both sides share one definition of the shape.
    """
    return f"{REDDIT_LINK_PREFIX}{url}"


def format_youtube_no_transcript(title: str) -> str:
    """The content a YouTube video gets when yt-dlp found no transcript for it.

    fetchers/youtube.py calls this to write the placeholder; `is_readable` recognizes
    it through `YOUTUBE_NO_TRANSCRIPT_SUFFIX`, so both sides share one definition.
    """
    return f"Video title: {title}\n\n{YOUTUBE_NO_TRANSCRIPT_SUFFIX}"


def _normalize(text: str) -> str:
    """Collapse whitespace so a multi-line placeholder matches regardless of layout."""
    return " ".join(text.split())


def _is_reddit_link_only(normalized: str) -> bool:
    """`normalized` is exactly `REDDIT_LINK_PREFIX` + a URL and nothing else."""
    if not normalized.startswith(REDDIT_LINK_PREFIX):
        return False
    remainder = normalized[len(REDDIT_LINK_PREFIX) :]
    return bool(remainder) and " " not in remainder


def _is_youtube_no_transcript(normalized: str) -> bool:
    return normalized.endswith(_normalize(YOUTUBE_NO_TRANSCRIPT_SUFFIX))


def is_readable(content: str | None) -> bool:
    """Whether `content` is real content rather than one of the measured failure
    shapes above.

    Genuine short text -- a one-sentence blurb, a two-line question -- is readable:
    every check here is shape-based, never a length threshold.
    """
    if not content:
        return False

    stripped = content.strip()
    if not stripped:
        return False

    if _ANCHOR_ONLY_RE.match(stripped):
        # The whole content is one anchor tag -- Hacker News's `<a href=...>Comments
        # </a>` stub, kept when trafilatura's extraction failed.
        return False

    visible = _normalize(_TAG_RE.sub("", stripped))
    if not visible:
        # Markup-only: tags with nothing readable between them.
        return False

    if visible.casefold().rstrip(".") == _JS_WALL_TEXT.rstrip("."):
        return False

    if visible == RSS_NO_CONTENT_FALLBACK:
        return False

    if _is_reddit_link_only(visible):
        return False

    if _is_youtube_no_transcript(visible):
        return False

    return True
