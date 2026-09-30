"""Unit tests for the shared trafilatura article extractor (SC-2).

fetchers/rss.py's `_extract_full_content` used to be the only place that called
trafilatura's `fetch_url`/`extract`; SC-2 moves that call into one helper both the
RSS and Reddit fetchers call. Per Principle I, the HTTP side of this is exercised
against a real local server, not a mock -- the only thing standing in is nothing at
all here, since trafilatura's own `fetch_url`/`extract` are the code under test.
"""

from __future__ import annotations

import http.server
import threading
from collections.abc import Iterator

import pytest

from prismis_daemon.article_extractor import extract_article

_ARTICLE_HTML = b"""<!doctype html>
<html><head><title>A Real Article</title></head>
<body>
<article>
<h1>A Real Article</h1>
<p>This is the first paragraph of a genuine article, long enough for trafilatura's
extraction heuristics to recognize it as the main content block rather than
boilerplate chrome around it.</p>
<p>A second paragraph continues the thought, adding enough additional real prose
that the extracted text is unambiguously the article body and not a snippet.</p>
</article>
</body></html>
"""


@pytest.fixture
def article_server() -> Iterator[str]:
    """A local HTTP server serving one real article page and one 404 -- the real
    boundary `extract_article` fetches, not a stand-in for it."""

    class _Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/article":
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.send_header("Content-Length", str(len(_ARTICLE_HTML)))
                self.end_headers()
                self.wfile.write(_ARTICLE_HTML)
            else:
                self.send_error(404)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    host, port = server.server_address[0], server.server_address[1]
    assert isinstance(host, str)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def test_extract_article_returns_extracted_text_for_a_real_page(
    article_server: str,
) -> None:
    """
    BREAKS: A helper that only wraps `fetch_url` without also calling `extract`
    would return raw HTML (including the `<article>`/`<h1>` markup) instead of
    the extracted plain-text body.
    """
    result = extract_article(f"{article_server}/article")

    assert result is not None
    assert "first paragraph of a genuine article" in result
    assert "<article>" not in result
    assert "<h1>" not in result


def test_extract_article_returns_none_when_the_page_is_unreachable(
    article_server: str,
) -> None:
    """
    BREAKS: Letting `fetch_url`'s exception (or a None download) propagate would
    force every caller to duplicate its own try/except instead of getting one clean
    None to fall back from.
    """
    result = extract_article(f"{article_server}/does-not-exist")
    assert result is None


def test_extract_article_returns_none_for_an_unroutable_host(no_network: None) -> None:
    """A real network failure (nothing listens on the no_network proxy port) also
    comes back as None, not a raised exception."""
    result = extract_article("https://example.invalid/whatever")
    assert result is None
