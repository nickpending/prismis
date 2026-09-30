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
