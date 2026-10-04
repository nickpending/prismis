"""The one readability check every consumer calls (gh #80, SC-1).

Content reaches analysis after every fetcher runs, and reaching analysis is not the
same as being readable: an RSS entry that keeps a feed's `<a href=...>Comments</a>`
stub after trafilatura fails, a JS-walled page's notice in any wording, a Reddit link
post whose content is nothing but the outbound URL, site navigation or footer text,
this daemon's own "no transcript" and "no content" placeholders, and outright
empty/markup-only text are all measured failure shapes (gh #80) that get summarized
and evaluated as if they were real content. `is_readable` is the one place that tells
those shapes apart from genuine short content -- there is no length floor on its own,
because a one-sentence news blurb and a two-line Reddit question are both genuine and
both short; every check below is about the content's shape, and a size bound only
qualifies the JavaScript-wall signal.

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

# A line that names JavaScript with one of these requirement verbs is a wall notice
# ("please enable JavaScript", "This page needs JavaScript", "JavaScript is required").
_JS_NAME_RE = re.compile(r"javascript", re.IGNORECASE)
_JS_REQUIREMENT_RE = re.compile(
    r"\b(?:enable|enabled|disable|disabled|need|needs|require|requires|required|turn on)\b",
    re.IGNORECASE,
)

# A notice makes content a wall only below this many visible characters. Measured on
# cerebro, 2026-10-03: walls up to 1,068 chars, real articles from 1,409.
_JS_WALL_MAX_VISIBLE_CHARS = 1200

# Minimum words on one line for the content to hold prose.
_MIN_PROSE_WORDS = 4

_TAG_RE = re.compile(r"<[^>]+>")
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
# Kana and CJK ideographs: each counts as one word, since those scripts carry no spaces.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿豈-﫿]")


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


def _word_count(line: str) -> int:
    """Words on one line with tags and URLs removed; each CJK character is one word
    and a token with no letter or digit (a bullet, a slash, a lone glyph) is none."""
    text = _URL_RE.sub(" ", _TAG_RE.sub(" ", line))
    cjk = len(_CJK_RE.findall(text))
    rest = _CJK_RE.sub(" ", text)
    return cjk + sum(1 for token in rest.split() if any(c.isalnum() for c in token))


def _has_prose(content: str) -> bool:
    """Some line of `content` holds at least `_MIN_PROSE_WORDS` words."""
    return any(_word_count(line) >= _MIN_PROSE_WORDS for line in content.splitlines())


def _is_javascript_wall(content: str, visible: str) -> bool:
    """A line names JavaScript with a requirement verb and the visible text is under
    `_JS_WALL_MAX_VISIBLE_CHARS`. The size only qualifies the notice; text without
    one is never judged by its size."""
    if len(visible) >= _JS_WALL_MAX_VISIBLE_CHARS:
        return False
    return any(
        _JS_NAME_RE.search(line) and _JS_REQUIREMENT_RE.search(line)
        for line in _TAG_RE.sub(" ", content).splitlines()
    )


def readability_failure(content: str | None) -> str | None:
    """The reason `content` is not real content, or None when it is readable.

    Returns the first failing check's reason: `empty`, `placeholder:rss_no_content`,
    `placeholder:reddit_link_only`, `placeholder:youtube_no_transcript`, `no_prose`
    or `js_wall`.

    Genuine short text -- a one-sentence blurb, a two-line question -- is readable:
    the checks are about shape (a line of prose; a JavaScript-requirement notice in
    a small page), never a bare length threshold.
    """
    if not content:
        return "empty"

    stripped = content.strip()
    if not stripped:
        return "empty"

    visible = _normalize(_TAG_RE.sub("", stripped))
    if not visible:
        # Markup-only: tags with nothing readable between them.
        return "empty"

    if visible == RSS_NO_CONTENT_FALLBACK:
        return "placeholder:rss_no_content"

    if _is_reddit_link_only(visible):
        return "placeholder:reddit_link_only"

    if _is_youtube_no_transcript(visible):
        return "placeholder:youtube_no_transcript"

    if not _has_prose(stripped):
        return "no_prose"

    if _is_javascript_wall(stripped, visible):
        return "js_wall"

    return None


def is_readable(content: str | None) -> bool:
    """Whether `content` is real content rather than one of the measured failure
    shapes above; `readability_failure` names which one when it is not."""
    return readability_failure(content) is None
