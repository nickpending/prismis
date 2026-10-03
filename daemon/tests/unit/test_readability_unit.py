"""Unit tests for the one shared readability check (gh #80, SC-1).

Protects:
- Each measured failure shape (HN's `<a href=...>Comments</a>` stub, a JS-required
  notice, Reddit's link-only content, YouTube's no-transcript placeholder, RSS's
  no-content fallback, and empty/markup-only text) is NOT readable.
- Genuine short text (a one-sentence blurb, a two-line question) and ordinary article
  text ARE readable -- there is no bare length floor, so a length-based check alone
  would misclassify these.
- The placeholder-writing helpers (format_reddit_link_only, format_youtube_no_transcript)
  and RSS_NO_CONTENT_FALLBACK are defined once here and is_readable recognizes exactly
  what they produce, so the writer and the checker cannot drift into two copies of the
  same sentence.
"""

import pytest

from prismis_daemon.readability import (
    RSS_NO_CONTENT_FALLBACK,
    format_reddit_link_only,
    format_youtube_no_transcript,
    is_readable,
)


# ---------------------------------------------------------------------------
# Measured failure shapes: not readable
# ---------------------------------------------------------------------------


def test_is_readable_false_for_hn_comments_anchor_stub() -> None:
    """
    BREAKS: A length-only check would call this readable -- the raw HTML is longer
    than the genuine short blurb below -- because it never looks at the shape.
    """
    content = '<a href="https://news.ycombinator.com/item?id=123">Comments</a>'
    assert is_readable(content) is False


def test_is_readable_false_for_javascript_required_notice() -> None:
    content = "You need to enable JavaScript to run this app."
    assert is_readable(content) is False


def test_is_readable_false_for_reddit_link_only_content() -> None:
    content = format_reddit_link_only("https://example.com/some-article")
    assert is_readable(content) is False


def test_is_readable_false_for_reddit_link_only_content_with_trailing_blank_lines() -> (
    None
):
    """The literal shape fetchers/reddit.py writes today includes trailing blank
    lines when there is no selftext to append -- the check must not require an
    exact match against format_reddit_link_only's own return value."""
    content = "Link: https://example.com/some-article\n\n"
    assert is_readable(content) is False


def test_is_readable_false_for_youtube_no_transcript_placeholder() -> None:
    content = format_youtube_no_transcript("Some Video Title")
    assert is_readable(content) is False


def test_is_readable_false_for_rss_no_content_fallback() -> None:
    assert is_readable(RSS_NO_CONTENT_FALLBACK) is False


def test_is_readable_false_for_empty_or_markup_only_text() -> None:
    assert is_readable("") is False
    assert is_readable(None) is False
    assert is_readable("   ") is False
    assert is_readable("<div><span></span></div>") is False


# ---------------------------------------------------------------------------
# Genuine content: readable, regardless of length
# ---------------------------------------------------------------------------


def test_is_readable_true_for_genuine_one_sentence_news_blurb() -> None:
    """A real 184-char-scale news blurb -- shorter than several of the failure
    shapes above, so a length floor would misclassify it."""
    content = (
        "The city council voted 5-2 on Tuesday to approve the new bike lane "
        "extension along Elm Street, with construction expected to begin "
        "sometime in early spring pending final budget approval."
    )
    assert len(content) < 250
    assert is_readable(content) is True


def test_is_readable_true_for_genuine_short_reddit_question() -> None:
    content = "Anyone else notice their battery life tank after the last update? Mine went from 2 days to barely lasting one."
    assert len(content) < 250
    assert is_readable(content) is True


def test_is_readable_true_for_ordinary_article_text() -> None:
    content = (
        "Researchers at the university published a new study this week "
        "examining long-term outcomes across a decade of patient records, "
        "finding a consistent pattern that held even after controlling for "
        "age, income, and prior health history."
    )
    assert is_readable(content) is True


def test_is_readable_true_for_content_that_merely_contains_a_link() -> None:
    """A real article with an inline link is not the HN anchor-only stub -- the
    anchor is only part of the content, not the whole of it."""
    content = (
        'Read the background first (<a href="https://example.com">here</a>), '
        "then consider how the numbers changed once the policy took effect."
    )
    assert is_readable(content) is True


# ---------------------------------------------------------------------------
# Shape rules (gh #80, readable-content-misses): has-prose and JavaScript-wall
# ---------------------------------------------------------------------------

_STOPPELS_EXTRACTION = (
    "Hacker News has set AI a lot of challenges, and it has passed most of them "
    "with flying colors.\n\nThis page needs JavaScript."
)
_YOUTUBE_FOOTER = "\n".join(
    [
        "About",
        "Press",
        "Copyright",
        "Contact us",
        "Creators",
        "Advertise",
        "Developers",
        "Terms",
        "Privacy",
        "Policy & Safety",
        "How YouTube works",
        "Test new features",
        "© 2026 Google LLC",
    ]
)
_MASTODON_NOTICE = (
    "To use the Mastodon web application, please enable JavaScript. "
    "Alternatively, try one of the native apps for Mastodon for your platform."
)
_NOTION_NOTICE = "Notion requires JavaScript to be enabled in your browser."
_NATURE_NOTICE = "Please enable JavaScript to view the content of this page."


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(_STOPPELS_EXTRACTION, id="stoppels"),
        pytest.param("‹\n›", id="frogandtoad-glyphs"),
        pytest.param(_YOUTUBE_FOOTER, id="youtube-footer"),
        pytest.param(_MASTODON_NOTICE, id="mastodon"),
        pytest.param(_NOTION_NOTICE, id="notion"),
        pytest.param(_NATURE_NOTICE, id="nature"),
    ],
)
def test_is_readable_false_for_failure_shapes_the_literal_check_missed(
    content: str,
) -> None:
    """
    BREAKS: The pre-change check compares against one literal wall sentence and
    one anchor stub, so each of these (a notice in another wording, a page whose
    only prose is a notice, glyphs, a footer list) came back readable.
    """
    assert is_readable(content) is False


def test_is_readable_true_for_japanese_paragraph_with_no_spaces() -> None:
    content = "政府は火曜日に新しい経済対策を発表し、来年度の予算に盛り込む方針を示した。"
    assert is_readable(content) is True


def test_is_readable_true_for_long_article_containing_a_javascript_notice() -> None:
    """A notice in 1,200 or more visible characters never makes it unreadable."""
    prose = (
        "The committee reviewed the proposal in detail and recorded its findings "
        "for the public archive. "
    )
    content = prose * 14 + "\nLoading... (JavaScript required)\n" + prose
    assert len(content) >= 1200
    assert is_readable(content) is True


def test_is_readable_true_for_long_readme_listing_no_javascript_required() -> None:
    prose = (
        "This library parses configuration files and exposes them as typed "
        "objects for the rest of the application to read. "
    )
    content = prose * 12 + "\n- No JavaScript required\n- Works offline\n"
    assert len(content) >= 1200
    assert is_readable(content) is True


def test_is_readable_javascript_wall_size_bound_is_1200_visible_characters() -> None:
    """The same notice flips from wall to readable at the 1,200 boundary."""
    prose_line = "The committee reviewed the proposal and recorded its findings.\n"
    notice = "This page needs JavaScript.\n"
    small = notice + prose_line * 3
    large = notice + prose_line * 30
    assert len(" ".join(small.split())) < 1200 <= len(" ".join(large.split()))
    assert is_readable(small) is False
    assert is_readable(large) is True
