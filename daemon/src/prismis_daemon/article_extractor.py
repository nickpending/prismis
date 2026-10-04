"""The one full-article extractor, shared by every fetcher that turns a URL into
article text (SC-2).

fetchers/rss.py used to be the only caller of trafilatura's `fetch_url`/`extract`;
fetchers/reddit.py now needs the same extraction for a link post's outbound URL. This
module is that one call, so both fetchers share it instead of each carrying its own
copy that can drift.
"""

from __future__ import annotations

import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Literal

from trafilatura import extract
from trafilatura.utils import decode_file

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT_SECONDS = 30
_MAX_PAGE_BYTES = 20_000_000
_USER_AGENT = "Mozilla/5.0 (compatible; prismis-daemon)"

ArticleOutcome = Literal["extracted", "empty", "fetch_failed"]


@dataclass(frozen=True)
class ArticleResult:
    """What one extraction attempt found: the text, and which of three outcomes it was."""

    text: str | None
    outcome: ArticleOutcome
    detail: str = ""

    def as_fetch_outcome(self) -> dict[str, str]:
        """The `{"outcome", "detail"}` record fetchers store under `fetch_outcome`."""
        return {"outcome": self.outcome, "detail": self.detail}


def extract_article(url: str) -> ArticleResult:
    """Fetch `url` and extract its main article text with trafilatura.

    Three outcomes: `extracted` (text set), `empty` (the page was fetched but
    trafilatura found nothing) and `fetch_failed` (`detail` is `HTTP <status>`
    when the server answered with an error status, `no response` when nothing
    came back, or the exception class name). The caller owns its own fallback --
    RSS's (the feed entry's own content) differs from Reddit's (the link-only
    placeholder) -- so this never raises or chooses one.
    """
    try:
        # One request, no status retries: trafilatura's own downloader retries a 429
        # or 5xx with long backoff and then reports only None, which hides the status
        # this result exists to carry.
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        # A fresh opener per call: `urlopen` caches one global opener, and with it
        # the proxy environment of whichever call came first.
        opener = urllib.request.build_opener()
        with opener.open(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            body = response.read(_MAX_PAGE_BYTES + 1)
        if len(body) > _MAX_PAGE_BYTES:
            return ArticleResult(None, "fetch_failed", "page too large")
        html = decode_file(body)

        content = (
            extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if html
            else None
        )
        if content:
            return ArticleResult(content, "extracted")
        return ArticleResult(None, "empty")

    except urllib.error.HTTPError as e:
        return ArticleResult(None, "fetch_failed", f"HTTP {e.code}")
    except urllib.error.URLError:
        return ArticleResult(None, "fetch_failed", "no response")
    except Exception as e:
        logger.warning(f"Error extracting article from {url}: {e}")
        return ArticleResult(None, "fetch_failed", type(e).__name__)
