"""The one full-article extractor, shared by every fetcher that turns a URL into
article text (SC-2).

fetchers/rss.py used to be the only caller of trafilatura's `fetch_url`/`extract`;
fetchers/reddit.py now needs the same extraction for a link post's outbound URL. This
module is that one call, so both fetchers share it instead of each carrying its own
copy that can drift.
"""

from __future__ import annotations

import logging

from trafilatura import extract, fetch_url

logger = logging.getLogger(__name__)


def extract_article(url: str) -> str | None:
    """Fetch `url` and extract its main article text with trafilatura.

    Returns the extracted text, or None when the page could not be fetched or
    trafilatura found no extractable content in it. The caller owns its own
    fallback -- RSS's fallback (the feed entry's own content/summary/description)
    differs from Reddit's (the link-only placeholder) -- so this stays a plain
    None rather than raising or choosing a fallback itself.
    """
    try:
        downloaded = fetch_url(url)
        if not downloaded:
            return None

        content = extract(
            downloaded,
            include_comments=False,
            include_tables=True,
            no_fallback=False,
        )
        return content or None

    except Exception as e:
        logger.warning(f"Error extracting article from {url}: {e}")
        return None
